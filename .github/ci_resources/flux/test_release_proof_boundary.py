"""Only trusted, published, complete runs can emit release-eligible proof."""

from pathlib import Path

import pytest
import yaml

from test_consumer_boundary import allows

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/deploy-flux.yaml").read_text())


def context():
    return {
        "github": {
            "event_name": "workflow_run",
            "repository": "washu-tag/scout",
            "event": {
                "workflow_run": {
                    "event": "push",
                    "head_branch": "main",
                    "conclusion": "success",
                    "head_repository": {"full_name": "washu-tag/scout"},
                }
            },
        },
        "needs": {
            "identity": {
                "result": "success",
                "outputs": {
                    "present": "true",
                    "bundle_digest": "sha256:" + "a" * 64,
                },
            },
            "deploy": {"result": "success"},
        },
    }


@pytest.mark.parametrize(
    "path,value",
    [
        ("github.event_name", "workflow_dispatch"),
        ("github.event_name", "pull_request"),
        ("github.event_name", "push"),
        ("github.repository", "attacker/scout"),
        ("github.event.workflow_run.event", "pull_request"),
        ("github.event.workflow_run.head_branch", "topic"),
        ("github.event.workflow_run.conclusion", "failure"),
        ("github.event.workflow_run.head_repository.full_name", "attacker/scout"),
        ("needs.identity.result", "failure"),
        ("needs.identity.result", "skipped"),
        ("needs.identity.outputs.present", "false"),
        ("needs.identity.outputs.bundle_digest", ""),
        ("needs.identity.outputs.bundle_digest", None),
        ("needs.deploy.result", "skipped"),
        ("needs.deploy.result", "failure"),
        ("needs.deploy.result", "cancelled"),
        ("cancelled", True),
    ],
)
def test_ineligible_events_and_incomplete_jobs_cannot_emit_proof(path, value):
    gate = WORKFLOW["jobs"]["release-proof"]["if"]
    event = context()
    assert allows(gate, event)
    cursor = event
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = value
    assert not allows(gate, event)


def test_proof_job_is_read_only_and_binds_exact_attempt_jobs():
    proof = WORKFLOW["jobs"]["release-proof"]
    assert proof["name"] == "Record published artifact proof"
    assert set(proof["needs"]) == {"identity", "deploy"}
    assert proof["permissions"] == {"contents": "read", "actions": "read"}
    assert proof["steps"][0]["with"] == {
        "ref": "${{ github.sha }}",
        "persist-credentials": False,
    }
    script = proof["steps"][1]["run"]
    assert "/attempts/${GITHUB_RUN_ATTEMPT}/jobs?per_page=100" in script
    assert '--revision "$TESTED_SHA"' in script
    assert '--revision "$GITHUB_SHA"' in script
    assert '--bundle-digest "$RECEIPT_BUNDLE_DIGEST"' in script
    assert WORKFLOW["env"]["VALUES_MODE"] == "${{ inputs.values || 'sops' }}"
    upload = proof["steps"][-1]["with"]
    assert upload["retention-days"] == 90
    assert upload["if-no-files-found"] == "error"
    assert proof["env"]["PROOF_NAME"] == (
        "scout-release-proof-${{ github.event.workflow_run.id }}-"
        "${{ github.event.workflow_run.run_attempt }}-${{ github.run_attempt }}"
    )
    assert upload["path"] == "${{ runner.temp }}/${{ env.PROOF_NAME }}.json"


def test_bundle_digest_reaches_both_config_verification_and_proof():
    jobs = WORKFLOW["jobs"]
    assert (
        jobs["identity"]["outputs"]["bundle_digest"]
        == "${{ steps.validate.outputs.bundle_digest }}"
    )
    for name in ("deploy", "release-proof"):
        assert (
            jobs[name]["env"]["RECEIPT_BUNDLE_DIGEST"]
            == "${{ needs.identity.outputs.bundle_digest }}"
        )
    verify = next(
        step
        for step in jobs["deploy"]["steps"]
        if step.get("name", "").startswith("Verify the exact")
    )
    assert 'export BUNDLE_DIGEST="$RECEIPT_BUNDLE_DIGEST"' in verify["run"]
