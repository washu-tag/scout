"""Keep publication privilege and recovery serialization around the shared gate."""

from pathlib import Path
import subprocess
import sys

import yaml
import pytest

from promote import CHARTS

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_release_redispatch_uses_stamped_policy_and_serialized_chart_writes():
    release = workflow("release.yaml")
    assert not (ROOT / ".github/workflows/promote-release.yaml").exists()
    assert (
        "needs.validate.outputs.resume != 'true'"
        in release["jobs"]["version-bump"]["if"]
    )
    normal = release["jobs"]["release"]
    assert release["concurrency"]["group"] != normal["concurrency"]["group"]
    assert normal["concurrency"]["group"] not in str(workflow("ci.yaml")["concurrency"])
    assert "concurrency" not in release["jobs"]["wait-for-build"]
    assert {"validate", "wait-for-build"} <= set(normal["needs"])
    assert "needs.wait-for-build.result == 'success'" in normal["if"]
    checkout = next(
        s for s in normal["steps"] if "actions/checkout@" in s.get("uses", "")
    )
    assert checkout["with"]["ref"] == "${{ needs.wait-for-build.outputs.commit_sha }}"
    wait_checkout = next(
        s
        for s in release["jobs"]["wait-for-build"]["steps"]
        if "actions/checkout@" in s.get("uses", "")
    )
    assert "needs.validate.outputs.resume_revision" in wait_checkout["with"]["ref"]
    steps = normal["steps"]
    package_index = next(
        i for i, s in enumerate(steps) if "helm push" in s.get("run", "")
    )
    promote_index = next(
        i for i, s in enumerate(steps) if "promote.py promote" in s.get("run", "")
    )
    assert package_index < promote_index
    assert 'promote.py charts --version "$VERSION"' in steps[package_index]["run"]
    assert "--resume" in steps[package_index]["run"]


def test_compatibility_chart_cli_uses_ci_catalog_from_any_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tooling/release/promote.py"), "charts"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    rows = [line.split() for line in result.stdout.splitlines()]
    expected = [
        [chart["chart-name"], chart["chart-dir"]]
        for chart in workflow("ci.yaml")["jobs"]["publish-charts"]["strategy"][
            "matrix"
        ]["include"]
    ]
    assert rows == expected
    assert dict(rows) == CHARTS
    for name, directory in rows:
        assert (
            yaml.safe_load((ROOT / directory / "Chart.yaml").read_text())["name"]
            == name
        )


def test_wait_is_read_only_and_main_promotion_keeps_exact_attempt_inputs():
    release = workflow("release.yaml")
    wait = release["jobs"]["wait-for-build"]
    assert release["permissions"] == {"contents": "read", "actions": "read"}
    assert "COSIGN_PRIVATE_KEY" not in str(wait)
    assert "create-github-app-token" not in str(wait)
    assert "--timeout 10800" in str(wait)
    assert wait["timeout-minutes"] == 185
    mutator = release["jobs"]["release"]
    assert mutator["permissions"] == {
        "contents": "read",
        "actions": "read",
        "packages": "write",
    }
    call = next(s for s in mutator["steps"] if "promote.py promote" in s.get("run", ""))
    assert call["if"] == "github.ref_name == 'main'"
    for arg in ("producer-run-id", "producer-run-attempt", "boundary-sha"):
        assert "--" + arg in call["run"]
    assert call["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert call["env"]["RELEASE_GH_TOKEN"] == "${{ steps.app_token.outputs.token }}"


def test_branch_releases_retain_exact_candidate_copy_and_attempt_downloads():
    release = workflow("release.yaml")
    steps = release["jobs"]["release"]["steps"]
    copy = next(s for s in steps if "--release-branch" in s.get("run", ""))
    assert copy["if"] == "github.ref_name != 'main'"
    assert "copy-flux-artifacts.py publish-images" in copy["run"]
    assert "GITHUB_RUN_ATTEMPT" in copy["run"]
    downloads = [s for s in steps if "actions/download-artifact@" in s.get("uses", "")]
    assert len(downloads) == 2
    for download in downloads:
        assert download["if"] == "github.ref_name != 'main'"
        assert "needs.wait-for-build.outputs.ci_run_attempt" in download["with"]["name"]
        assert (
            download["with"]["run-id"]
            == "${{ needs.wait-for-build.outputs.ci_run_id }}"
        )
    publish = next(s for s in steps if "gh release create" in s.get("run", ""))
    assert publish["if"] == "github.ref_name != 'main'"
    assert steps.index(copy) < steps.index(publish)
    assert "force=false" in publish["run"]


@pytest.mark.parametrize(
    "current,allowed", [("1.2.3", True), ("2.0.0", False), ("dev", True)]
)
def test_recovered_release_reset_cannot_change_later_stamp(tmp_path, current, allowed):
    import os

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.com")
    (tmp_path / "VERSION").write_text("1.2.3\n")
    script = tmp_path / ".github/scripts/update-versions.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\nprintf 'dev\\n' > VERSION\n")
    git("add", ".")
    git("commit", "-qm", "Update to version 1.2.3")
    stamped = git("rev-parse", "HEAD")
    if current != "1.2.3":
        (tmp_path / "VERSION").write_text(current + "\n")
        git("add", "VERSION")
        git(
            "commit",
            "-qm",
            "Update to version 2.0.0" if current != "dev" else "Reset to dev versions",
        )
    step = next(
        s
        for s in workflow("release.yaml")["jobs"]["reset-dev"]["steps"]
        if s.get("name") == "Reset to dev versions"
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "STAMPED_SHA": stamped},
        capture_output=True,
    )
    assert (result.returncode == 0) is allowed
