"""Every release-versioned CI image must derive the stamped version."""

import os
import shutil
import subprocess
from pathlib import Path

import yaml

from producer_plan import IMAGE_PATHS, VENDOR_IMAGES, WORKFLOW, workflow_catalog

ROOT = Path(__file__).resolve().parents[2]
STAMP = "9.9.9"


def run(args, label=None, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, (
        f"{label or args[:3]} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def copy_worktree(dest: Path) -> None:
    listed = run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).stdout
    for name in filter(None, listed.split("\0")):
        source = ROOT / name
        if source.is_file():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def test_update_versions_stamps_every_release_image(tmp_path):
    repo = tmp_path / "repo"
    copy_worktree(repo)
    run(["bash", ".github/scripts/update-versions.sh", STAMP], cwd=repo)

    action = yaml.safe_load(
        (ROOT / ".github/actions/derive-version/action.yaml").read_text()
    )
    detect = next(s for s in action["runs"]["steps"] if s["id"] == "detect-version")
    jobs, _ = workflow_catalog(WORKFLOW)
    subprojects = {
        item["image-name"]: item["subproject"]
        for item in jobs["build-and-upload"]["strategy"]["matrix"]["include"]
    }

    unstamped = {}
    for image in IMAGE_PATHS:
        if image in VENDOR_IMAGES:
            continue
        output = tmp_path / f"output-{image}"
        run(
            ["bash", "-eo", "pipefail", "-c", detect["run"]],
            label=f"derive-version for {image}",
            cwd=repo,
            env={
                **os.environ,
                "SUBPROJECT": subprojects[image],
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF_NAME": "main",
            },
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        if values["version"] != STAMP:
            unstamped[image] = values["version"]

    assert not unstamped, (
        "CI catalog images that update-versions.sh does not stamp; the release "
        f"promotion will find no <version> tag for them: {unstamped}"
    )
