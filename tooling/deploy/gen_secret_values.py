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
On top of that:

- valkey_password: printable ASCII without " or \\ (it is embedded in a JSON file);
- MinIO: s3_password and each s3_*_secret at least 8 characters, s3_username 3;
- oauth2_proxy_cookie_secret: 16, 24 or 32 bytes, raw or base64url-decoded.

Absent or empty optional and disabled-conditional keys are left out, so the
templates' defaults apply. Dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
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
# The template defaults it (MINIO_ROOT_USER minio).
OPTIONAL = {"s3_username"}

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

# ansible/inventory.example.yaml placeholders, never real IdP credentials.
EXAMPLE_PLACEHOLDERS = {
    "your-github-client",
    "your-github-secret",
    "your-microsoft-client",
    "your-microsoft-secret",
    "your-microsoft-tenant-id",
}
# Ansible role defaults a site may still run: keep them at adoption, rotate after.
WEAK_DEFAULTS = {"changeme", "changeme-nextauth-secret", "trinokeystorepass"}

# Ansible could rename these DB roles; the base fixes the names, so a renamed role
# must be renamed back in Postgres before cutover.
FIXED_ROLE_NAMES = {
    "hive_postgres_user": "hive",
    "hive_readonly_postgres_user": "hive_readonly",
    "keycloak_postgres_user": "keycloak",
    "superset_postgres_user": "superset",
}

# Line and paragraph separators, controls (LF, CR, tab, DEL, ...) and invisible
# format characters (zero-width space, BOM).
_BAD_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}


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
    if key == "valkey_password" and (
        not re.fullmatch(r"[ -~]*", value) or '"' in value or "\\" in value
    ):
        out.append('must be printable ASCII without " or \\ (embedded in JSON)')
    if len(value) < MIN_LENGTH.get(key, 0):
        out.append("is shorter than {} characters".format(MIN_LENGTH[key]))
    if key == "oauth2_proxy_cookie_secret" and not cookie_secret_ok(value):
        out.append("must be 16, 24 or 32 bytes, raw or base64url-encoded")
    if value in EXAMPLE_PLACEHOLDERS:
        out.append("is the inventory.example.yaml placeholder")
    return out


def validate(values: dict, cluster_vars: dict, contract: list) -> tuple:
    """(problems, warnings) for a site's secret values. Messages never include a value."""
    problems, warnings = [], []
    for key in sorted(set(values) - set(contract)):
        hint = (
            " (the base fixes this role name; rename the role before cutover)"
            if key in FIXED_ROLE_NAMES
            else ""
        )
        problems.append("{}: not in required-secret-values.txt{}".format(key, hint))
    for key in contract:
        value = values.get(key)
        if value is None or value == "":
            flag = CONDITIONAL.get(key)
            if flag is None and key not in OPTIONAL:
                problems.append("{}: missing or empty".format(key))
            elif flag and flag_on(cluster_vars.get(flag, "")):
                problems.append("{}: missing or empty while {} is on".format(key, flag))
            continue
        if not isinstance(value, str):
            problems.append("{}: must be a JSON string".format(key))
            continue
        problems.extend("{}: {}".format(key, p) for p in value_problems(key, value))
        if value in WEAK_DEFAULTS:
            warnings.append("{}: is a weak Ansible default; rotate it".format(key))
    for var, name in FIXED_ROLE_NAMES.items():
        if var in cluster_vars and str(cluster_vars[var]) != name:
            problems.append(
                "{}: the base fixes this role name to {}; rename the role "
                "before cutover".format(var, name)
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

    values = json.loads(Path(args.values).read_text())
    cluster_vars = json.loads(Path(args.cluster_vars_values).read_text())
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
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
