#!/usr/bin/env python3
"""Generate the keycloak-config-cli realm from the single source of truth.

The Ansible template `ansible/roles/keycloak/templates/scout-realm.json.j2` is the
one definition of the Scout realm. The Ansible lane renders it with Jinja; the GitOps
lane needs the same realm as a helm-templated config-cli chart file. This tool
translates the Jinja template into `helm/keycloak-config-cli/files/scout-realm.json`
so the two lanes never carry independent copies that drift.

Translation:
  - Jinja {% if/for %}      -> helm {{- if/range }} (conditionals gate on eq "true"
    because cluster-vars arrive as quoted strings; the alb host loop splits a
    comma-string and trims/drops blanks).
  - site config (smtp/provider/terms/lifespans/xnat/idps/hosts/attrs) -> chart .Values.

Client secrets, client ids and hostnames used to be translated here too. They are
now written as `$(env:...)` in the template itself and pass through untouched, so
both lanes hand config-cli the same tokens and resolve them from the same Job env.
What is left in VARMAP is what the two lanes genuinely source differently.

Usage:
  render_realm.py            # write the chart file
  render_realm.py --check    # exit 1 if the committed chart file is stale
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "ansible/roles/keycloak/templates/scout-realm.json.j2"
OUT = ROOT / "helm/keycloak-config-cli/files/scout-realm.json"

HEADER = (
    "{{/* GENERATED from ansible/roles/keycloak/templates/scout-realm.json.j2 by\n"
    "     tooling/keycloak/render_realm.py -- do not edit by hand; edit the template\n"
    "     and re-run the tool. The helm {{ }} / $(env:) tokens are intentional. */}}\n"
)

VARMAP = {
    "keycloak_realm_name | capitalize": "Scout",
    "keycloak_realm_name": "scout",
    "keycloak_token_lifespan": "28800",
    "keycloak_access_token_lifespan": "300",
    "keycloak_temporal_token_lifespan": "28800",
    "keycloak_minio_token_lifespan": "28800",
    "keycloak_trino_svc_token_lifespan": "14400",
    "keycloak_terms_localizations | to_json": "{{ .Values.termsLocalizations | default dict | toJson }}",
    "keycloak_default_provider": "{{ .Values.defaultProvider }}",
    "keycloak_smtp_debug": "{{ .Values.smtp.debug }}",
    "keycloak_smtp_reply_to_display_name": "{{ .Values.smtp.replyToDisplayName }}",
    "keycloak_smtp_starttls": "{{ .Values.smtp.starttls }}",
    "keycloak_smtp_auth": "{{ .Values.smtp.auth }}",
    "keycloak_smtp_port": "{{ .Values.smtp.port }}",
    "keycloak_smtp_host": "{{ .Values.smtp.host }}",
    "keycloak_smtp_reply_to": "{{ .Values.smtp.replyTo }}",
    "keycloak_smtp_from_display_name": "{{ .Values.smtp.fromDisplayName }}",
    "keycloak_smtp_from": "{{ .Values.smtp.from }}",
    "keycloak_smtp_envelope_from": "{{ .Values.smtp.envelopeFrom }}",
    "keycloak_smtp_ssl": "{{ .Values.smtp.ssl }}",
}

GUARD = (
    "    {{- $idps := list }}\n"
    '    {{- if eq (.Values.github.enabled | toString) "true" }}{{- $idps = append $idps "github" }}{{- end }}\n'
    '    {{- if eq (.Values.microsoft.enabled | toString) "true" }}{{- $idps = append $idps "microsoft" }}{{- end }}\n'
    '    {{- if not (has .Values.defaultProvider $idps) }}{{- fail (printf "keycloak realm: defaultProvider %q is not an enabled IdP (github.enabled=%v microsoft.enabled=%v)" .Values.defaultProvider .Values.github.enabled .Values.microsoft.enabled) }}{{- end }}\n'
)


def _sub(s, old, new):
    """str.replace that fails closed: a silent no-op here would emit a corrupt realm."""
    if old not in s:
        raise SystemExit(
            "render_realm.py: post-processing anchor not found (template changed?): %r"
            % (old[:70],)
        )
    return s.replace(old, new)


def render(s):
    # 1) strip Jinja comments
    s = re.sub(r"^[ \t]*\{#.*?#\}[ \t]*\r?\n", "", s, flags=re.S | re.M)  # whole-line
    s = re.sub(r"\{#.*?#\}", "", s, flags=re.S)  # inline remnant
    # 2) control blocks -> helm (string-typed cluster-vars: gate on eq "true")
    s = s.replace(
        "{% if enable_xnat | default(false) | bool %}",
        '{{- if eq (.Values.enableXnat | toString) "true" }}',
    )
    s = s.replace(
        "{% if keycloak_gh_client_id is defined and keycloak_gh_client_id %}",
        '{{- if eq (.Values.github.enabled | toString) "true" }}',
    )
    s = s.replace(
        "{% if keycloak_microsoft_client_id is defined and keycloak_microsoft_client_id %}",
        '{{- if eq (.Values.microsoft.enabled | toString) "true" }}',
    )
    s = s.replace(
        "{% for h in keycloak_alb_oidc_hosts %}",
        '{{- range $h := (splitList "," .Values.albOidcHosts | compact) }}',
    )
    s = s.replace(
        "{% for attr_name, attr_config in trino_attribute_filters.items() %}",
        "{{- range $attr_name, $attr_config := .Values.trinoAttributeFilters }}",
    )
    s = s.replace(
        "{% if attr_config.options is defined %}",
        '{{- if hasKey $attr_config "options" }}',
    )
    s = s.replace("{% else %}", "{{- else }}")
    s = s.replace("{% endif %}", "{{- end }}")
    s = s.replace("{% endfor %}", "{{- end }}")
    # 3) inline-expression specials (trino loop)
    s = s.replace(
        "{{ 'multiselect' if attr_config.options is defined else 'text' }}",
        '{{- if hasKey $attr_config "options" }}multiselect{{- else }}text{{- end }}',
    )
    # The em dashes here are U+2014, verbatim from the ansible source; keep the two
    # lanes in sync (do not "fix" to a hyphen or the helm-lane default silently diverges).
    s = s.replace(
        "{{ attr_config.display_name | default('Scout AuthZ — ' ~ attr_name) }}",
        '{{ $attr_config.display_name | default (printf "Scout AuthZ — %s" $attr_name) }}',
    )
    s = s.replace(
        "{{ attr_config.max_length | default(64) }}",
        "{{ $attr_config.max_length | default 64 }}",
    )
    s = s.replace(
        "{{ attr_config.options | to_json }}", "{{ $attr_config.options | toJson }}"
    )
    s = s.replace(
        "{{ attr_config.value_pattern | default('^([A-Za-z0-9 _-]+|\\\\\\\\*)$') }}",
        '{{ $attr_config.value_pattern | default "^([A-Za-z0-9 _-]+|\\\\\\\\*)$" }}',
    )
    s = s.replace(
        "{{ attr_config.value_pattern_error | default('Use a code (letters, digits, spaces, hyphen, underscore) or the * wildcard for all values.') }}",
        '{{ $attr_config.value_pattern_error | default "Use a code (letters, digits, spaces, hyphen, underscore) or the * wildcard for all values." }}',
    )
    s = s.replace("{{ attr_name }}", "{{ $attr_name }}")
    s = s.replace("{{ h }}", "{{ $h }}")
    # 4) plain {{ var }} substitutions (longest key first)
    for key in sorted(VARMAP, key=len, reverse=True):
        s = re.sub(
            r"\{\{\s*" + re.escape(key) + r"\s*\}\}", lambda m, v=VARMAP[key]: v, s
        )
    # 5) fail closed on anything the tool did not translate: a non-helm {{ }} token
    #    (helm tokens start with -, ., or $) or a surviving jinja {% %} control block.
    leftover = re.findall(r"\{\{(?!\s*[-.$])[^}]*\}\}", s)
    if leftover:
        raise SystemExit("unmapped ansible {{ }} remain: %s" % sorted(set(leftover)))
    control = re.findall(r"\{%.*?%\}", s, flags=re.S)
    if control:
        raise SystemExit(
            "unhandled jinja control blocks remain: %s" % sorted(set(control))
        )
    # 6) strip static trailing commas (invalid JSON)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    # 6b) trim each ALB host and drop blanks
    s = _sub(
        s,
        '(splitList "," .Values.albOidcHosts | compact) }}',
        '(splitList "," .Values.albOidcHosts) }}{{- $h = trim $h }}{{- if $h }}',
    )
    s = _sub(
        s,
        '"https://{{ $h }}/oauth2/idpresponse"{{- end }}',
        '"https://{{ $h }}/oauth2/idpresponse"{{- end }}{{- end }}',
    )
    s = _sub(
        s, '"https://{{ $h }}"{{- end }}', '"https://{{ $h }}"{{- end }}{{- end }}'
    )
    # 6c) fail the render if defaultProvider is not an enabled IdP
    s = _sub(s, '    "authenticatorConfig": [', GUARD + '    "authenticatorConfig": [')
    return HEADER + s


def main():
    out = render(SRC.read_text())
    if "--check" in sys.argv:
        cur = OUT.read_text() if OUT.exists() else ""
        if cur != out:
            print(
                "ERROR: %s is stale. Run: python3 tooling/keycloak/render_realm.py"
                % OUT.relative_to(ROOT),
                file=sys.stderr,
            )
            sys.exit(1)
        print("%s is up to date." % OUT.relative_to(ROOT))
        return
    OUT.write_text(out)
    print("wrote %s" % OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
