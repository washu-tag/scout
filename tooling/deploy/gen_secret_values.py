#!/usr/bin/env python3
"""Generate the ``scout-secret-values`` Secret for an on-prem site (ADR 0031 section 3).

The on-prem ``secrets-ready`` Kustomization substitutes this one Secret into every
fixed-name Secret under ``deploy/base/secrets-on-prem``, so it must carry each key in
``deploy/required-secret-values.txt`` with a value that survives Flux substitution
unchanged. Reads the site's plaintext values (a flat JSON object of strings) and its
cluster-vars values (the ``gen_cluster_vars.py --values`` file, for the feature flags
and namespaces), validates them, and emits the Secret ready to encrypt with SOPS or to
hand to another backend.

Fail closed, and never echo a value: a problem names the key and the rule only.
Every value must be a string with no ', no line break, control or format character,
and no leading or trailing whitespace. The templates single-quote each value; Flux
strips LF and folds CR to a space; CNPG and MinIO trim what the apps read untrimmed.
Values a consumer pastes into a larger string get a narrower character set:

- the keycloak-client-secrets values: A-Z a-z 0-9 . _ ~ + / = - (config-cli
  substitutes them into the realm JSON before parsing it);
- valkey_password, superset_postgres_password: A-Z a-z 0-9 . _ ~ - (the superset
  chart builds connection URLs from them; valkey's also sits in a JSON file);
- the config.env inputs: no " $ ` or \\ (double-quoted, and sourced by sh).

Plus MinIO's minimum lengths, oauth2-proxy's cookie-secret sizes, and the realm
flags: a conditional key is required while its flag is on and rejected while it is
off. Absent or empty optional and disabled-conditional keys are left out, so the
templates' defaults apply. Dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from gen_cluster_vars import load_required, yaml_double_quoted

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_REQUIRED = _REPO / "deploy" / "required-secret-values.txt"

# Required (non-empty) only when this cluster-var flag is on.
CONDITIONAL = {
    "keycloak_xnat_client_secret": "keycloak_enable_xnat",
    "keycloak_gh_client_id": "keycloak_github_enabled",
    "keycloak_gh_client_secret": "keycloak_github_enabled",
    "keycloak_microsoft_client_id": "keycloak_microsoft_enabled",
    "keycloak_microsoft_client_secret": "keycloak_microsoft_enabled",
    "keycloak_microsoft_tenant_id": "keycloak_microsoft_enabled",
}
# The templates default them (MinIO root user minio, OpenID on).
OPTIONAL = {"s3_username", "minio_oidc_enabled"}
BOOLEANS = {"minio_oidc_enabled": ("on", "off", "true", "false")}

# keycloak-client-secrets: config-cli substitutes these into the realm JSON.
REALM_VALUES = {
    "keycloak_oauth2_proxy_client_secret",
    "keycloak_superset_client_secret",
    "keycloak_superset_svc_client_secret",
    "keycloak_jupyterhub_client_secret",
    "keycloak_grafana_client_secret",
    "keycloak_temporal_client_secret",
    "keycloak_launchpad_client_secret",
    "keycloak_minio_client_secret",
    "keycloak_open_webui_client_secret",
    "keycloak_voila_svc_client_secret",
    "keycloak_report_viewer_svc_client_secret",
    "keycloak_fragment_reconciler_svc_client_secret",
    *CONDITIONAL,
}
# The superset chart pastes these into redis:// and postgresql:// URLs.
URL_VALUES = {"valkey_password", "superset_postgres_password"}
# minio-scout-env-configuration config.env, double-quoted and sourced by sh.
CONFIG_ENV_VALUES = {
    "s3_username",
    "s3_password",
    "keycloak_minio_client_secret",
    "minio_oidc_enabled",
}

# MinIO rejects shorter root and user credentials.
MIN_LENGTH = {
    "s3_username": 3,
    "s3_password": 8,
    "s3_lake_reader_secret": 8,
    "s3_lake_writer_secret": 8,
    "s3_loki_writer_secret": 8,
    "s3_opa_bundle_reader_secret": 8,
    "s3_opa_bundle_writer_secret": 8,
}

# ansible/inventory.example.yaml placeholders, never real credentials (its generated
# values are $(openssl ...) commands, caught by prefix).
EXAMPLE_PLACEHOLDERS = {
    "your-github-client",
    "your-github-secret",
    "your-microsoft-client",
    "your-microsoft-secret",
    "your-microsoft-tenant-id",
}
# Ansible role defaults a site may still run: keep them at adoption, rotate after.
WEAK_DEFAULTS = {"changeme", "changeme-nextauth-secret", "trinokeystorepass"}

# Ansible inventory vars that could rename a DB role or database the base fixes; a
# renamed one must be renamed back in Postgres before cutover.
FIXED_NAMES = {
    "hive_postgres_user": "hive",
    "hive_readonly_postgres_user": "hive_readonly",
    "keycloak_postgres_user": "keycloak",
    "superset_postgres_user": "superset",
    "superset_database": "superset",
}

# Controls (LF, CR, tab, DEL, ...), invisible format characters (zero-width space,
# BOM), surrogates, unassigned code points (U+FFFF) and line/paragraph separators.
_BAD_CATEGORIES = {"Cc", "Cf", "Cs", "Cn", "Zl", "Zp"}


def flag_on(value) -> bool:
    """A cluster-var flag as the realm reads it: YAML 1.1 truthy after substitution."""
    return str(value).strip().lower() in ("true", "yes", "on", "y")


def cookie_secret_ok(value: str) -> bool:
    """oauth2-proxy's rule: an AES key of 16/24/32 bytes, base64url-decoded when that
    gives a valid length, else the raw bytes."""
    if len(value.encode()) in (16, 24, 32):
        return True
    s = value.rstrip("=")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        return False
    try:
        decoded = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        return False
    return len(decoded) in (16, 24, 32)


def value_problems(key: str, value: str) -> list:
    out = []
    if "'" in value:
        out.append("contains ' (the templates single-quote every value)")
    if any(unicodedata.category(c) in _BAD_CATEGORIES for c in value):
        out.append("contains a line break, control or format character")
    if value != value.strip():
        out.append("has leading or trailing whitespace")
    if key in REALM_VALUES and not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
        out.append(
            "may use only A-Z a-z 0-9 . _ ~ + / = - (it goes into the realm JSON)"
        )
    if key in URL_VALUES and not re.fullmatch(r"[A-Za-z0-9._~-]+", value):
        out.append("may use only A-Z a-z 0-9 . _ ~ - (it goes into a connection URL)")
    if key in CONFIG_ENV_VALUES and any(c in value for c in '"$`\\'):
        out.append(
            'contains " $ ` or \\ (config.env is double-quoted and sourced by sh)'
        )
    if len(value) < MIN_LENGTH.get(key, 0):
        out.append("is shorter than {} characters".format(MIN_LENGTH[key]))
    if key == "oauth2_proxy_cookie_secret" and not cookie_secret_ok(value):
        out.append("must be 16, 24 or 32 bytes, raw or base64url-encoded")
    if key in BOOLEANS and value not in BOOLEANS[key]:
        out.append("must be one of {}".format(", ".join(BOOLEANS[key])))
    if value in EXAMPLE_PLACEHOLDERS or value.startswith("$("):
        out.append("is an inventory.example.yaml placeholder")
    return out


def validate(values: dict, cluster_vars: dict, contract: list) -> tuple:
    """(problems, warnings) for a site's secret values. Messages never include a value."""
    problems, warnings = [], []
    for key in sorted(set(values) - set(contract)):
        hint = (
            " (the base fixes this name; rename it before cutover)"
            if key in FIXED_NAMES
            else ""
        )
        problems.append("{}: not in required-secret-values.txt{}".format(key, hint))
    for key in contract:
        value = values.get(key)
        flag = CONDITIONAL.get(key)
        on = flag is not None and flag_on(cluster_vars.get(flag, ""))
        if value is None or value == "":
            if flag is None and key not in OPTIONAL:
                problems.append("{}: missing or empty".format(key))
            elif on:
                problems.append("{}: missing or empty while {} is on".format(key, flag))
            continue
        if flag is not None and not on:
            problems.append(
                "{}: set while {} is off; turn the flag on or drop the "
                "value".format(key, flag)
            )
            continue
        if not isinstance(value, str):
            problems.append("{}: must be a JSON string".format(key))
            continue
        problems.extend("{}: {}".format(key, p) for p in value_problems(key, value))
        if value in WEAK_DEFAULTS:
            warnings.append("{}: is a weak Ansible default; rotate it".format(key))
    for var, name in FIXED_NAMES.items():
        if var in cluster_vars and str(cluster_vars[var]) != name:
            problems.append(
                "{}: the base fixes this name to {}; rename it before "
                "cutover".format(var, name)
            )
    hive_ns = cluster_vars.get("hive_namespace")
    if hive_ns is not None and hive_ns == cluster_vars.get(
        "postgres_cluster_namespace"
    ):
        problems.append(
            "hive_namespace must differ from postgres_cluster_namespace: each gets "
            "its own superuser-secret"
        )
    return problems, warnings


def build(values: dict, contract: list) -> dict:
    """The Secret data: every contract key with a non-empty value."""
    return {key: values[key] for key in contract if values.get(key)}


def render_secret(data: dict, name: str, namespace: str) -> str:
    """The values Secret. The label re-renders secrets-ready on any edit
    (kustomize-controller >= v1.7.0); the annotation stops a site Kustomization with
    postBuild from expanding ${...} inside the values."""
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "metadata:",
        "  name: {}".format(name),
        "  namespace: {}".format(namespace),
        "  labels:",
        "    reconcile.fluxcd.io/watch: Enabled",
        "  annotations:",
        "    kustomize.toolkit.fluxcd.io/substitute: disabled",
        "type: Opaque",
        "stringData:",
    ]
    for key in sorted(data):
        lines.append("  {}: {}".format(key, yaml_double_quoted(data[key])))
    return "\n".join(lines) + "\n"


def load_json(path) -> dict:
    """A JSON object read as UTF-8, rejecting duplicate keys rather than keeping the last."""

    def no_duplicates(pairs):
        keys = [k for k, _ in pairs]
        dups = sorted({k for k in keys if keys.count(k) > 1})
        if dups:
            raise ValueError("duplicate keys: {}".format(", ".join(dups)))
        return dict(pairs)

    data = json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=no_duplicates
    )
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--values", required=True, help="site secret values (JSON)")
    ap.add_argument(
        "--cluster-vars-values",
        required=True,
        help="the site's gen_cluster_vars.py --values file (JSON)",
    )
    ap.add_argument(
        "--required-secret-values",
        default=str(DEFAULT_REQUIRED),
        help="path to deploy/required-secret-values.txt (default: repo copy)",
    )
    ap.add_argument(
        "--name", default="scout-secret-values", help="Secret metadata.name"
    )
    ap.add_argument(
        "--namespace", default="flux-system", help="Secret metadata.namespace"
    )
    ap.add_argument("-o", "--output", default="", help="output path (default: stdout)")
    args = ap.parse_args(argv)

    try:
        values = load_json(args.values)
        cluster_vars = load_json(args.cluster_vars_values)
    except (OSError, ValueError) as exc:
        sys.stderr.write("cannot read the values: {}\n".format(exc))
        raise SystemExit(1)
    contract = load_required(args.required_secret_values)

    problems, warnings = validate(values, cluster_vars, contract)
    for w in warnings:
        sys.stderr.write("warning: {}\n".format(w))
    if problems:
        sys.stderr.write("secret values failed validation:\n")
        for p in problems:
            sys.stderr.write("  {}\n".format(p))
        raise SystemExit(1)

    text = render_secret(build(values, contract), args.name, args.namespace)
    if args.output:
        # Plaintext credentials: owner-only, also when the file already exists.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
