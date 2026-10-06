#!/usr/bin/env python3
"""Select one producer-attempt receipt and read its JSON ZIP member."""

import argparse
import json
from pathlib import Path
import re
import zipfile


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
        if any(re.fullmatch(r"scout-config-ref-[0-9]+", a["name"]) for a in artifacts):
            raise ValueError(
                f"missing {name}: a receipt exists for another producer attempt; "
                "rerun all jobs to publish and verify the current attempt"
            )
        return None
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name} artifact, found {len(matches)}")
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ValueError("receipt artifact is expired or lacks expiry metadata")
    if type(artifact.get("id")) is not int or artifact["id"] <= 0:
        raise ValueError("receipt artifact has an invalid ID")
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
    # Read the known member directly; never extract archive paths to disk.
    with zipfile.ZipFile(archive_path) as archive:
        try:
            return archive.read("scout-config-ref.json")
        except KeyError as error:
            raise ValueError("receipt archive lacks scout-config-ref.json") from error


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
