"""Only trusted, published, complete runs can emit release-eligible proof."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/deploy-flux.yaml").read_text())


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
