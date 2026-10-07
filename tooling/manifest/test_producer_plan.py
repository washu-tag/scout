"""Producer ordering/carry regression cases using actual Git history, offline."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from build_haul import parse_fresh, parse_predecessor
from haul import render_images
from producer_plan import (
    CARRY_POLICY,
    CHART_PATHS,
    IMAGE_CHARTS,
    IMAGE_PATHS,
    REPO,
    catalog,
    classify,
    context,
    create,
    render,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
COMPONENTS = [REPO + n for n in IMAGE_PATHS] + [
    REPO + "charts/" + n for n in CHART_PATHS
]


def run_git(repo, *args):
    return (
        subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE)
        .decode()
        .strip()
    )


def commit(repo, path, value="changed"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-qm", path)
    return run_git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "user.email", "test@example.invalid")
    base = commit(repo, "README.md", "baseline")
    return repo, base


def snapshot(tmp_path, revision, *, marked=True):
    dest = tmp_path / "snapshot"
    dest.mkdir()
    annotations = {
        "org.opencontainers.image.source": "https://github.com/washu-tag/scout",
        "org.opencontainers.image.revision": revision,
        "org.opencontainers.image.version": "0.20261005.1",
    }
    if marked:
        annotations["io.scout.build.carry-policy"] = CARRY_POLICY
    (dest / "manifest.json").write_text(json.dumps({"annotations": annotations}))
    (dest / "haul.yaml").write_text(
        render_images([f"{r}:0.20261005.1@{D1}" for r in COMPONENTS], name="scout")
    )
    return dest


def expected(revision, attempt=1):
    return context("washu-tag/scout", revision, 123, attempt, "0.20261006.2")


def test_failed_and_replaced_pushes_are_in_accumulated_diff(history, tmp_path):
    repo, base = history
    commit(repo, "launchpad/src/a.ts")  # failed/queued-replaced build
    head = commit(repo, "hl7-listener/src/B.java")
    plan = create(snapshot(tmp_path, base), repo, expected(head))
    assert plan["flags"]["launchpad"] and plan["flags"]["launchpad-chart"]
    assert plan["flags"]["hl7-listener"] and plan["flags"]["hl7-listener-chart"]
    assert not plan["flags"]["keycloak"]
    assert len(plan["requiredFresh"]) == 4


def test_newer_completed_build_rejects_old_rerun(history, tmp_path):
    repo, old = history
    newer = commit(repo, "launchpad/new")
    with pytest.raises(ValueError, match="stale/divergent"):
        create(snapshot(tmp_path, newer), repo, expected(old))


def test_divergent_history_fails_closed(history, tmp_path):
    repo, base = history
    left = commit(repo, "launchpad/left")
    run_git(repo, "checkout", "-q", "--detach", base)
    right = commit(repo, "launchpad/right")
    with pytest.raises(ValueError, match="stale/divergent"):
        create(snapshot(tmp_path, left), repo, expected(right))


def test_unmarked_history_rebaselines_every_component(history, tmp_path):
    repo, base = history
    plan = create(snapshot(tmp_path, base, marked=False), repo, expected(base))
    assert not plan["carry"]
    assert plan["publish"]
    assert set(plan["requiredFresh"]) == set(COMPONENTS)


def test_explicit_rebuild_publishes_all_images_and_charts(history, tmp_path):
    repo, base = history
    plan = create(snapshot(tmp_path, base), repo, expected(base), force=True)
    assert not plan["carry"] and set(plan["requiredFresh"]) == set(COMPONENTS)


def test_equal_revision_and_docs_only_publish_nothing(history, tmp_path):
    repo, base = history
    frozen = snapshot(tmp_path, base)
    assert not create(frozen, repo, expected(base))["publish"]
    head = commit(repo, "docs/howto.md")
    assert not create(frozen, repo, expected(head))["publish"]


def test_rerun_recovers_config_after_haul_already_advanced(history, tmp_path):
    repo, base = history
    plan = create(snapshot(tmp_path, base), repo, expected(base, attempt=2))
    assert plan["publish"] and plan["carry"] and not plan["requiredFresh"]


@pytest.mark.parametrize("path", ["deploy/base/foo.yaml", "cosign.pub"])
def test_configuration_changes_publish_without_component_churn(path):
    flags, publish = classify([path])
    assert publish and not any(flags.values())


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yaml",
        ".github/actions/derive-version/action.yaml",
        "tooling/manifest/components.txt",
        "tooling/deploy/stamp_config.py",
        "ansible/group_vars/all/versions.yaml",
    ],
)
def test_shared_build_and_version_inputs_rebuild_all(path):
    flags, publish = classify([path])
    assert publish and all(flags.values())


@pytest.mark.parametrize("image,chart", IMAGE_CHARTS.items())
def test_image_change_also_repackages_its_primary_chart(image, chart):
    flags, _ = classify([IMAGE_PATHS[image][0] + "changed"])
    assert flags[image] and flags[chart + "-chart"]


def test_deleted_path_still_rebuilds_component(history, tmp_path):
    repo, _ = history
    base = commit(repo, "launchpad/deleted.ts")
    (repo / "launchpad/deleted.ts").unlink()
    run_git(repo, "add", "-u")
    run_git(repo, "commit", "-qm", "delete")
    head = run_git(repo, "rev-parse", "HEAD")
    assert create(snapshot(tmp_path, base), repo, expected(head))["flags"]["launchpad"]


def test_cross_attempt_plan_is_rejected(history, tmp_path):
    repo, base = history
    frozen = snapshot(tmp_path, base)
    create(frozen, repo, expected(base))
    with pytest.raises(ValueError, match="rerun all jobs"):
        validate(frozen, expected(base, attempt=2))


@pytest.mark.parametrize("file", ["manifest.json", "haul.yaml"])
def test_frozen_snapshot_cannot_change_after_plan(history, tmp_path, file):
    repo, base = history
    frozen = snapshot(tmp_path, base)
    create(frozen, repo, expected(base))
    with (frozen / file).open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="content changed"):
        validate(frozen, expected(base))


def test_wrong_repository_is_rejected(history, tmp_path):
    repo, base = history
    with pytest.raises(ValueError, match="source repository"):
        create(
            snapshot(tmp_path, base),
            repo,
            {**expected(base), "repository": "other/scout"},
        )


def test_fork_can_explicitly_use_signed_upstream_predecessor(history, tmp_path):
    repo, base = history
    head = commit(repo, "launchpad/from-fork.ts")
    fork = {**expected(head), "repository": "contributor/scout"}
    plan = create(
        snapshot(tmp_path, base),
        repo,
        fork,
        predecessor_repository="washu-tag/scout",
    )
    assert plan["repository"] == "contributor/scout"
    assert plan["predecessorRepository"] == "washu-tag/scout"
    assert plan["flags"]["launchpad"]
    assert len(plan["requiredFresh"]) == 2


def test_upstream_cannot_accept_another_predecessor_repository(history, tmp_path):
    repo, base = history
    frozen = snapshot(tmp_path, base)
    metadata = json.loads((frozen / "manifest.json").read_text())
    metadata["annotations"][
        "org.opencontainers.image.source"
    ] = "https://github.com/other/scout"
    (frozen / "manifest.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="source repository"):
        create(frozen, repo, expected(base), predecessor_repository="other/scout")


def test_fork_predecessor_override_still_requires_ancestry(history, tmp_path):
    repo, base = history
    newer = commit(repo, "launchpad/newer.ts")
    with pytest.raises(ValueError, match="stale/divergent"):
        create(
            snapshot(tmp_path, newer),
            repo,
            {**expected(base), "repository": "contributor/scout"},
            predecessor_repository="washu-tag/scout",
        )


def test_render_requires_every_planned_fresh_artifact(history, tmp_path):
    repo, base = history
    head = commit(repo, "launchpad/changed")
    frozen = snapshot(tmp_path, base)
    create(frozen, repo, expected(head))
    digests = tmp_path / "digests"
    digests.mkdir()
    with pytest.raises(ValueError, match="cannot carry"):
        render(
            frozen, expected(head), digests, ROOT / "tooling/manifest/components.txt"
        )
    for n, name in enumerate(("launchpad", "charts/launchpad")):
        (digests / f"{n}.txt").write_text(f"name {REPO}{name}:0.20261006.2@{D2}\n")
    out = render(
        frozen, expected(head), digests, ROOT / "tooling/manifest/components.txt"
    )
    assert f"{REPO}launchpad:0.20261006.2@{D2}" in out
    assert f"{REPO}keycloak:0.20261005.1@{D1}" in out


@pytest.mark.parametrize("mode", ["wrong-version", "unexpected"])
def test_render_rejects_other_build_or_unplanned_receipts(history, tmp_path, mode):
    repo, base = history
    head = commit(repo, "launchpad/changed")
    frozen = snapshot(tmp_path, base)
    create(frozen, repo, expected(head))
    digests = tmp_path / "digests"
    digests.mkdir()
    for n, name in enumerate(("launchpad", "charts/launchpad")):
        (digests / f"{n}.txt").write_text(f"name {REPO}{name}:0.20261006.2@{D2}\n")
    if mode == "wrong-version":
        (digests / "0.txt").write_text(f"name {REPO}launchpad:0.20261005.1@{D2}\n")
    else:
        (digests / "extra.txt").write_text(f"name {REPO}keycloak:0.20261006.2@{D2}\n")
    with pytest.raises(ValueError, match="another build version|unplanned fresh"):
        render(
            frozen, expected(head), digests, ROOT / "tooling/manifest/components.txt"
        )


def test_vendor_source_changes_update_legacy_but_shared_rebuild_preserves_it(
    history, tmp_path
):
    repo, base = history
    head = commit(repo, "keycloak/Dockerfile")
    frozen = snapshot(tmp_path, base)
    plan = create(frozen, repo, expected(head))
    assert plan["legacy"]["keycloak-legacy"] and not plan["legacy"]["superset-legacy"]
    # An explicit rebuild alone publishes fresh build tags without altering the
    # pinned upstream/vendor tag used by legacy deployments.
    plan = create(frozen, repo, expected(base), force=True)
    assert all(plan["flags"].values()) and not any(plan["legacy"].values())


@pytest.mark.parametrize("fresh", [False, True])
def test_superset_chart_defaults_to_selected_build_image(tmp_path, fresh):
    haul = tmp_path / "haul.yaml"
    haul.write_text(render_images([f"{REPO}superset:0.20261005.1@{D1}"], name="scout"))
    version = subprocess.check_output(
        [
            "bash",
            str(ROOT / ".github/scripts/chart-app-version.sh"),
            "scout-dashboards",
            "0.20261006.2",
            str(haul),
        ],
        env={**os.environ, "IMAGE_REBUILT": str(fresh).lower()},
        text=True,
    ).strip()
    assert version == ("0.20261006.2" if fresh else "0.20261005.1")


def test_missing_inventory_component_is_planned_before_building(history, tmp_path):
    repo, base = history
    frozen = snapshot(tmp_path, base)
    text = (frozen / "haul.yaml").read_text()
    (frozen / "haul.yaml").write_text(
        "\n".join(line for line in text.splitlines() if REPO + "launchpad:" not in line)
    )
    plan = create(frozen, repo, expected(base))
    assert plan["flags"]["launchpad"] and plan["flags"]["launchpad-chart"]


@pytest.mark.parametrize("parser", [parse_fresh, parse_predecessor])
def test_duplicate_component_receipts_are_rejected(tmp_path, parser):
    if parser is parse_fresh:
        for name in ("one", "two"):
            (tmp_path / name).write_text(
                f"launchpad {REPO}launchpad:0.20261006.2@{D1}\n"
            )
        path = tmp_path
    else:
        path = tmp_path / "haul.yaml"
        path.write_text(
            "spec:\n  images:\n"
            + f"    - name: {REPO}launchpad:0.20261006.2@{D1}\n" * 2
        )
    with pytest.raises(ValueError, match="duplicate"):
        parser(str(path))


def workflow_catalog(tmp_path, mutate):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())
    jobs = workflow["jobs"]
    filter_step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "filter")
    filters = yaml.safe_load(filter_step["with"]["filters"])
    mutate(jobs, filters)
    filter_step["with"]["filters"] = yaml.safe_dump(filters)
    path = tmp_path / "ci.yaml"
    path.write_text(yaml.safe_dump(workflow))
    return path


def test_new_catalog_component_requires_a_path_filter(tmp_path):
    def add_image(jobs, filters):
        jobs["build-and-upload"]["strategy"]["matrix"]["include"].append(
            {"image-name": "new-component", "subproject": "new-component"}
        )

    with pytest.raises(
        ValueError, match="missing component path filter: new-component"
    ):
        catalog(workflow_catalog(tmp_path, add_image))


def test_catalog_uses_shared_paths_and_chart_image_coupling(tmp_path):
    def add_component(jobs, filters):
        jobs["build-and-upload"]["strategy"]["matrix"]["include"].append(
            {"image-name": "new-component", "subproject": "new-component"}
        )
        jobs["publish-charts"]["strategy"]["matrix"]["include"].append(
            {
                "chart-name": "new-chart",
                "changed": "new-chart-chart",
                "image-name": "new-component",
            }
        )
        filters["new-component"] = [
            "new-component/**",
            "shared/source/**",
            "exact.file",
        ]
        filters["new-chart-chart"] = ["helm/new-chart/**"]

    images, charts, links = catalog(workflow_catalog(tmp_path, add_component))
    assert images["new-component"] == ("new-component/", "shared/source/", "exact.file")
    assert charts["new-chart"] == ("helm/new-chart/",)
    assert links["new-component"] == "new-chart"


@pytest.mark.parametrize(
    "pattern", ["launchpad/*.js", "!launchpad/**", "launchpad/[ab].js"]
)
def test_unsupported_catalog_pattern_fails_closed(tmp_path, pattern):
    def replace_filter(jobs, filters):
        filters["launchpad"] = [pattern]

    with pytest.raises(ValueError, match="unsupported component path pattern"):
        catalog(workflow_catalog(tmp_path, replace_filter))


def test_planner_inventory_and_workflow_matrices_stay_in_sync():
    inventory = {
        p.strip()
        for p in (ROOT / "tooling/manifest/components.txt").read_text().splitlines()
        if p.strip() and not p.startswith("#")
    }
    assert inventory == set(COMPONENTS)
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())
    jobs = workflow["jobs"]
    assert {
        m["image-name"]
        for m in jobs["build-and-upload"]["strategy"]["matrix"]["include"]
    } == set(IMAGE_PATHS)
    assert {
        m["chart-name"] for m in jobs["publish-charts"]["strategy"]["matrix"]["include"]
    } == set(CHART_PATHS)
    assert {
        m["image-name"]: m["chart-name"]
        for m in jobs["publish-charts"]["strategy"]["matrix"]["include"]
        if "image-name" in m
    } == IMAGE_CHARTS
    assert "scout-main-producer" in workflow["concurrency"]["group"]
    assert (
        workflow["concurrency"]["cancel-in-progress"]
        == "${{ github.event_name == 'pull_request' }}"
    )
    for name in (
        "publish",
        "publish-charts",
        "publish-haul",
        "config-artifact-publish",
    ):
        steps = jobs[name]["steps"]
        assert any(
            step.get("with", {}).get("name")
            == "flux-candidate-${{ github.run_attempt }}"
            for step in steps
        )
        assert any(
            "copy-flux-artifacts.py validate" in step.get("run", "") for step in steps
        )
        assert not any(
            ":main" in line
            for step in steps
            for line in step.get("run", "").splitlines()
            if line.strip().startswith("oras pull")
        )
    for name in ("bootstrap-haul", "seed-charts"):
        recovery = yaml.safe_load((ROOT / f".github/workflows/{name}.yaml").read_text())
        calls = [
            step
            for job in recovery["jobs"].values()
            for step in job["steps"]
            if step.get("uses") == "./.github/actions/publish-haul"
        ]
        assert calls and all(step["with"]["advance-main"] == "false" for step in calls)


def test_freeze_resolves_alias_once_then_uses_verified_digest(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls"
    manifest = '{"annotations":{}}'
    for name, script in {
        "oras": """#!/usr/bin/env python3
import os,sys,pathlib
args=sys.argv[1:]
with open(os.environ['CALL_LOG'],'a') as f: f.write('oras '+ ' '.join(args)+'\\n')
if args[:2]==['manifest','fetch']:
    pathlib.Path(args[args.index('--output')+1]).write_text(os.environ['MANIFEST'])
elif args[0]=='pull':
    pathlib.Path(args[args.index('-o')+1], 'haul.yaml').write_text('signed payload')
""",
        "cosign": """#!/usr/bin/env python3
import os,sys
with open(os.environ['CALL_LOG'],'a') as f: f.write('cosign '+ ' '.join(sys.argv[1:])+'\\n')
sys.exit(int(os.environ.get('VERIFY_FAILURE','0')))
""",
    }.items():
        (bindir / name).write_text(script)
        (bindir / name).chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
        "CALL_LOG": str(log),
        "MANIFEST": manifest,
    }
    script = ROOT / ".github/scripts/freeze-predecessor.sh"
    subprocess.run(["bash", str(script), str(tmp_path / "out")], env=env, check=True)
    lines = log.read_text().splitlines()
    digest = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
    assert len(lines) == 3 and lines[0].count(":main") == 1
    assert lines[1].startswith("cosign verify") and lines[1].endswith("@" + digest)
    assert lines[2].startswith(
        "oras pull ghcr.io/washu-tag/manifests/scout-manifest@" + digest
    )
    log.write_text("")
    result = subprocess.run(
        ["bash", str(script), str(tmp_path / "failed")],
        env={**env, "VERIFY_FAILURE": "1"},
    )
    assert result.returncode != 0 and "oras pull" not in log.read_text()
