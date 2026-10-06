"""Release proof is a strict binding of separate producer/consumer attempts."""

from copy import deepcopy
import json

import pytest

from artifact_identity import IdentityError, load_receipt
from proof_receipt import REQUIRED_JOBS, main, validate_jobs, validate_proof

CONTEXT = {
    "repository": "washu-tag/scout",
    "run_id": 901,
    "run_attempt": 2,
    "revision": "c" * 40,
}


@pytest.fixture
def producer():
    return {
        "schemaVersion": 2,
        "repository": "washu-tag/scout",
        "revision": "a" * 40,
        "runId": 801,
        "runAttempt": 3,
        "version": "0.20261006.54",
        "manifestDigest": "sha256:" + "a" * 64,
        "bundleDigest": "sha256:" + "b" * 64,
        "configDigest": "sha256:" + "c" * 64,
    }


@pytest.fixture
def proof(producer):
    return {
        "schemaVersion": 1,
        "producer": deepcopy(producer),
        "consumer": {
            "repository": CONTEXT["repository"],
            "runId": CONTEXT["run_id"],
            "runAttempt": CONTEXT["run_attempt"],
            "revision": CONTEXT["revision"],
        },
        "artifactMode": "published",
        "valuesMode": "sops",
        "profile": "onprem-core-ingest-auth",
        "legs": ["ingest", "auth"],
    }


@pytest.fixture
def jobs():
    return [
        {
            "jobs": [
                {
                    "name": name,
                    "run_id": CONTEXT["run_id"],
                    "head_sha": CONTEXT["revision"],
                    "status": "completed",
                    "conclusion": "success",
                }
                for name in REQUIRED_JOBS
            ]
        }
    ]


def args():
    return [
        "--repository",
        CONTEXT["repository"],
        "--run-id",
        str(CONTEXT["run_id"]),
        "--run-attempt",
        str(CONTEXT["run_attempt"]),
        "--revision",
        CONTEXT["revision"],
    ]


def test_create_and_validate_separate_workflow_and_producer_revisions(
    producer, proof, jobs, tmp_path
):
    producer_path, jobs_path, output = [
        tmp_path / name for name in ("producer", "jobs", "proof")
    ]
    producer_path.write_text(json.dumps(producer))
    jobs_path.write_text(json.dumps(jobs))
    assert (
        main(
            [
                "create",
                *args(),
                "--producer",
                str(producer_path),
                "--jobs",
                str(jobs_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert load_receipt(output) == proof
    assert (
        main(
            [
                "validate",
                *args(),
                "--producer",
                str(producer_path),
                "--proof",
                str(output),
            ]
        )
        == 0
    )
    assert validate_proof(proof, producer=producer, **CONTEXT) == proof


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", True),
        ("schemaVersion", 2),
        ("artifactMode", "local"),
        ("valuesMode", "plain"),
        ("profile", "ingest"),
        ("legs", ["ingest"]),
        ("legs", ["auth"]),
        ("legs", ["ingest", "auth", "ingest"]),
        ("legs", {"ingest": "success", "auth": "success"}),
    ],
)
def test_ineligible_proof_rejected(producer, proof, field, value):
    proof[field] = value
    with pytest.raises(IdentityError):
        validate_proof(proof, producer=producer, **CONTEXT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("runAttempt", 2),
        ("runId", 802),
        ("revision", "b" * 40),
        ("configDigest", "sha256:" + "d" * 64),
        ("manifestDigest", "sha256:" + "d" * 64),
        ("bundleDigest", "sha256:" + "d" * 64),
        ("runAttempt", True),
        ("repository", "attacker/scout"),
    ],
)
def test_other_producer_or_digest_cannot_use_green_proof(producer, proof, field, value):
    proof["producer"][field] = value
    with pytest.raises(IdentityError):
        validate_proof(proof, producer=producer, **CONTEXT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("runAttempt", 1),
        ("runId", 902),
        ("revision", "a" * 40),
        ("runAttempt", True),
        ("runId", "901"),
        ("revision", "c" * 40 + "\nevil=yes"),
        ("repository", "attacker/scout"),
    ],
)
def test_other_consumer_attempt_cannot_reuse_proof(producer, proof, field, value):
    proof["consumer"][field] = value
    with pytest.raises(IdentityError):
        validate_proof(proof, producer=producer, **CONTEXT)


def test_schema1_producer_cannot_qualify(producer, proof):
    producer["schemaVersion"] = 1
    producer.pop("bundleDigest")
    proof["producer"] = producer
    with pytest.raises(IdentityError, match="release requires"):
        validate_proof(proof, producer=producer, **CONTEXT)


@pytest.mark.parametrize("where", [None, "producer", "consumer"])
def test_extra_locations_and_missing_fields_rejected(producer, proof, where):
    selected = proof if where is None else proof[where]
    selected["url"] = "https://attacker.example/receipt"
    with pytest.raises(IdentityError):
        validate_proof(proof, producer=producer, **CONTEXT)
    selected.pop("url")
    selected.pop(next(iter(selected)))
    with pytest.raises(IdentityError):
        validate_proof(proof, producer=producer, **CONTEXT)


@pytest.mark.parametrize("missing", REQUIRED_JOBS)
def test_previous_attempt_success_cannot_fill_a_missing_current_job(jobs, missing):
    jobs[0]["jobs"] = [job for job in jobs[0]["jobs"] if job["name"] != missing]
    with pytest.raises(IdentityError, match="current attempt requires"):
        validate_jobs(jobs, run_id=CONTEXT["run_id"], revision=CONTEXT["revision"])


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": "skipped"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"conclusion": "neutral"},
        {"status": "in_progress"},
        {"run_id": 900},
        {"run_id": True},
        {"head_sha": "b" * 40},
    ],
)
def test_green_aggregate_cannot_hide_bad_leg_metadata(jobs, change):
    jobs[0]["jobs"][-1].update(change)
    with pytest.raises(IdentityError, match="job did not succeed"):
        validate_jobs(jobs, run_id=CONTEXT["run_id"], revision=CONTEXT["revision"])


def test_job_pagination_and_duplicate_detection(jobs):
    split = [{"jobs": jobs[0]["jobs"][:1]}, {"jobs": jobs[0]["jobs"][1:]}]
    validate_jobs(split, run_id=CONTEXT["run_id"], revision=CONTEXT["revision"])
    split.append({"jobs": jobs[0]["jobs"][:1]})
    with pytest.raises(IdentityError, match="exactly one"):
        validate_jobs(split, run_id=CONTEXT["run_id"], revision=CONTEXT["revision"])


@pytest.mark.parametrize(
    "pages", [[], {}, [{"message": "Forbidden"}], [{"jobs": [None]}]]
)
def test_api_errors_are_not_empty_success(pages):
    with pytest.raises(IdentityError):
        validate_jobs(pages, run_id=CONTEXT["run_id"], revision=CONTEXT["revision"])


@pytest.mark.parametrize(
    "replacement", [b'{"schemaVersion":1,"schemaVersion":1}', b"[]", b"\xff"]
)
def test_malformed_proof_rejected_without_echoing_payload(
    producer, replacement, tmp_path, capsys
):
    path, proof_path = tmp_path / "producer", tmp_path / "proof"
    path.write_text(json.dumps(producer))
    proof_path.write_bytes(replacement)
    assert (
        main(["validate", *args(), "--producer", str(path), "--proof", str(proof_path)])
        == 1
    )
    assert 'schemaVersion":' not in capsys.readouterr().err


def test_failed_leg_never_writes_a_proof(producer, jobs, tmp_path):
    path, jobs_path, output = [
        tmp_path / name for name in ("producer", "jobs", "proof")
    ]
    path.write_text(json.dumps(producer))
    jobs[0]["jobs"][-1]["conclusion"] = "failure"
    jobs_path.write_text(json.dumps(jobs))
    assert (
        main(
            [
                "create",
                *args(),
                "--producer",
                str(path),
                "--jobs",
                str(jobs_path),
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()
