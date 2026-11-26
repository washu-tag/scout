#!/usr/bin/env python3
"""Renders the realm and checks the xnat client's roles and scope mappings.

The openid plugin's role sync reads each mapped xnat client role from the ID token
and from access tokens, so the realm must define it and put it in scope for the
xnat client and the xnat-audience scope. Scout's own mappings must name roles the
realm has without any extras. With XNAT off, the realm must hold nothing XNAT.
"""
import json
import subprocess
import sys

import yaml

CHART = "helm/keycloak-config-cli"
ANSIBLE_DEFAULTS = "ansible/roles/scout_common/defaults/main.yaml"
BASE_ROLES = ["xnat-access", "xnat-admin"]


def render(*helm_args):
    out = subprocess.run(
        ["helm", "template", "t", CHART, *helm_args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc.get("kind") == "ConfigMap":
            for value in (doc.get("data") or {}).values():
                if value.lstrip().startswith("{"):
                    return json.loads(value)
    sys.exit("no realm ConfigMap rendered")


def xnat_view(realm):
    groups = {g["name"]: g.get("clientRoles", {}).get("xnat") for g in realm["groups"]}
    scopes = {
        m.get("client") or "scope:" + m["clientScope"]: m["roles"]
        for m in realm.get("clientScopeMappings", {}).get("xnat", [])
    }
    return {
        "client": any(c.get("clientId") == "xnat" for c in realm["clients"]),
        "roles": [r["name"] for r in realm["roles"]["client"].get("xnat", [])],
        "scout-admin": groups.get("scout-admin"),
        "scout-user": groups.get("scout-user"),
        "scopes": scopes,
        "audience scope": any(
            s["name"] == "xnat-audience" for s in realm.get("clientScopes", [])
        ),
    }


def with_xnat(roles):
    return {
        "client": True,
        "roles": roles,
        "scout-admin": BASE_ROLES,
        "scout-user": ["xnat-access"],
        "scopes": {"xnat": roles, "scope:xnat-audience": roles},
        "audience scope": True,
    }


CASES = [
    (
        "XNAT off",
        ["--set", "enableXnat=false"],
        {
            "client": False,
            "roles": [],
            "scout-admin": None,
            "scout-user": None,
            "scopes": {},
            "audience scope": False,
        },
    ),
    ("XNAT on, no extra roles", ["--set", "enableXnat=true"], with_xnat(BASE_ROLES)),
    (
        "a site's extra roles, each once",
        [
            "--set",
            "enableXnat=true",
            "--set-json",
            'xnatRoles=["xnat-admin", "xnat-privileged", "xnat-privileged", "xnat-access"]',
        ],
        with_xnat(BASE_ROLES + ["xnat-privileged"]),
    ),
    (
        "XNAT off ignores xnatRoles, even one that isn't a list",
        ["--set", "enableXnat=false", "--set-string", "xnatRoles=xnat-privileged"],
        {
            "client": False,
            "roles": [],
            "scout-admin": None,
            "scout-user": None,
            "scopes": {},
            "audience scope": False,
        },
    ),
]

failed = False
for name, args, want in CASES:
    got = xnat_view(render(*args))
    if got == want:
        print(f"  ok   {name}")
    else:
        failed = True
        print(f"::error::{name}: want {want}, got {got}")

rejected = subprocess.run(
    [
        "helm",
        "template",
        "t",
        CHART,
        "--set",
        "enableXnat=true",
        "--set-string",
        "xnatRoles=xnat-privileged",
    ],
    capture_output=True,
    text=True,
)
if rejected.returncode != 0 and "xnatRoles must be a list" in rejected.stderr:
    print("  ok   XNAT on rejects an xnatRoles that isn't a list")
else:
    failed = True
    print(
        "::error::XNAT on accepted an xnatRoles that isn't a list, or failed for another reason"
    )

defaults = yaml.safe_load(open(ANSIBLE_DEFAULTS))["xnat_openid_role_mappings_default"]
missing = [m["keycloak_role"] for m in defaults if m["keycloak_role"] not in BASE_ROLES]
if missing:
    failed = True
    print(
        f"::error file={ANSIBLE_DEFAULTS}::Scout's role sync mappings name {missing}, "
        "which the realm defines only as a site's extra roles"
    )
else:
    print("  ok   Scout's own mappings name roles the realm always has")

sys.exit(1 if failed else 0)
