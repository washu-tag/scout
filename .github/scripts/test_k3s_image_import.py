"""Ansible reruns must not replace a scheduled build with a published image."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = yaml.safe_load(
    (ROOT / ".github/actions/k3s-image-import-or-pull/action.yaml").read_text()
)


@pytest.mark.parametrize("required", ["true", "", "unexpected"])
def test_missing_or_unknown_build_cannot_pull_published_image(tmp_path, required):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text('#!/bin/sh\necho called >> "$RUNNER_TEMP/commands"\n')
    sudo.chmod(0o755)
    step = ACTION["runs"]["steps"][-1]
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "VERSION": "latest",
            "RAW_VERSION": "latest",
            "IMAGE_NAME": "hl7-transformer",
            "REGISTRY": "ghcr.io",
            "NAMESPACE": "washu-tag",
            "ARTIFACT_REQUIRED": required,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "commands").exists()
    if required == "true":
        assert "rerun all jobs" in result.stdout


def test_required_download_failure_stops_the_job():
    download = next(
        step
        for step in ACTION["runs"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert not download.get("continue-on-error", False)
    assert "github.run_attempt" in download["with"]["name"]
    assert ACTION["inputs"]["artifact-required"]["default"] == "true"


def test_every_import_gets_the_image_build_decision():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())
    callers = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses") == "./.github/actions/k3s-image-import-or-pull"
    ]
    assert callers
    for step in callers:
        name = step["with"]["image-name"]
        assert step["with"]["artifact-required"] == (
            "${{ needs.changes.outputs['" + name + "'] }}"
        )
