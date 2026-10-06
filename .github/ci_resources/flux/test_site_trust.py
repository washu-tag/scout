"""Controller status must prove signature enforcement, not merely failure."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import site_trust as trust


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SOURCE = "oci://127.0.0.1:5000/scout-site"
KEY = "scout-site-cosign-pub"


def source(name="scout-site", good=True, url=SOURCE, digest=DIGEST, key=KEY):
    return {
        "metadata": {"name": name, "namespace": "flux-system", "generation": 2},
        "spec": {
            "url": url,
            "ref": {"digest": digest},
            "verify": {
                "provider": "cosign",
                "secretRef": {"name": key},
            },
        },
        "status": {
            "observedGeneration": 2 if good else -1,
            **(
                {"artifact": {"revision": digest, "digest": OTHER_DIGEST}}
                if good
                else {}
            ),
            "conditions": [
                {
                    "type": kind,
                    "status": "True" if good else "False",
                    "reason": "Succeeded" if good else "VerificationError",
                    "observedGeneration": 2,
                }
                for kind in ("Ready", "SourceVerified")
            ],
        },
    }


def dependent(name):
    return {
        "metadata": {"name": name, "namespace": "flux-system", "generation": 1},
        "spec": {
            "sourceRef": {"kind": "OCIRepository", "name": name},
            "targetNamespace": "ci-site-negative",
            "path": "./",
            "prune": False,
        },
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "ArtifactFailed",
                    "observedGeneration": 1,
                }
            ]
        },
    }


def test_positive_uses_oci_revision_not_archive_digest():
    obj = source()
    assert trust.positive_ready(obj, SOURCE, DIGEST, KEY)
    obj["status"]["artifact"] = {"revision": OTHER_DIGEST, "digest": DIGEST}
    with pytest.raises(trust.TrustError, match="resolved OCI revision"):
        trust.positive_ready(obj, SOURCE, DIGEST, KEY)


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "oci://attacker/scout-site"),
        ("ref", {"tag": "latest"}),
        ("ref", {"digest": OTHER_DIGEST}),
        ("ref", {"digest": DIGEST, "tag": "latest"}),
        ("verify", {"provider": "cosign", "secretRef": {"name": "untrusted"}}),
        ("verify", {}),
        ("suspend", True),
    ],
)
def test_positive_fails_on_changed_source_or_policy(field, value):
    obj = source()
    obj["spec"][field] = value
    with pytest.raises(trust.TrustError, match="identity or verification"):
        trust.positive_ready(obj, SOURCE, DIGEST, KEY)


@pytest.mark.parametrize("which", ["Ready", "SourceVerified", "top"])
def test_positive_does_not_accept_stale_success(which):
    obj = source()
    if which == "top":
        obj["status"]["observedGeneration"] = 1
    else:
        trust.condition(obj, which)["observedGeneration"] = 1
    assert not trust.positive_ready(obj, SOURCE, DIGEST, KEY)


def test_negative_recoverable_error_does_not_require_top_observed_generation():
    obj = source(good=False)
    assert obj["status"]["observedGeneration"] == -1
    assert trust.negative_rejected(obj, SOURCE, DIGEST, KEY)
    del obj["status"]["observedGeneration"]
    assert trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


@pytest.mark.parametrize("which", ["Ready", "SourceVerified"])
def test_negative_cannot_reuse_stale_rejection(which):
    obj = source(good=False)
    trust.condition(obj, which)["observedGeneration"] = 1
    assert not trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


@pytest.mark.parametrize("which", ["Ready", "SourceVerified"])
@pytest.mark.parametrize(
    "reason", ["FetchFailed", "AuthenticationFailed", "BucketOperationFailed"]
)
def test_unrelated_source_failure_is_not_a_passing_negative(which, reason):
    obj = source(good=False)
    trust.condition(obj, which)["reason"] = reason
    with pytest.raises(trust.TrustError, match="other than signature verification"):
        trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


@pytest.mark.parametrize("which", ["Ready", "SourceVerified"])
def test_any_negative_source_acceptance_fails(which):
    obj = source(good=False)
    trust.condition(obj, which)["status"] = "True"
    with pytest.raises(trust.TrustError, match="was accepted"):
        trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


def test_fetch_failure_before_verification_is_not_a_signature_rejection():
    obj = source(good=False)
    obj["status"]["conditions"] = [
        {
            "type": "Ready",
            "status": "False",
            "reason": "FetchFailed",
            "observedGeneration": 2,
        }
    ]
    with pytest.raises(trust.TrustError, match="other than signature verification"):
        trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


def test_rejected_source_must_not_retain_a_usable_artifact():
    obj = source(good=False)
    obj["status"]["artifact"] = {"revision": DIGEST, "url": "http://source/archive"}
    with pytest.raises(trust.TrustError, match="published an artifact"):
        trust.negative_rejected(obj, SOURCE, DIGEST, KEY)


def test_blocked_dependent_needs_no_last_attempted_revision():
    assert trust.dependent_blocked(dependent("ci-site-wrong-key"), "ci-site-wrong-key")


@pytest.mark.parametrize(
    "field,value",
    [
        ("lastAppliedRevision", DIGEST),
        ("inventory", {"entries": [{"id": "canary"}]}),
        ("conditions", [{"type": "Ready", "status": "True", "observedGeneration": 1}]),
    ],
)
def test_dependent_must_never_have_applied(field, value):
    obj = dependent("ci-site-wrong-key")
    obj["status"][field] = value
    with pytest.raises(trust.TrustError, match="applied content"):
        trust.dependent_blocked(obj, "ci-site-wrong-key")


def test_dependent_must_have_observed_blocked_source():
    obj = dependent("ci-site-wrong-key")
    obj["status"] = {}
    assert not trust.dependent_blocked(obj, "ci-site-wrong-key")
    obj["status"] = {
        "conditions": [
            {
                "type": "Ready",
                "status": "False",
                "reason": "BuildFailed",
                "observedGeneration": 1,
            }
        ]
    }
    with pytest.raises(trust.TrustError, match="unrelated reason"):
        trust.dependent_blocked(obj, "ci-site-wrong-key")


def test_get_absence_only_when_kubectl_succeeds(monkeypatch):
    def result(code, stdout=""):
        monkeypatch.setattr(
            trust.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=code, stdout=stdout),
        )

    result(0)
    assert trust.get("configmap", trust.MARKER) is None
    result(1)
    with pytest.raises(trust.TrustError, match="kubectl failed"):
        trust.get("configmap", trust.MARKER)
    result(0, "not-json")
    with pytest.raises(trust.TrustError, match="invalid JSON"):
        trust.get("configmap", trust.MARKER)
    result(
        0, json.dumps({"metadata": {"name": "different", "namespace": "flux-system"}})
    )
    with pytest.raises(trust.TrustError, match="different object"):
        trust.get("configmap", trust.MARKER)


@pytest.fixture
def manifest(tmp_path):
    template = Path(__file__).with_name("site-trust-negative.yaml").read_text()
    template = template.replace("@REGISTRY@", "127.0.0.1:5000")
    template = template.replace("@SITE_GOOD_DIGEST@", DIGEST).replace(
        "@SITE_TAMPERED_DIGEST@", OTHER_DIGEST
    )
    path = tmp_path / "negative.yaml"
    path.write_text(template)
    return path


class FakeCluster:
    def __init__(self, manifest):
        self.identities = trust.negative_specs(manifest)
        self.calls = []
        self.applied = False
        self.mode = "pass"

    def kubectl(self, *args):
        self.calls.append(args)
        if args[0] == "apply":
            self.applied = True
            if self.mode == "partial-apply-error":
                raise trust.TrustError("apply failed")
        if args[0] == "delete" and self.mode == "cleanup-error":
            raise trust.TrustError("cleanup failed")
        return ""

    def get(self, kind, name, namespace="flux-system"):
        if not self.applied:
            return {"exists": True} if self.mode == "existing" else None
        if self.mode == "api-error":
            raise trust.TrustError("API unavailable")
        if kind == "configmap":
            return {"marker": True} if self.mode == "marker" else None
        if kind == trust.KUSTOMIZATION_KIND:
            return dependent(name)
        obj = source(name, False, *self.identities[name])
        if self.mode == "accepted":
            trust.condition(obj, "SourceVerified")["status"] = "True"
        if self.mode == "pending":
            obj["status"] = {}
        return obj


@pytest.fixture
def cluster(monkeypatch, manifest):
    fake = FakeCluster(manifest)
    monkeypatch.setattr(trust, "kubectl", fake.kubectl)
    monkeypatch.setattr(trust, "get", fake.get)
    return fake


def test_both_signature_negatives_pass_and_cleanup(manifest, cluster):
    trust.run_negative(manifest, timeout=1)
    assert [call[0] for call in cluster.calls] == ["apply", "delete"]


@pytest.mark.parametrize(
    "mode,match",
    [
        ("accepted", "was accepted"),
        ("marker", "marker was applied"),
        ("api-error", "API unavailable"),
        ("partial-apply-error", "apply failed"),
        ("pending", "timed out"),
        ("cleanup-error", "cleanup failed"),
    ],
)
def test_failures_always_cleanup_after_apply(manifest, cluster, mode, match):
    cluster.mode = mode
    with pytest.raises(trust.TrustError, match=match):
        trust.run_negative(manifest, timeout=0)
    assert [call[0] for call in cluster.calls] == ["apply", "delete"]


def test_preexisting_resource_is_never_reused_or_deleted(manifest, cluster):
    cluster.mode = "existing"
    with pytest.raises(trust.TrustError, match="already exists"):
        trust.run_negative(manifest, timeout=1)
    assert not cluster.calls


def test_cleanup_scope_rejects_extra_resources(manifest):
    docs = list(yaml.safe_load_all(manifest.read_text()))
    extra = copy.deepcopy(docs[0])
    extra["metadata"]["name"] = "production"
    manifest.write_text(yaml.safe_dump_all(docs + [extra]))
    with pytest.raises(trust.TrustError, match="exactly the five"):
        trust.negative_specs(manifest)


def test_good_and_tampered_fixture_digests_must_differ(manifest):
    manifest.write_text(manifest.read_text().replace(OTHER_DIGEST, DIGEST))
    with pytest.raises(trust.TrustError, match="distinct digests"):
        trust.negative_specs(manifest)


def test_cleanup_failure_does_not_hide_original_verdict(manifest, cluster):
    cluster.mode = "accepted"
    original = cluster.kubectl

    def fail_cleanup(*args):
        if args[0] == "delete":
            raise trust.TrustError("cleanup failed")
        return original(*args)

    # run_negative uses the patched module callable, not the instance method.
    from unittest.mock import patch

    with patch.object(trust, "kubectl", fail_cleanup):
        with pytest.raises(
            trust.TrustError,
            match="was accepted; negative resource cleanup also failed",
        ):
            trust.run_negative(manifest, timeout=0)


def test_verdict_evidence_precedes_cleanup_without_condition_messages(
    manifest, cluster, capsys
):
    original_get = cluster.get

    def with_sensitive_message(*args, **kwargs):
        obj = original_get(*args, **kwargs)
        if obj and "status" in obj:
            for c in obj["status"].get("conditions", []):
                c["message"] = "SECRET_SENTINEL"
        return obj

    from unittest.mock import patch

    with patch.object(trust, "get", with_sensitive_message):
        trust.run_negative(manifest, timeout=1)
    out = capsys.readouterr().out
    assert "SECRET_SENTINEL" not in out
    for name in trust.CASES:
        assert name + ": " in out
    for field in (
        "Ready",
        "SourceVerified",
        "observedGeneration",
        "artifact.revision",
        "dependent.lastAppliedRevision",
    ):
        assert field in out


def test_cli_reports_api_failure(monkeypatch, capsys):
    def broken(*args):
        raise trust.TrustError("API unavailable")

    monkeypatch.setattr(trust, "wait_positive", broken)
    assert (
        trust.main(
            [
                "positive",
                "--name",
                "scout-site",
                "--source",
                SOURCE,
                "--digest",
                DIGEST,
                "--key",
                KEY,
            ]
        )
        == 1
    )
    assert "API unavailable" in capsys.readouterr().err
