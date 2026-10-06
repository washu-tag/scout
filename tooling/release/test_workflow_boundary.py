"""Keep publication privilege and recovery serialization around the shared gate."""

from pathlib import Path
import re

import yaml

from promote import CHARTS

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_promotion_and_compatibility_writes_share_one_recovery_lock():
    release = workflow("release.yaml")
    recovery = workflow("promote-release.yaml")
    normal = release["jobs"]["release"]
    retry = recovery["jobs"]["promote"]
    assert normal["concurrency"] == retry["concurrency"]
    lock = normal["concurrency"]["group"]
    assert release["concurrency"]["group"] != lock
    assert lock not in str(workflow("ci.yaml")["concurrency"])
    assert "concurrency" not in release["jobs"]["wait-for-build"]
    assert {"validate", "wait-for-build"} <= set(normal["needs"])
    assert "needs.wait-for-build.result == 'success'" in normal["if"]
    # Release-only charts must be packaged under this same lock, from exact source.
    checkout = [
        s for s in normal["steps"] if s.get("with", {}).get("path") == "release-source"
    ]
    assert len(checkout) == 1
    assert (
        checkout[0]["with"]["ref"] == "${{ needs.wait-for-build.outputs.commit_sha }}"
    )
    chart_steps = [
        (i, s) for i, s in enumerate(normal["steps"]) if "helm push" in s.get("run", "")
    ]
    assert len(chart_steps) == 1
    chart_index, chart_step = chart_steps[0]
    charts = re.findall(r"^([a-z0-9-]+) helm/", chart_step["run"], re.MULTILINE)
    assert sorted(charts) == sorted(CHARTS)
    promoter = [
        i
        for i, s in enumerate(normal["steps"])
        if "promote.py promote" in s.get("run", "")
    ]
    assert len(promoter) == 1 and chart_index < promoter[0]
    assert "helm push" not in str(retry)
    assert "update-versions.sh" not in str(retry)


def test_wait_is_read_only_and_both_mutators_require_trusted_main():
    release = workflow("release.yaml")
    recovery = workflow("promote-release.yaml")
    wait = release["jobs"]["wait-for-build"]
    assert (
        release["permissions"]
        == recovery["permissions"]
        == {"contents": "read", "actions": "read"}
    )
    assert "COSIGN_PRIVATE_KEY" not in str(wait)
    assert "create-github-app-token" not in str(wait)
    assert "promote.py wait" in str(wait)
    for spec, job_name, guard_name in (
        (release, "release", "validate"),
        (recovery, "promote", "promote"),
    ):
        guard = str(spec["jobs"][guard_name])
        assert "refs/heads/main" in guard and "${{ github.ref }}" in guard
        mutator = spec["jobs"][job_name]
        assert mutator["permissions"] == {
            "contents": "read",
            "actions": "read",
            "packages": "write",
        }
        calls = [
            step
            for step in mutator["steps"]
            if "promote.py promote" in step.get("run", "")
        ]
        assert len(calls) == 1
        call = calls[0]
        for arg in (
            "producer-run-id",
            "producer-run-attempt",
            "consumer-run-id",
            "consumer-run-attempt",
            "boundary-sha",
        ):
            assert "--" + arg in call["run"]
        assert call["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert call["env"]["RELEASE_GH_TOKEN"] == "${{ steps.app_token.outputs.token }}"
