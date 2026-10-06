"""Independent regressions for real draft-release lookup and recovery semantics."""

import copy
import subprocess

import pytest

import promote as p
from test_promote import FakeGitHub, VERSION, fixture, promote


class PublishedTagLookup(FakeGitHub):
    """GitHub's tag endpoint cannot discover an unpublished draft."""

    duplicate = False

    def request(self, path, *, method="GET", data=None, missing_ok=False):
        suffix = path.split("/repos/" + p.REPOSITORY + "/", 1)[1].split("?", 1)[0]
        if method == "GET" and suffix == "releases/tags/v" + VERSION:
            return (
                None if self.release is None or self.release["draft"] else self.release
            )
        if method == "GET" and suffix == "releases":
            result = [copy.deepcopy(self.release)] if self.release else []
            if self.duplicate and self.release:
                result.append(dict(self.release, id=404))
            return result
        if method == "GET" and suffix == "releases/303":
            return copy.deepcopy(self.release)
        return super().request(path, method=method, data=data, missing_ok=missing_ok)


@pytest.mark.parametrize(
    "failure", ["upload-.yaml", "upload-.json", "publish", "alias"]
)
def test_draft_invisible_to_tag_lookup_resumes_original_release(tmp_path, failure):
    source, oci = fixture()
    api = PublishedTagLookup(source.producer, source.proof)
    if failure == "alias":
        oci.fail = p.REGISTRIES["bundleDigest"]
    else:
        api.fail = failure
    with pytest.raises(p.PromotionError, match="injected"):
        promote(api, oci, tmp_path)
    assert api.release["draft"] is True
    assert (
        api.request(f"/repos/{p.REPOSITORY}/releases/tags/v{VERSION}", missing_ok=True)
        is None
    )
    original_id = api.release["id"]
    promote(api, oci, tmp_path)
    assert api.release["id"] == original_id
    assert api.release["draft"] is False
    assert api.events.count("create-draft") == 1
    assert api.events.count("publish") == 1


def test_conflicting_draft_record_is_found_before_any_retry_mutation(tmp_path):
    source, oci = fixture()
    api = PublishedTagLookup(source.producer, source.proof)
    api.fail = "publish"
    with pytest.raises(p.PromotionError, match="injected"):
        promote(api, oci, tmp_path)
    api.assets[f"scout-release-{VERSION}.yaml"] = b"different selected build"
    before = copy.deepcopy(api.assets), list(api.events), list(oci.events)
    with pytest.raises(p.PromotionError, match="conflicts"):
        promote(api, oci, tmp_path)
    assert before == (api.assets, api.events, oci.events)


def test_ambiguous_existing_drafts_fail_before_any_mutation(tmp_path):
    source, oci = fixture()
    api = PublishedTagLookup(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": True}
    api.duplicate = True
    with pytest.raises(p.PromotionError, match="multiple releases"):
        promote(api, oci, tmp_path)
    assert api.events == []
    assert oci.events == []


def test_draft_reads_use_writer_identity_but_actions_evidence_keeps_reader(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "reader-fixture")
    monkeypatch.setenv("RELEASE_GH_TOKEN", "writer-fixture")
    seen = []

    def command(*args, **kwargs):
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(args, 0, b"HTTP/2.0 200 OK\n\n{}", b"")

    monkeypatch.setattr(subprocess, "run", command)
    api = p.GitHub()
    api.request("/repos/a/b/actions/runs/123/attempts/1")
    api.request("/repos/a/b/releases?per_page=100&page=1")
    api.request("/repos/a/b/releases/303/assets?per_page=100&page=1")
    api.asset("a/b", 404)
    assert seen[0] is None
    assert all(env["GH_TOKEN"] == "writer-fixture" for env in seen[1:])


class StarterGitHub(PublishedTagLookup):
    def __init__(self, producer, proof):
        super().__init__(producer, proof)
        self.starters = {}
        self.fail_upload = None
        self.changed_asset = None
        self.fail_delete = None

    def starter(self, suffix=".yaml", **changes):
        asset_id = 900 + len(self.starters)
        self.starters[asset_id] = {
            "id": asset_id,
            "name": f"scout-release-{VERSION}{suffix}",
            "state": "starter",
            "size": 0,
            **changes,
        }
        return asset_id

    def request(self, path, *, method="GET", data=None, missing_ok=False):
        suffix = path.split("/repos/" + p.REPOSITORY + "/", 1)[1].split("?", 1)[0]
        if suffix == "releases/303/assets" and method == "GET":
            assets = super().request(
                path, method=method, data=data, missing_ok=missing_ok
            )
            return assets + copy.deepcopy(list(self.starters.values()))
        if suffix.startswith("releases/assets/"):
            asset_id = int(suffix.rsplit("/", 1)[1])
            if method == "GET":
                if asset_id == self.changed_asset:
                    return dict(self.starters[asset_id], state="uploaded", size=12)
                return copy.deepcopy(self.starters[asset_id])
            if method == "DELETE":
                if asset_id == self.fail_delete:
                    self.fail_delete = None
                    raise p.PromotionError("injected failed starter deletion")
                self.mutate("delete-starter")
                del self.starters[asset_id]
                return {}
        return super().request(path, method=method, data=data, missing_ok=missing_ok)

    def upload(self, repository, release_id, file):
        if self.fail_upload and file.name.endswith(self.fail_upload):
            self.starter(self.fail_upload)
            self.fail_upload = None
            raise p.PromotionError("injected upload 502 with starter asset")
        assert not any(asset["name"] == file.name for asset in self.starters.values())
        return super().upload(repository, release_id, file)


@pytest.mark.parametrize("suffix", [".yaml", ".sigstore.json"])
def test_real_failed_upload_placeholder_is_cleaned_then_resumed(tmp_path, suffix):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.fail_upload = suffix
    with pytest.raises(p.PromotionError, match="upload 502"):
        promote(api, oci, tmp_path)
    assert api.release["draft"] is True
    assert len(api.starters) == 1
    promote(api, oci, tmp_path)
    assert not api.starters
    assert api.release["draft"] is False
    assert api.events.count("create-draft") == 1
    assert api.events.count("delete-starter") == 1
    assert api.events[-1] == "publish"


@pytest.mark.parametrize("case", ["proof", "package", "alias", "tag", "boundary"])
def test_no_placeholder_deletion_before_complete_preflight(tmp_path, case):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": True}
    api.starter()
    if case == "proof":
        api.proof["valuesMode"] = "plain"
    elif case == "package":
        oci.bad_signature = (
            p.REGISTRIES["bundleDigest"] + "@" + api.producer["bundleDigest"]
        )
    elif case == "alias":
        oci.tags[p.REGISTRIES["configDigest"] + ":" + VERSION] = p.digest(
            b"other package"
        )
    elif case == "tag":
        api.tag["object"]["sha"] = "d" * 40
    else:
        api.compare_status = "diverged"
    with pytest.raises((p.PromotionError, p.IdentityError)):
        promote(api, oci, tmp_path)
    assert len(api.starters) == 1
    assert api.events == []
    assert oci.events == []


@pytest.mark.parametrize(
    "change",
    [
        {"size": 1},
        {"size": False},
        {"state": "uploaded"},
        {"name": "unrelated-file.yaml"},
        {"name": "../scout-release-1.2.3.yaml"},
    ],
)
def test_only_exact_expected_empty_starter_is_recoverable(tmp_path, change):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": True}
    api.starter(**change)
    with pytest.raises(p.PromotionError):
        promote(api, oci, tmp_path)
    assert len(api.starters) == 1
    assert api.events == []
    assert oci.events == []


def test_published_empty_starter_is_never_deleted(tmp_path):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": False}
    api.starter()
    with pytest.raises(p.PromotionError, match="recoverable draft"):
        promote(api, oci, tmp_path)
    assert len(api.starters) == 1
    assert api.events == []
    assert oci.events == []


def test_every_placeholder_is_rechecked_before_deleting_any(tmp_path):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": True}
    api.starter()
    api.changed_asset = api.starter(".sigstore.json")
    with pytest.raises(p.PromotionError, match="upload changed"):
        promote(api, oci, tmp_path)
    assert len(api.starters) == 2
    assert api.events == []
    assert oci.events == []


def test_failed_placeholder_delete_can_retry_without_replacing_finished_evidence(
    tmp_path,
):
    source, oci = fixture()
    api = StarterGitHub(source.producer, source.proof)
    api.release = {"id": 303, "tag_name": "v" + VERSION, "draft": True}
    api.starter()
    api.fail_delete = api.starter(".sigstore.json")
    with pytest.raises(p.PromotionError, match="failed starter deletion"):
        promote(api, oci, tmp_path)
    assert len(api.starters) == 1
    assert api.assets == {}
    assert oci.events == []
    promote(api, oci, tmp_path)
    assert api.release["draft"] is False
    assert not api.starters
    assert api.events.count("delete-starter") == 2
