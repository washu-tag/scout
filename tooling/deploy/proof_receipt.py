#!/usr/bin/env python3
"""Bind the published Flux ingest/auth proof to a producer and consumer attempt.

The release gate must independently validate GitHub's workflow/attempt/job API
metadata. This receipt identifies evidence; it does not authenticate itself or
claim that the haul bundle was restored in a disconnected cluster.
"""

import argparse
import json
from pathlib import Path
import sys

from artifact_identity import IdentityError, REVISION, load_receipt, validate_receipt

REPOSITORY = "washu-tag/scout"
FIELDS = {
    "schemaVersion",
    "producer",
    "consumer",
    "artifactMode",
    "valuesMode",
    "profile",
    "legs",
}
CONSUMER_FIELDS = {"repository", "runId", "runAttempt", "revision"}
REQUIRED_JOBS = (
    "Resolve exact published config",
    "deploy-and-test-flux (ingest)",
    "deploy-and-test-flux (auth)",
)


def validate_proof(proof, *, producer, repository, run_id, run_attempt, revision):
    """Require a complete schema2 producer and a separately expected consumer."""
    if not isinstance(producer, dict):
        raise IdentityError("proof requires a validated producer receipt")
    validate_receipt(
        producer,
        repository=REPOSITORY,
        revision=producer.get("revision"),
        run_id=producer.get("runId"),
        run_attempt=producer.get("runAttempt"),
        require_bundle=True,
    )
    if not isinstance(proof, dict) or set(proof) != FIELDS:
        raise IdentityError("proof must contain exactly the schema fields")
    if type(proof["schemaVersion"]) is not int or proof["schemaVersion"] != 1:
        raise IdentityError("unsupported proof schemaVersion")
    # Validate the nested object as well as comparing it: Python otherwise
    # considers True equal to 1, including when nested inside dictionaries.
    validate_receipt(
        proof["producer"],
        repository=producer["repository"],
        revision=producer["revision"],
        run_id=producer["runId"],
        run_attempt=producer["runAttempt"],
        require_bundle=True,
    )
    if proof["producer"] != producer:
        raise IdentityError("proof producer does not match the selected receipt")
    consumer = proof["consumer"]
    if not isinstance(consumer, dict) or set(consumer) != CONSUMER_FIELDS:
        raise IdentityError("proof consumer must contain exactly the context fields")
    if repository != REPOSITORY or consumer["repository"] != REPOSITORY:
        raise IdentityError("proof must belong to the production repository")
    if not isinstance(consumer["revision"], str) or not REVISION.fullmatch(
        consumer["revision"]
    ):
        raise IdentityError("invalid consumer revision")
    for field in ("runId", "runAttempt"):
        if type(consumer[field]) is not int or consumer[field] <= 0:
            raise IdentityError("invalid consumer " + field)
    expected = {
        "repository": repository,
        "revision": revision,
        "runId": run_id,
        "runAttempt": run_attempt,
    }
    for field, value in expected.items():
        if type(value) is not type(consumer[field]) or consumer[field] != value:
            raise IdentityError("proof consumer context mismatch: " + field)
    for field, value in (
        ("artifactMode", "published"),
        ("valuesMode", "sops"),
        ("profile", "onprem-core-ingest-auth"),
        ("legs", ["ingest", "auth"]),
    ):
        if proof[field] != value:
            raise IdentityError("invalid proof " + field)
    return proof


def validate_jobs(pages, *, run_id, revision):
    """Check jobs fetched from this consumer's exact attempt API endpoint."""
    if not isinstance(pages, list) or not pages:
        raise IdentityError("job listing must contain API pages")
    jobs = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("jobs"), list):
            raise IdentityError("invalid job API page")
        jobs.extend(page["jobs"])
    if any(not isinstance(job, dict) for job in jobs):
        raise IdentityError("invalid job API entry")
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1:
            raise IdentityError("current attempt requires exactly one job: " + name)
        job = matches[0]
        if (
            type(job.get("run_id")) is not int
            or job["run_id"] != run_id
            or job.get("head_sha") != revision
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise IdentityError("current attempt job did not succeed: " + name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    validate = commands.add_parser("validate")
    for command in (create, validate):
        command.add_argument("--producer", type=Path, required=True)
        command.add_argument("--repository", required=True)
        command.add_argument("--run-id", type=int, required=True)
        command.add_argument("--run-attempt", type=int, required=True)
        command.add_argument("--revision", required=True)
    create.add_argument("--jobs", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    validate.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        producer = load_receipt(args.producer)
        if args.command == "create":
            validate_jobs(
                json.loads(args.jobs.read_text()),
                run_id=args.run_id,
                revision=args.revision,
            )
            proof = {
                "schemaVersion": 1,
                "producer": producer,
                "consumer": {
                    "repository": args.repository,
                    "runId": args.run_id,
                    "runAttempt": args.run_attempt,
                    "revision": args.revision,
                },
                "artifactMode": "published",
                "valuesMode": "sops",
                "profile": "onprem-core-ingest-auth",
                "legs": ["ingest", "auth"],
            }
        else:
            proof = load_receipt(args.proof)
        validate_proof(
            proof,
            producer=producer,
            repository=args.repository,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            revision=args.revision,
        )
        if args.command == "create":
            args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    except (IdentityError, OSError, ValueError) as exc:
        message = (
            str(exc) if isinstance(exc, IdentityError) else "cannot read proof input"
        )
        print("release proof: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
