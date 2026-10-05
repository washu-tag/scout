#!/usr/bin/env python3
"""Select one producer-attempt receipt and read only its bounded JSON ZIP member."""

import argparse
import json
from pathlib import Path
import stat
import zipfile

MAX_ARCHIVE_BYTES = 128 * 1024
MAX_RECEIPT_BYTES = 16 * 1024


def select_artifact(pages, *, run_id, run_attempt, revision):
    if not isinstance(pages, list) or not pages:
        raise ValueError("artifact listing must contain at least one API page")
    artifacts = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("artifacts"), list):
            raise ValueError("malformed artifact API page")
        artifacts.extend(page["artifacts"])
    name = f"scout-config-ref-{run_attempt}"
    if any(
        not isinstance(artifact, dict) or not isinstance(artifact.get("name"), str)
        for artifact in artifacts
    ):
        raise ValueError("malformed artifact entry")
    matches = [artifact for artifact in artifacts if artifact.get("name") == name]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} artifact, found {len(matches)}")
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ValueError("receipt artifact is expired or lacks expiry metadata")
    if type(artifact.get("id")) is not int or artifact["id"] <= 0:
        raise ValueError("receipt artifact has an invalid ID")
    size = artifact.get("size_in_bytes")
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES:
        raise ValueError(
            "receipt archive exceeds its size limit or has invalid size metadata"
        )
    provenance = artifact.get("workflow_run", {})
    if (
        not isinstance(provenance, dict)
        or provenance.get("id") != run_id
        or provenance.get("head_sha") != revision
    ):
        raise ValueError(
            "receipt archive does not belong to the triggering producer run/revision"
        )
    return artifact["id"]


def read_receipt(archive_path):
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("receipt archive exceeds its size limit")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != "scout-config-ref.json":
            raise ValueError("receipt archive must contain only scout-config-ref.json")
        member = members[0]
        mode = member.external_attr >> 16
        if member.is_dir() or stat.S_ISLNK(mode):
            raise ValueError("receipt archive member must be a regular file")
        if member.file_size > MAX_RECEIPT_BYTES:
            raise ValueError("receipt JSON exceeds its size limit")
        with archive.open(member) as stream:
            data = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES:
            raise ValueError("receipt JSON exceeds its size limit")
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--listing", type=Path, required=True)
    select.add_argument("--run-id", type=int, required=True)
    select.add_argument("--run-attempt", type=int, required=True)
    select.add_argument("--revision", required=True)
    select.add_argument("--github-output", type=Path, required=True)
    extract = commands.add_parser("extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "select":
            artifact_id = select_artifact(
                json.loads(args.listing.read_text()),
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                revision=args.revision,
            )
            with args.github_output.open("a") as output:
                output.write(
                    f"present={'true' if artifact_id is not None else 'false'}\n"
                )
                if artifact_id is not None:
                    output.write(f"artifact_id={artifact_id}\n")
            if artifact_id is None:
                print(
                    "Producer attempt has no config receipt; no published config to test."
                )
        else:
            args.output.write_bytes(read_receipt(args.archive))
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as error:
        parser.exit(1, f"receipt archive error: {error}\n")


if __name__ == "__main__":
    main()
