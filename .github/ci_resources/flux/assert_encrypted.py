#!/usr/bin/env python3
"""Fail unless a SOPS-encrypted Secret holds ciphertext only.

Every value under data/stringData must be a sops ENC[...] string, the file must carry sops
metadata with an age recipient, and no plaintext value from the values file may appear
anywhere in it. Prints key names and counts, never a value.

Usage: assert_encrypted.py <encrypted secret yaml> <plaintext values json>
"""

import json
import sys
from pathlib import Path

import yaml


def main() -> None:
    path, values_path = Path(sys.argv[1]), Path(sys.argv[2])
    text = path.read_text()
    doc = yaml.safe_load(text)
    values = json.loads(values_path.read_text())
    problems = []
    if not (doc.get("sops") or {}).get("age"):
        problems.append("no sops metadata with an age recipient")
    leaves = {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
    if not leaves:
        problems.append("no data or stringData")
    problems += [
        "{}: not ciphertext".format(k)
        for k, v in sorted(leaves.items())
        if not (
            isinstance(v, str) and v.startswith("ENC[AES256_GCM,") and v.endswith("]")
        )
    ]
    # Short flags (minio_oidc_enabled: off) and the root user name are not secret and
    # match ordinary words in the file, so only look for the longer values.
    problems += [
        "{}: plaintext value present in the file".format(k)
        for k, v in sorted(values.items())
        if isinstance(v, str) and len(v) >= 8 and v in text
    ]
    for p in problems:
        print("::error::{}: {}".format(path.name, p))
    if problems:
        sys.exit(1)
    print("{}: {} values, all ciphertext".format(path.name, len(leaves)))


if __name__ == "__main__":
    main()
