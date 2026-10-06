#!/usr/bin/env python3
"""Merge the shared deployment fixture with the CI-only cluster-vars overrides.

Nested dictionaries are merged so changes to shared resource and database
defaults reach CI unless its overlay explicitly overrides them.
"""

import argparse
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE = Path("tooling/deploy/fixtures/cluster-vars.values.json")
OVERLAY = Path(".github/ci_resources/flux/cluster-vars.values.json")


def merge_values(base, overrides):
    values = copy.deepcopy(base)
    for key, value in overrides.items():
        if key == "_comment":
            continue
        if key not in base:
            raise ValueError(f"unknown CI cluster-vars override: {key}")
        if isinstance(value, dict):
            if not isinstance(base[key], dict):
                raise ValueError(f"CI cluster-vars override is not a scalar: {key}")
            values[key] = merge_values(base[key], value)
        elif isinstance(base[key], dict):
            raise ValueError(f"CI cluster-vars override must be an object: {key}")
        else:
            values[key] = value
    values.pop("_comment", None)
    return values


def load_values(root=ROOT, *, extractor_data_dir=None):
    base = json.loads((root / BASE).read_text())
    overlay = json.loads((root / OVERLAY).read_text())
    values = merge_values(base, overlay)
    if extractor_data_dir is not None:
        values["extractor_data_dir"] = str(extractor_data_dir)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractor-data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = load_values(extractor_data_dir=args.extractor_data_dir)
    args.output.write_text(json.dumps(values, indent=2) + "\n")


if __name__ == "__main__":
    main()
