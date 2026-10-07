"""Promotion protocol tests: API context, conflicting inputs and interrupted writes."""

import copy
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

import promote as p

SHA = "a" * 40
BOUNDARY = "b" * 40
VERSION = "1.2.3"


def zip_json(name, value):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, p.canonical(value))
    return stream.getvalue()


def run(run_id, attempt, workflow, event, revision):
    return dict(
        id=run_id,
        run_attempt=attempt,
        workflow_id=workflow,
        event=event,
        head_branch="main",
        head_sha=revision,
        repository={"full_name": p.REPOSITORY},
        head_repository={"full_name": p.REPOSITORY},
        status="completed",
        conclusion="success",
        created_at="2026-10-05T12:00:00Z",
    )


class FakeGitHub:
    """A stateful server fixture; GET and mutations exercise the same protocol."""

    def __init__(self, producer):
        self.producer = producer
        self.runs = {
            101: run(101, 2, 11, "push", SHA),
        }
        self.jobs = [
            dict(
                name=name,
                run_id=101,
                run_attempt=2,
                head_sha=SHA,
                status="completed",
                conclusion="success",
            )
            for name in p.REQUIRED_JOBS
        ]
        self.release = None
        self.tag = {"object": {"type": "commit", "sha": BOUNDARY}}
        self.assets = {}
        self.events = []
        self.fail = None
        self.compare_status = "ahead"
        self.expired = False
        self.extra_artifact = False
        self.archive_digest = None
        self.archives = {}
        self.downloads = 0

    def mutate(self, event):
        if self.fail == event:
            self.fail = None
            raise p.PromotionError("injected " + event)
        self.events.append(event)

    def artifact(self, run_id):
        assert run_id == 101
        name, filename, value = (
            "scout-config-ref-2",
            "scout-config-ref.json",
            self.producer,
        )
        raw = zip_json(filename, value)
        self.archives[run_id] = raw
        return dict(
            id=run_id,
            name=name,
            expired=self.expired,
            workflow_run=self.runs[run_id],
            digest=self.archive_digest or p.digest(raw),
        )

    def request(self, path, *, method="GET", data=None, missing_ok=False):
        tail = path.split("/repos/" + p.REPOSITORY + "/", 1)[1].split("?", 1)[0]
        if tail == "actions/workflows/ci.yaml":
            filename = tail.rsplit("/", 1)[1]
            return dict(
                id=11,
                path=".github/workflows/" + filename,
            )
        if tail.startswith("actions/workflows/") and tail.endswith("/runs"):
            return {"workflow_runs": [self.runs[101]]}
        if tail.startswith("actions/runs/"):
            run_id = int(tail.split("/")[2])
            if tail.endswith("/artifacts"):
                item = self.artifact(run_id)
                return {"artifacts": [item, item] if self.extra_artifact else [item]}
            if tail.endswith("/jobs"):
                return {"jobs": self.jobs}
            attempt = int(tail.split("/")[-1])
            assert attempt == self.runs[run_id]["run_attempt"]
            return self.runs[run_id]
        if tail.startswith("compare/"):
            return dict(status=self.compare_status, merge_base_commit={"sha": BOUNDARY})
        if tail == "releases" and method == "GET":
            return [self.release] if self.release else []
        if tail == "releases/303" and method == "GET":
            return copy.deepcopy(self.release)
        if tail == "releases/generate-notes" and method == "POST":
            self.mutate("generate-notes")
            assert self.tag["object"]["sha"] == SHA
            return {"body": "Release notes through the stamped revision"}
        if tail == "releases/303/assets":
            return [
                dict(id=index, name=name, size=len(value), state="uploaded")
                for index, (name, value) in enumerate(self.assets.items(), 1)
            ]
        if tail == "releases" and method == "POST":
            self.mutate("create-draft")
            self.release = dict(id=303, tag_name=data["tag_name"], draft=True)
            if self.tag is None:  # GitHub may create the source tag with the draft.
                self.tag = {
                    "object": {"type": "commit", "sha": data["target_commitish"]}
                }
            return copy.deepcopy(self.release)
        if tail == "releases/303" and method == "PATCH":
            self.mutate("publish")
            self.release["draft"] = data["draft"]
            if "body" in data:
                self.release["body"] = data["body"]
            return copy.deepcopy(self.release)
        if tail == "git/ref/tags/v" + VERSION:
            return copy.deepcopy(self.tag)
        if tail == "git/refs" or tail == "git/refs/tags/v" + VERSION:
            self.mutate("move-tag")
            assert data.get("force", False) is False
            self.tag = {"object": {"type": "commit", "sha": data["sha"]}}
            return self.tag
        raise AssertionError((method, path, data))

    def download(self, path):
        self.downloads += 1
        return self.archives[int(path.split("/")[-2])]

    def upload(self, repository, version, file):
        self.mutate("upload-" + file.suffix)
        assert file.name not in self.assets
        self.assets[file.name] = file.read_bytes()

    def asset(self, repository, asset_id):
        return list(self.assets.values())[asset_id - 1]


class FakeOCI:
    def __init__(self):
        self.manifests = {}
        self.blobs = {}
        self.tags = {}
        self.verified = []
        self.events = []
        self.fail = None
        self.bad_signature = None

    def manifest(self, reference):
        return self.manifests[reference]

    def resolve(self, reference, *, missing_ok=False):
        if reference not in self.tags and not missing_ok:
            raise p.PromotionError("missing test tag")
        return self.tags.get(reference)

    def verify(self, reference):
        if self.bad_signature == reference:
            raise p.PromotionError("wrong key")
        self.verified.append(reference)

    def blob(self, repository, descriptor):
        return self.blobs[descriptor["digest"]]

    def tag(self, reference, version):
        field = reference.split("@", 1)[0]
        if self.fail == field:
            self.fail = None
            raise p.PromotionError("injected alias failure")
        self.events.append("alias")
        self.tags[field + ":" + version] = reference.split("@", 1)[1]

    def sign_record(self, record, bundle):
        self.events.append("sign")
        bundle.write_bytes(p.digest(record.read_bytes()).encode())

    def verify_record(self, record, bundle):
        p.require(
            bundle.read_bytes() == p.digest(record.read_bytes()).encode(),
            "wrong record signature",
        )


def fixture():
    oci = FakeOCI()
    producer = dict(
        schemaVersion=2,
        repository=p.REPOSITORY,
        revision=SHA,
        runId=101,
        runAttempt=2,
        version="0.20261005.42",
    )
    annotations = {
        "org.opencontainers.image.source": "https://github.com/" + p.REPOSITORY,
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.version": producer["version"],
        "io.scout.build.run-id": "101",
        "io.scout.build.run-attempt": "2",
        "io.scout.build.carry-policy": "predecessor-v1",
    }
    lines = []
    for name in p.IMAGES:
        sha = p.digest(name.encode())
        repository = "ghcr.io/washu-tag/" + name
        oci.tags[repository + ":" + VERSION] = sha
        lines.append("  - name: " + repository + ":" + producer["version"] + "@" + sha)
    layer = ("images:\n" + "\n".join(lines) + "\n").encode()
    layer_digest = p.digest(layer)
    oci.blobs[layer_digest] = layer
    for field, repository in p.REGISTRIES.items():
        anno = dict(annotations)
        if field in ("configDigest", "bundleDigest"):
            anno["io.scout.build.manifest-digest"] = producer["manifestDigest"]
        manifest = {
            "schemaVersion": 2,
            "annotations": anno,
            "artifactType": field,
            "layers": [
                {
                    "mediaType": "application/yaml",
                    "digest": layer_digest,
                    "size": len(layer),
                }
            ],
        }
        raw = p.canonical(manifest)
        producer[field] = p.digest(raw)
        oci.manifests[repository + "@" + producer[field]] = raw
    for name in p.CHARTS:
        oci.tags["ghcr.io/washu-tag/charts/" + name + ":" + VERSION] = p.digest(
            name.encode()
        )
    return FakeGitHub(producer), oci


def promote(api, oci, tmp_path):
    return p.promote(
        api,
        oci,
        repository=p.REPOSITORY,
        version=VERSION,
        revision=SHA,
        producer_id=101,
        producer_attempt=2,
        boundary_sha=BOUNDARY,
        work_dir=tmp_path,
    )


def test_promote_preserves_digests_and_is_idempotent(tmp_path):
    api, oci = fixture()
    record = promote(api, oci, tmp_path).read_bytes()
    assert api.release["draft"] is False
    assert api.events[-1] == "publish"
    assert api.tag["object"]["sha"] == SHA
    assert len(oci.verified) == 3 + len(p.IMAGES) + len(p.CHARTS)
    before = list(api.events), list(oci.events)
    assert promote(api, oci, tmp_path).read_bytes() == record
    assert (api.events, oci.events) == before
    for field, repository in p.REGISTRIES.items():
        assert oci.tags[repository + ":" + VERSION] == api.producer[field]


@pytest.mark.parametrize(
    "failure",
    ["create-draft", "upload-.yaml", "upload-.json", "move-tag", "publish", "alias"],
)
def test_interrupted_promotion_resumes_without_rebuilding(tmp_path, failure):
    api, oci = fixture()
    if failure == "alias":
        oci.fail = p.REGISTRIES["bundleDigest"]
    else:
        api.fail = failure
    with pytest.raises(p.PromotionError, match="injected"):
        promote(api, oci, tmp_path)
    assert api.release is None or api.release["draft"]
    record = (tmp_path / ("scout-release-" + VERSION + ".yaml")).read_bytes()
    assert promote(api, oci, tmp_path).read_bytes() == record
    assert api.release["draft"] is False
    assert api.events.count("publish") == 1


def test_draft_creation_may_create_tag(tmp_path):
    api, oci = fixture()
    api.tag = None
    promote(api, oci, tmp_path)
    assert "move-tag" not in api.events


@pytest.mark.parametrize(
    "case",
    [
        "wrong-producer",
        "manual-producer",
        "fork-producer",
        "failed-producer",
        "other-digest",
        "old-schema",
        "skipped-auth",
        "stale-attempt-job",
        "duplicate-job",
        "expired",
        "duplicate-artifact",
        "corrupt-archive",
        "wrong-key",
        "moving-release-image",
        "alias-conflict",
        "unexpected-tag",
        "annotated-tag",
        "unrelated-boundary",
        "missing-carry-policy",
    ],
)
def test_invalid_inputs_cannot_mutate_release(tmp_path, case):
    api, oci = fixture()
    if case == "wrong-producer":
        api.runs[101]["head_sha"] = "d" * 40
    elif case == "manual-producer":
        api.runs[101]["event"] = "workflow_dispatch"
    elif case == "fork-producer":
        api.runs[101]["head_repository"]["full_name"] = "other/scout"
    elif case == "failed-producer":
        api.runs[101]["conclusion"] = "failure"
    elif case == "other-digest":
        reference = p.REGISTRIES["configDigest"] + "@" + api.producer["configDigest"]
        oci.manifests[reference] += b"\n"
    elif case == "old-schema":
        api.producer["schemaVersion"] = 1
        del api.producer["bundleDigest"]
    elif case == "skipped-auth":
        next(job for job in api.jobs if job["name"] == "deploy-and-test-flux (auth)")[
            "conclusion"
        ] = "skipped"
    elif case == "stale-attempt-job":
        api.jobs[1]["run_attempt"] = 1
    elif case == "duplicate-job":
        api.jobs.append(copy.deepcopy(api.jobs[2]))
    elif case == "expired":
        api.expired = True
    elif case == "duplicate-artifact":
        api.extra_artifact = True
    elif case == "corrupt-archive":
        api.archive_digest = p.digest(b"corrupt")
    elif case == "wrong-key":
        oci.bad_signature = (
            p.REGISTRIES["bundleDigest"] + "@" + api.producer["bundleDigest"]
        )
    elif case == "moving-release-image":
        oci.tags["ghcr.io/washu-tag/launchpad:" + VERSION] = p.digest(b"newer-main")
    elif case == "alias-conflict":
        oci.tags[p.REGISTRIES["configDigest"] + ":" + VERSION] = p.digest(b"other")
    elif case == "unexpected-tag":
        api.tag["object"]["sha"] = "d" * 40
    elif case == "annotated-tag":
        api.tag["object"]["type"] = "tag"
    elif case == "unrelated-boundary":
        api.compare_status = "diverged"
    elif case == "missing-carry-policy":
        reference = (
            p.REGISTRIES["manifestDigest"] + "@" + api.producer["manifestDigest"]
        )
        manifest = json.loads(oci.manifests[reference])
        del manifest["annotations"]["io.scout.build.carry-policy"]
        raw = p.canonical(manifest)
        # Keep the reported digest valid while invalidating package provenance.
        sha = p.digest(raw)
        api.producer["manifestDigest"] = sha
        oci.manifests[p.REGISTRIES["manifestDigest"] + "@" + sha] = raw
    with pytest.raises((p.PromotionError, p.IdentityError)):
        promote(api, oci, tmp_path)
    assert api.events == []
    assert oci.events == []


def test_changed_build_tags_are_not_evidence(tmp_path):
    api, oci = fixture()
    for repository in p.REGISTRIES.values():
        oci.tags[repository + ":" + api.producer["version"]] = p.digest(b"newer-run")
    promote(api, oci, tmp_path)
    assert api.release["draft"] is False


def test_conflicting_existing_record_is_not_overwritten(tmp_path):
    api, oci = fixture()
    api.fail = "publish"
    with pytest.raises(p.PromotionError):
        promote(api, oci, tmp_path)
    api.assets["scout-release-" + VERSION + ".yaml"] = b"conflict"
    before = copy.deepcopy(api.assets), list(api.events), list(oci.events)
    with pytest.raises(p.PromotionError, match="conflicts"):
        promote(api, oci, tmp_path)
    assert before == (api.assets, api.events, oci.events)


def test_wait_selects_explicit_attempts():
    api, _ = fixture()
    producer = p.find_evidence(api, p.REPOSITORY, SHA)
    assert producer["runAttempt"] == 2


def test_notes_follow_final_tag_and_keep_manual_draft_body(tmp_path):
    api, oci = fixture()
    api.fail = "generate-notes"
    with pytest.raises(p.PromotionError, match="generate-notes"):
        promote(api, oci, tmp_path)
    assert api.tag["object"]["sha"] == SHA
    assert api.release["draft"] is True
    api.release["body"] = "Maintainer notes"
    promote(api, oci, tmp_path)
    assert "generate-notes" not in api.events
    assert api.release["body"] == "Maintainer notes"


def test_legacy_drift_during_upload_keeps_release_draft(tmp_path, monkeypatch):
    api, oci = fixture()
    upload = api.upload

    def drifting_upload(*args):
        upload(*args)
        oci.tags["ghcr.io/washu-tag/launchpad:" + VERSION] = p.digest(
            b"concurrent-main"
        )

    monkeypatch.setattr(api, "upload", drifting_upload)
    with pytest.raises(p.PromotionError, match="changed during promotion"):
        promote(api, oci, tmp_path)
    assert api.release["draft"] is True
    assert "publish" not in api.events


@pytest.mark.parametrize("body", [b"[]", b'{"assets": []}'])
def test_github_api_accepts_objects_and_arrays(monkeypatch, body):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            b"HTTP/2.0 200 OK\nContent-Type: application/json\r\n\r\n" + body,
            b"",
        )

    monkeypatch.setattr(subprocess, "run", fake)
    assert p.GitHub().request("/repos/a/b/releases") == json.loads(body)


@pytest.mark.parametrize("body", [b"invalid JSON", b"null"])
def test_github_api_rejects_invalid_json_or_response_type(monkeypatch, body):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, b"HTTP/2.0 200 OK\n\n" + body, b""
        ),
    )
    with pytest.raises(p.PromotionError):
        p.GitHub().request("/repos/a/b/releases")


def test_read_and_release_credentials_are_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "reader")
    monkeypatch.setenv("RELEASE_GH_TOKEN", "writer")
    seen = []

    def fake(*a, **kw):
        seen.append(kw.get("env"))
        return subprocess.CompletedProcess(a, 0, b"HTTP/2.0 200 OK\n\n{}", b"")

    monkeypatch.setattr(subprocess, "run", fake)
    github = p.GitHub()
    github.request("/repos/a/b/actions/runs/1")
    github.request("/repos/a/b/releases", method="POST", data={})
    github.upload("a/b", 303, tmp_path / "record")
    assert seen[0] is None
    assert seen[1]["GH_TOKEN"] == seen[2]["GH_TOKEN"] == "writer"


@pytest.mark.parametrize("name", p.REQUIRED_JOBS)
@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled"])
def test_each_required_ci_job_must_succeed_in_the_selected_attempt(
    tmp_path, name, conclusion
):
    api, oci = fixture()
    next(job for job in api.jobs if job["name"] == name)["conclusion"] = conclusion
    with pytest.raises(p.PromotionError, match="did not succeed"):
        promote(api, oci, tmp_path)
    assert api.events == [] and oci.events == []


def test_rerun_cannot_use_the_previous_attempt_outputs(tmp_path):
    api, oci = fixture()
    api.producer["runAttempt"] = 1
    with pytest.raises(p.IdentityError, match="context mismatch: runAttempt"):
        promote(api, oci, tmp_path)
    assert api.events == [] and oci.events == []


def test_wait_does_not_search_other_workflows(monkeypatch):
    api, _ = fixture()
    request = api.request
    paths = []

    def record(path, **kwargs):
        paths.append(path)
        return request(path, **kwargs)

    monkeypatch.setattr(api, "request", record)
    producer = p.find_evidence(api, p.REPOSITORY, SHA)
    assert producer["revision"] == SHA
    assert not any("deploy-flux" in path or "workflow_run" in path for path in paths)
    assert not any("/202/" in path for path in paths)


@pytest.mark.parametrize(
    "annotation,value",
    [
        ("io.scout.build.manifest-digest", None),
        ("io.scout.build.manifest-digest", "sha256:" + "f" * 64),
        ("io.scout.build.run-attempt", "1"),
    ],
)
def test_signed_bundle_must_bind_the_tested_manifest_and_attempt(
    tmp_path, annotation, value
):
    api, oci = fixture()
    repository = p.REGISTRIES["bundleDigest"]
    manifest = json.loads(
        oci.manifests[repository + "@" + api.producer["bundleDigest"]]
    )
    if value is None:
        manifest["annotations"].pop(annotation)
    else:
        manifest["annotations"][annotation] = value
    raw = p.canonical(manifest)
    api.producer["bundleDigest"] = p.digest(raw)
    oci.manifests[repository + "@" + api.producer["bundleDigest"]] = raw
    with pytest.raises(p.PromotionError, match="producer annotations mismatch"):
        promote(api, oci, tmp_path)
    assert api.events == [] and oci.events == []
