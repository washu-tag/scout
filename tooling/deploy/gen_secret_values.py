#!/usr/bin/env python3
"""Generate the ``scout-secret-values`` Secret for an on-prem site (ADR 0031 section 3).

The on-prem ``secrets-ready`` Kustomization substitutes this one Secret into every
fixed-name Secret under ``deploy/base/secrets-on-prem``. Reads the site's plaintext
values (a flat JSON object of strings) and its cluster-vars values (the
``gen_cluster_vars.py --values`` file, for the realm flags, namespaces and the MinIO
settings config.env reads), checks
them against ``deploy/required-secret-values.txt`` and the value rules listed in
``deploy/required-secrets.md``, and emits the Secret ready to encrypt with SOPS or to
hand to another backend. Fails closed and never echoes a value: a problem names the
key and the rule only. Dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
from pathlib import Path

from gen_cluster_vars import yaml_double_quoted

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_REQUIRED = _REPO / "deploy" / "required-secret-values.txt"

# One set for every value, safe in the single-quoted templates, the realm JSON and the
# sourced config.env. The superset chart pastes URL_VALUES into redis:// and
# postgresql:// URLs, so those also leave out + / =.
SAFE_CHARS = re.compile(r"[A-Za-z0-9._~+/=-]+")
URL_CHARS = re.compile(r"[A-Za-z0-9._~-]+")
URL_VALUES = {"valkey_password", "superset_postgres_password"}

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


def load_contract(path) -> dict:
    """{name: rule} from required-secret-values.txt; rule is "required", "optional"
    or "when=<cluster-var flag>"."""
    contract = {}
    for ln in Path(path).read_text().splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        name, *rules = ln.split()
        rule = " ".join(rules) or "required"
        if rule not in ("required", "optional") and not re.fullmatch(
            r"when=[A-Za-z_][A-Za-z0-9_]*", rule
        ):
            raise ValueError("{}: unknown rule {}".format(name, rule))
        contract[name] = rule
    return contract


def flag_on(value) -> bool:
    """A cluster-var flag as the realm reads it: YAML 1.1 truthy after substitution."""
    return str(value).strip().lower() in ("true", "yes", "on", "y")


def min_length(key: str) -> int:
    """MinIO's minimums: 3 for the root user, 8 for its password and user secrets."""
    if key == "s3_username":
        return 3
    return 8 if key.startswith("s3_") else 0


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
    if value in EXAMPLE_PLACEHOLDERS or value.startswith("$("):
        return ["is an inventory.example.yaml placeholder"]
    out = []
    if key in URL_VALUES:
        if not URL_CHARS.fullmatch(value):
            out.append(
                "may use only A-Z a-z 0-9 . _ ~ - (it goes into a connection URL)"
            )
    elif not SAFE_CHARS.fullmatch(value):
        out.append("may use only A-Z a-z 0-9 . _ ~ + / = -")
    if len(value) < min_length(key):
        out.append("is shorter than {} characters".format(min_length(key)))
    if key == "oauth2_proxy_cookie_secret" and not cookie_secret_ok(value):
        out.append("must be 16, 24 or 32 bytes, raw or base64url-encoded")
    return out


def validate(values: dict, cluster_vars: dict, contract: dict) -> tuple:
    """(problems, warnings) for a site's secret values. Messages never include a value."""
    problems, warnings = [], []
    for key in sorted(set(values) - set(contract)):
        hint = (
            " (the base fixes this name; rename it before cutover)"
            if key in FIXED_NAMES
            else ""
        )
        problems.append("{}: not in required-secret-values.txt{}".format(key, hint))
    for key, rule in contract.items():
        value = values.get(key)
        flag = rule[len("when=") :] if rule.startswith("when=") else None
        on = flag is not None and flag_on(cluster_vars.get(flag, ""))
        if value is None or value == "":
            if rule == "required":
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
    # config.env reads these two cluster-vars too; the template defaults both.
    if cluster_vars.get("s3_username"):
        problems.extend(
            "s3_username: {}".format(p)
            for p in value_problems("s3_username", str(cluster_vars["s3_username"]))
        )
    oidc = cluster_vars.get("minio_oidc_enabled")
    if oidc not in (None, "") and str(oidc) not in ("on", "off", "true", "false"):
        problems.append("minio_oidc_enabled: must be on, off, true or false")
    return problems, warnings


def build(values: dict, contract: dict) -> dict:
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
    ap.add_argument(
        "-o",
        "--output",
        required=True,
        help="file to write; created owner-only (plaintext credentials never go to stdout)",
    )
    args = ap.parse_args(argv)

    try:
        values = load_json(args.values)
        cluster_vars = load_json(args.cluster_vars_values)
        contract = load_contract(args.required_secret_values)
    except (OSError, ValueError) as exc:
        sys.stderr.write("cannot read the inputs: {}\n".format(exc))
        raise SystemExit(1)

    problems, warnings = validate(values, cluster_vars, contract)
    for w in warnings:
        sys.stderr.write("warning: {}\n".format(w))
    if problems:
        sys.stderr.write("secret values failed validation:\n")
        for p in problems:
            sys.stderr.write("  {}\n".format(p))
        raise SystemExit(1)

    text = render_secret(build(values, contract), args.name, args.namespace)
    # Plaintext credentials: owner-only, also when the file already exists.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
