#!/usr/bin/env python3
"""Render the example pluggable app and run its fragment through the reconciler.

Nothing deploys examples/, so without this a fragment schema change could break
the example with every other check still green.

Usage (with keycloak-fragment-reconciler installed):
  check-example-fragment.py [chart-dir]
"""

import subprocess
import sys

import yaml

from scout_keycloak_fragment_reconciler.fragment import (
    FragmentError,
    check_site_rules,
    parse,
)
from scout_keycloak_fragment_reconciler.settings import FRAGMENT_LABEL, Settings

DOMAIN = "scout.example.edu"

chart = sys.argv[1] if len(sys.argv) > 1 else "examples/on-prem-pluggable-app"
rendered = subprocess.run(
    ["helm", "template", "example", chart, "--set", f"domain={DOMAIN}"],
    check=True,
    capture_output=True,
    text=True,
).stdout

fragments = [
    doc
    for doc in yaml.safe_load_all(rendered)
    if doc
    and doc.get("kind") == "ConfigMap"
    and doc["metadata"].get("labels", {}).get(FRAGMENT_LABEL) == "true"
]
if not fragments:
    sys.exit(f"{chart} renders no ConfigMap labelled {FRAGMENT_LABEL}=true")

tiers = Settings.model_fields["tier_roles"].default
try:
    for cm in fragments:
        for key, text in cm["data"].items():
            for client in parse(text).clients:
                check_site_rules(client, hostname=DOMAIN, tiers=tiers)
            print(f"ok: {cm['metadata']['name']}/{key}")
except FragmentError as exc:
    sys.exit(f"{cm['metadata']['name']}/{key}: {exc}")
