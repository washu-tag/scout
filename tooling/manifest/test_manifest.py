"""Offline unit tests for the Scout build manifest lib. No registry, no network."""

import pytest

from manifest import Artifact, Manifest, Predecessor, assemble, carry_section, validate

D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64


def img(name, digest, build, changed, tag="0.20260730.1"):
    return Artifact(
        name=name,
        ref=f"ghcr.io/washu-tag/{name}:{tag}",
        digest=digest,
        producedByBuild=build,
        changedThisBuild=changed,
    )


def test_pinned_ref():
    a = img("hl7-transformer", D1, "0.20260730.1", True)
    assert a.pinned() == f"ghcr.io/washu-tag/hl7-transformer:0.20260730.1@{D1}"


def test_resolve_image_and_chart():
    m = Manifest(
        version="0.20260730.1",
        sourceCommit="abc1234",
        rebuildScope="changed",
        images=[img("hl7-transformer", D1, "0.20260730.1", True)],
        charts=[img("scout-opa", D2, "0.20260730.1", False)],
    )
    assert m.resolve("hl7-transformer").endswith(f"@{D1}")
    assert m.resolve("scout-opa").endswith(f"@{D2}")
    with pytest.raises(KeyError):
        m.resolve("nope")


def test_roundtrip_dumps_loads():
    m = Manifest(
        version="0.20260730.1",
        sourceCommit="abc1234",
        rebuildScope="changed",
        images=[img("hl7-transformer", D1, "0.20260730.1", True)],
        charts=[],
        predecessor=Predecessor(version="0.20260729.9", sourceCommit="def5678"),
    )
    assert Manifest.loads(m.dumps()).to_dict() == m.to_dict()


def test_carry_section_changed_vs_carried():
    previous = [
        img("a", D1, "0.20260729.9", True),
        img("b", D2, "0.20260728.5", True),
    ]
    fresh = [img("a", D3, "0.20260730.1", False)]  # 'a' rebuilt this run
    out = {x.name: x for x in carry_section(previous, fresh, ["a", "b"], build_version="0.20260730.1")}

    # 'a' changed this build: fresh digest, stamped changed + producedByBuild=this build
    assert out["a"].digest == D3
    assert out["a"].changedThisBuild is True
    assert out["a"].producedByBuild == "0.20260730.1"
    # 'b' unchanged: carried at its old digest + producing build, marked not-changed
    assert out["b"].digest == D2
    assert out["b"].changedThisBuild is False
    assert out["b"].producedByBuild == "0.20260728.5"


def test_carry_section_fails_closed_on_missing_predecessor_entry():
    # 'b' did not change this build but is absent from the predecessor -> must rebuild.
    with pytest.raises(ValueError, match="absent from the predecessor"):
        carry_section([img("a", D1, "0.20260729.9", True)], [], ["a", "b"], build_version="0.20260730.1")


def test_assemble_carries_predecessor_and_bootstraps():
    prev = Manifest(
        version="0.20260729.9",
        sourceCommit="def5678",
        rebuildScope="all",
        images=[img("a", D1, "0.20260729.9", True), img("b", D2, "0.20260729.9", True)],
        charts=[],
    )
    m = assemble(
        version="0.20260730.1",
        source_commit="abc1234",
        rebuild_scope="changed",
        previous=prev,
        fresh_images=[img("a", D3, "0.20260730.1", False)],
        fresh_charts=[],
        all_image_names=["a", "b"],
        all_chart_names=[],
    )
    assert m.predecessor.version == "0.20260729.9"
    assert m.resolve("a").endswith(f"@{D3}")  # rebuilt
    assert m.resolve("b").endswith(f"@{D2}")  # carried forward unchanged
    assert [i.changedThisBuild for i in m.images] == [True, False]


def test_assemble_bootstrap_no_predecessor():
    m = assemble(
        version="0.20260730.1",
        source_commit="abc1234",
        rebuild_scope="all",
        previous=None,
        fresh_images=[img("a", D1, "0.20260730.1", True)],
        fresh_charts=[],
        all_image_names=["a"],
        all_chart_names=[],
    )
    assert m.predecessor is None
    assert m.resolve("a").endswith(f"@{D1}")


def test_assembled_manifest_matches_schema():
    m = assemble(
        version="0.20260730.1",
        source_commit="abc1234def",
        rebuild_scope="changed",
        previous=Manifest(
            version="0.20260729.9",
            sourceCommit="def5678",
            rebuildScope="all",
            images=[img("b", D2, "0.20260729.9", True)],
            charts=[],
        ),
        fresh_images=[img("a", D1, "0.20260730.1", False)],
        fresh_charts=[],
        all_image_names=["a", "b"],
        all_chart_names=[],
    )
    validate(m.to_dict())  # raises if the wire shape drifts from schema.json


def test_schema_rejects_bad_digest():
    import jsonschema

    m = Manifest(
        version="0.20260730.1",
        sourceCommit="abc1234",
        rebuildScope="changed",
        images=[img("a", "sha256:not-a-real-digest", "0.20260730.1", True)],
        charts=[],
    )
    with pytest.raises(jsonschema.ValidationError):
        validate(m.to_dict())
