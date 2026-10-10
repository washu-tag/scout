"""Same-version dispatch resumes original inputs despite current-branch drift."""

import copy
import json
import importlib.util
from pathlib import Path
import shutil

import yaml

import pytest

import promote as p
from test_promote import BOUNDARY, SHA, VERSION, fixture, promote


@pytest.mark.parametrize("failure", ["upload-.yaml", "upload-.json", "publish"])
def test_redispatch_retains_original_attempt_through_each_draft_stage(
    tmp_path, failure
):
    api, oci = fixture()
    api.fail = failure
    with pytest.raises(p.PromotionError, match="injected"):
        promote(api, oci, tmp_path)
    inputs = p.resume_inputs(api, p.REPOSITORY, VERSION)
    assert inputs == p.promotion_inputs(VERSION, SHA, 101, 2, BOUNDARY)
    # Discovery never substitutes the current attempt for the recorded attempt.
    api.runs[101]["run_attempt"] = 3
    assert p.resume_inputs(api, p.REPOSITORY, VERSION) == inputs
    api.runs[101]["run_attempt"] = 2
    promote(api, oci, tmp_path)
    assert api.release["draft"] is False
    assert p.resume_inputs(api, p.REPOSITORY, VERSION) == inputs


def test_current_bootstrap_then_stamped_tooling_survives_policy_and_catalog_drift(
    tmp_path,
):
    api, oci = fixture()
    api.fail = "publish"
    with pytest.raises(p.PromotionError):
        promote(api, oci, tmp_path / "first")
    root = Path(p.__file__).resolve().parents[2]
    stamped = tmp_path / "stamped"
    for path in (
        "tooling/release/promote.py",
        "tooling/deploy/artifact_identity.py",
        "tooling/manifest/producer_plan.py",
        "tooling/manifest/build_haul.py",
        "tooling/manifest/haul.py",
        "tooling/manifest/resolve.py",
        ".github/workflows/ci.yaml",
    ):
        target = stamped / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / path, target)
    current = tmp_path / "main"
    shutil.copytree(stamped, current)
    source = current / "tooling/release/promote.py"
    source.write_text(
        source.read_text()
        .replace('"prepare-flux-artifacts",', '"a-new-job",')
        .replace('"onprem-core-ingest-auth"', '"a-new-profile"')
        .replace('"legacy Ansible release outputs;', '"a-new-scope;')
    )
    ci = current / ".github/workflows/ci.yaml"
    catalog = yaml.safe_load(ci.read_text())
    catalog["jobs"]["publish-charts"]["strategy"]["matrix"]["include"].append(
        {"chart-name": "new-chart", "chart-dir": "helm/new-chart"}
    )
    ci.write_text(yaml.safe_dump(catalog))

    def load(checkout, name):
        spec = importlib.util.spec_from_file_location(
            name, checkout / "tooling/release/promote.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    bootstrap = load(current, "current_release_bootstrap")
    assert bootstrap.REQUIRED_JOBS != p.REQUIRED_JOBS
    assert bootstrap.CHARTS != p.CHARTS
    original = bootstrap.resume_inputs(api, p.REPOSITORY, VERSION)
    # release.yaml checks out this recovered revision before executing promotion.
    policy = load(stamped, "stamped_release_policy")
    policy.promote(
        api,
        oci,
        repository=p.REPOSITORY,
        version=VERSION,
        revision=original["revision"],
        producer_id=original["runId"],
        producer_attempt=original["runAttempt"],
        boundary_sha=original["boundaryRevision"],
        work_dir=tmp_path / "retry",
    )
    assert api.release["draft"] is False
    assert api.events.count("create-draft") == 1


def test_marker_and_record_must_agree_before_selecting_tooling(tmp_path):
    api, oci = fixture()
    api.fail = "publish"
    with pytest.raises(p.PromotionError):
        promote(api, oci, tmp_path)
    wrong = p.promotion_inputs(VERSION, SHA, 101, 3, BOUNDARY)
    api.release["body"] = p.DRAFT_INPUTS + json.dumps(wrong) + " -->"
    before = copy.deepcopy(api.events)
    with pytest.raises(p.PromotionError, match="conflict"):
        p.resume_inputs(api, p.REPOSITORY, VERSION)
    assert api.events == before


def test_old_release_without_record_cannot_be_restamped():
    api, _ = fixture()
    api.release = dict(id=303, tag_name="v" + VERSION, draft=False)
    with pytest.raises(p.PromotionError, match="no resumable"):
        p.resume_inputs(api, p.REPOSITORY, VERSION)


def test_missing_chart_is_packaged_but_signed_charts_are_reused():
    _, oci = fixture()
    name = next(iter(p.CHARTS))
    del oci.tags[f"ghcr.io/washu-tag/charts/{name}:{VERSION}"]
    assert p.charts_to_package(oci, VERSION, SHA) == {name: p.CHARTS[name]}
    assert len(oci.verified) == len(p.CHARTS) - 1
    assert oci.events == []
    with pytest.raises(p.PromotionError, match="original digest"):
        p.charts_to_package(oci, VERSION, SHA, resume=True)


def test_bad_existing_chart_signature_fails_without_repackaging():
    _, oci = fixture()
    name = next(iter(p.CHARTS))
    repository = "ghcr.io/washu-tag/charts/" + name
    oci.bad_signature = repository + "@" + oci.tags[repository + ":" + VERSION]
    with pytest.raises(p.PromotionError, match="wrong key"):
        p.charts_to_package(oci, VERSION, SHA)
    assert oci.events == []


def test_release_image_list_comes_from_shared_ci_catalog():
    from producer_plan import IMAGE_PATHS, VENDOR_IMAGES

    assert set(p.IMAGES) == IMAGE_PATHS.keys() - set(VENDOR_IMAGES)


def test_chart_written_before_draft_cannot_be_reused_after_a_new_stamp():
    api, oci = fixture()
    assert api.release is None
    assert p.charts_to_package(oci, VERSION, SHA) == {}
    with pytest.raises(p.PromotionError, match="revision mismatch"):
        p.charts_to_package(oci, VERSION, "c" * 40)
    assert oci.events == []


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_branch_wait_accepts_exact_successful_candidate_without_main_publication(event):
    api, _ = fixture()
    api.runs[101].update(head_branch="release/4.2", event=event)
    selected = p.find_evidence(api, p.REPOSITORY, SHA, "release/4.2")
    assert selected["runId"] == 101 and selected["runAttempt"] == 2
    assert api.downloads == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_branch", "other-branch"),
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_repository", {"full_name": "other/scout"}),
    ],
)
def test_branch_wait_rejects_mismatched_or_failed_attempt(field, value):
    api, _ = fixture()
    api.runs[101].update(head_branch="release/4.2")
    api.runs[101][field] = value
    with pytest.raises(p.PromotionError):
        p.find_evidence(api, p.REPOSITORY, SHA, "release/4.2")


def test_branch_wait_ignores_same_sha_pull_request_runs():
    api, _ = fixture()
    api.runs[101].update(head_branch="release/4.2", event="workflow_dispatch")
    request = api.request

    def request_with_pr(path, **kwargs):
        if "/actions/workflows/" in path and "/runs?" in path:
            return {
                "workflow_runs": [
                    dict(api.runs[101], id=202, event="pull_request"),
                    api.runs[101],
                ]
            }
        return request(path, **kwargs)

    api.request = request_with_pr
    assert p.find_evidence(api, p.REPOSITORY, SHA, "release/4.2")["runId"] == 101


def test_branch_retry_selects_newest_manual_dispatch_for_the_same_stamp():
    api, _ = fixture()
    api.runs[101].update(
        head_branch="release/4.2", event="workflow_dispatch", conclusion="failure"
    )
    api.runs[102] = dict(api.runs[101], id=102, run_attempt=1, conclusion="success")
    request = api.request

    def newest_first(path, **kwargs):
        if "/actions/workflows/" in path and "/runs?" in path:
            return {"workflow_runs": [api.runs[102], api.runs[101]]}
        return request(path, **kwargs)

    api.request = newest_first
    selected = p.find_evidence(api, p.REPOSITORY, SHA, "release/4.2")
    assert selected["runId"] == 102 and selected["runAttempt"] == 1
