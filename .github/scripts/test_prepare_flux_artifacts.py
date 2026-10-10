"""Candidate composition refuses missing builds and preserves frozen carry refs."""

import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "prepare_flux_artifacts", Path(__file__).with_name("prepare-flux-artifacts.py")
)
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)
DIGEST = "sha256:" + "a" * 64


def docker_archive(path, tags):
    content = json.dumps([{"RepoTags": tags}]).encode()
    with tarfile.open(path, "w") as bundle:
        member = tarfile.TarInfo("manifest.json")
        member.size = len(content)
        bundle.addfile(member, io.BytesIO(content))
    return path


def test_archive_selects_original_legacy_tag(tmp_path):
    ref = "ghcr.io/washu-tag/launchpad:1.2.3"
    archive = docker_archive(tmp_path / "launchpad.tar", [ref])
    assert candidate.archive_reference(archive, "launchpad") == ref


@pytest.mark.parametrize(
    "tags", [[], ["ghcr.io/washu-tag/keycloak:1"], ["one:1", "two:2"]]
)
def test_archive_cannot_substitute_another_image(tmp_path, tags):
    with pytest.raises(ValueError):
        candidate.archive_reference(
            docker_archive(tmp_path / "image.tar", tags), "launchpad"
        )


def test_required_build_cannot_fall_back_to_predecessor(tmp_path):
    with pytest.raises(ValueError, match="missing current-attempt"):
        candidate.prepare_images(
            [{"image-name": "launchpad"}],
            {"flags": {"launchpad": True}},
            tmp_path,
            tmp_path,
            "localhost:5000",
            {candidate.REPOSITORY + "launchpad": ("old", DIGEST)},
            tmp_path,
        )


def test_unchanged_image_carries_only_frozen_ref_without_registry_lookup(tmp_path):
    rows = candidate.prepare_images(
        [{"image-name": "launchpad"}],
        {"flags": {"launchpad": False}},
        tmp_path,
        tmp_path,
        "localhost:5000",
        {candidate.REPOSITORY + "launchpad": ("old", DIGEST)},
        tmp_path,
    )
    assert rows == [
        {
            "name": "launchpad",
            "repository": candidate.REPOSITORY + "launchpad",
            "tag": "old",
            "digest": DIGEST,
            "fresh": False,
            "layout": None,
            "legacyTag": None,
            "publishLegacy": False,
        }
    ]


def test_unplanned_build_cannot_be_silently_ignored(tmp_path):
    (tmp_path / "launchpad.tar").touch()
    with pytest.raises(ValueError, match="unplanned"):
        candidate.prepare_images(
            [{"image-name": "launchpad"}],
            {"flags": {"launchpad": False}},
            tmp_path,
            tmp_path,
            "localhost:5000",
            {candidate.REPOSITORY + "launchpad": ("old", DIGEST)},
            tmp_path,
        )


def test_workflow_catalog_covers_the_existing_haul_inventory():
    images, charts = candidate.catalog()
    names = {candidate.REPOSITORY + row["image-name"] for row in images}
    names |= {candidate.REPOSITORY + "charts/" + row["chart-name"] for row in charts}
    inventory = {
        line.strip()
        for line in (candidate.ROOT / "tooling/manifest/components.txt")
        .read_text()
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert names == inventory
