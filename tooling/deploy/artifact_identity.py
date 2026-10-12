#!/usr/bin/env python3
"""Create and check the exact CI build output identity.

This is build identity metadata, not another component manifest (ADR 0033).
Registry locations remain fixed in the workflows. Release and CI verification supply trusted
repository/revision/run context, then verifies the raw OCI manifest fetched by
the recorded digest before using the config artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


FIELDS = {
    "schemaVersion",
    "repository",
    "revision",
    "runId",
    "runAttempt",
    "version",
    "manifestDigest",
    "configDigest",
}
REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}"
)
REVISION = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
VERSION = re.compile(r"0\.([0-9]{8})\.[1-9][0-9]*")


class IdentityError(ValueError):
    """The handoff does not identify the expected build."""


def _json_object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdentityError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise IdentityError("JSON must contain an object")
    return value


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def load_receipt(path):
    """Read the receipt's JSON object; callers validate its identity fields."""
    return _json_object(Path(path).read_bytes())


def validate_receipt(
    receipt, *, repository, revision, run_id, run_attempt, require_bundle=False
):
    """Validate all fields, then bind the receipt to caller-supplied context."""
    if not isinstance(receipt, dict):
        raise IdentityError("receipt must contain exactly the schema fields")
    schema = receipt.get("schemaVersion")
    fields = (
        FIELDS | {"bundleDigest"} if type(schema) is int and schema == 2 else FIELDS
    )
    if set(receipt) != fields:
        raise IdentityError("receipt must contain exactly the schema fields")
    if type(schema) is not int or schema not in (1, 2):
        raise IdentityError("unsupported schemaVersion")
    if require_bundle and schema != 2:
        raise IdentityError("release requires schemaVersion 2 with bundleDigest")
    if not _matches(REPOSITORY, receipt["repository"]) or receipt["repository"].split(
        "/"
    )[1] in (".", ".."):
        raise IdentityError("invalid repository")
    if not _matches(REVISION, receipt["revision"]):
        raise IdentityError("invalid revision")
    for field in ("runId", "runAttempt"):
        if type(receipt[field]) is not int or receipt[field] <= 0:
            raise IdentityError("invalid " + field)
    if not _matches(VERSION, receipt["version"]):
        raise IdentityError("invalid version")
    try:
        datetime.strptime(receipt["version"].split(".")[1], "%Y%m%d")
    except ValueError as exc:
        raise IdentityError("invalid version date") from exc
    digests = ("manifestDigest", "configDigest")
    if schema == 2:
        digests += ("bundleDigest",)
    for field in digests:
        if not _matches(DIGEST, receipt[field]):
            raise IdentityError("invalid " + field)
    expected = {
        "repository": repository,
        "revision": revision,
        "runId": run_id,
        "runAttempt": run_attempt,
    }
    for field, value in expected.items():
        if type(value) is not type(receipt[field]) or receipt[field] != value:
            raise IdentityError("receipt context mismatch: " + field)
    return receipt


def validate_manifest(raw, receipt):
    """Bind exact OCI manifest bytes and annotations to the validated receipt."""
    if "sha256:" + hashlib.sha256(raw).hexdigest() != receipt["configDigest"]:
        raise IdentityError("OCI manifest digest does not match configDigest")
    manifest = _json_object(raw)
    annotations = manifest.get("annotations")
    if not isinstance(annotations, dict):
        raise IdentityError("OCI manifest annotations are missing")
    expected = {
        "org.opencontainers.image.source": "https://github.com/"
        + receipt["repository"],
        "org.opencontainers.image.revision": receipt["revision"],
        "org.opencontainers.image.version": receipt["version"],
        "io.scout.build.run-id": str(receipt["runId"]),
        "io.scout.build.run-attempt": str(receipt["runAttempt"]),
        "io.scout.build.manifest-digest": receipt["manifestDigest"],
    }
    for key, value in expected.items():
        if annotations.get(key) != value:
            raise IdentityError("OCI annotation mismatch: " + key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="write a producer receipt")
    validate = commands.add_parser("validate", help="validate build outputs")
    for command in (create, validate):
        command.add_argument("--repository", required=True)
        command.add_argument("--revision", required=True)
        command.add_argument("--run-id", required=True, type=int)
        command.add_argument("--run-attempt", required=True, type=int)
    create.add_argument("--version", required=True)
    create.add_argument("--manifest-digest", required=True)
    create.add_argument("--bundle-digest")
    create.add_argument("--config-digest", required=True)
    create.add_argument("--output", required=True, type=Path)
    validate.add_argument("--receipt", required=True, type=Path)
    validate.add_argument("--manifest", type=Path, help="exact raw OCI manifest bytes")
    validate.add_argument("--github-output", type=Path)
    validate.add_argument("--require-bundle", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            receipt = {
                "schemaVersion": 2 if args.bundle_digest is not None else 1,
                "repository": args.repository,
                "revision": args.revision,
                "runId": args.run_id,
                "runAttempt": args.run_attempt,
                "version": args.version,
                "manifestDigest": args.manifest_digest,
                "configDigest": args.config_digest,
            }
            if args.bundle_digest is not None:
                receipt["bundleDigest"] = args.bundle_digest
        else:
            receipt = load_receipt(args.receipt)
        validate_receipt(
            receipt,
            repository=args.repository,
            revision=args.revision,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            require_bundle=args.command == "validate" and args.require_bundle,
        )
        if args.command == "create":
            args.output.write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )
        else:
            if args.manifest is not None:
                validate_manifest(args.manifest.read_bytes(), receipt)
            # All validation completes before exporting any values. These
            # regex-constrained scalars cannot inject GitHub output commands.
            if args.github_output is not None:
                with args.github_output.open("a", encoding="utf-8") as output:
                    for name, field in (
                        ("version", "version"),
                        ("config_digest", "configDigest"),
                        ("manifest_digest", "manifestDigest"),
                    ):
                        output.write(name + "=" + receipt[field] + "\n")
                    if receipt["schemaVersion"] == 2:
                        output.write("bundle_digest=" + receipt["bundleDigest"] + "\n")
    except (IdentityError, OSError) as exc:
        # Never echo receipt contents into the runner's command-aware log.
        message = (
            str(exc)
            if isinstance(exc, IdentityError)
            else "cannot read or write identity file"
        )
        print("artifact identity: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
