"""Tests for stamp_config: every placeholder in a deploy/ copy gets stamped,
both hl7-transformer image literals move, and an absent haul component fails closed.
"""

import hashlib
import shutil
from collections import Counter
from pathlib import Path

import pytest
import yaml

from stamp_config import (
    StampError,
    compute_config_hash,
    parse_haul,
    stamp_tree,
    verify_clean,
)

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
HAUL = FIX / "haul.yaml"
REALM = FIX / "realm.json"
DEPLOY = HERE.parents[1] / "deploy"

# Every version: '0.0.0' placeholder in deploy/base maps to one of these charts.
EXPECTED_CHART_PLACEHOLDERS = {
    "temporal-bootstrap",
    "hl7log-extractor",
    "hl7-transformer",
    "scout-opa",
    "hive-metastore",
    "scout-dashboards",
    "keycloak-config-cli",
    "keycloak-fragment-reconciler",
    "launchpad",
}

# Every washu-tag values.image in deploy/base, by owning component. Counted,
# because a component may carry more than one (superset: server + dashboards).
EXPECTED_IMAGE_VALUES_TAGS = {
    "hl7-transformer": 1,
    "hl7log-extractor": 1,
    "keycloak-fragment-reconciler": 1,
    "superset": 2,
    "launchpad": 1,
}

# Every washu-tag image literal written inline rather than through values.image.
EXPECTED_INLINE_IMAGES = {
    "hl7-transformer": 1,
    "keycloak": 1,
}


@pytest.fixture
def haul():
    return parse_haul(HAUL)


@pytest.fixture
def copy_deploy(tmp_path):
    dst = tmp_path / "deploy"
    shutil.copytree(DEPLOY, dst)
    return dst


def test_every_placeholder_stamped_and_clean(haul, copy_deploy):
    images, charts = haul
    ch = compute_config_hash(REALM)
    stamps = stamp_tree(copy_deploy, images, charts, ch)

    # No residual placeholder anywhere in the stamped copy.
    assert verify_clean(copy_deploy) == []

    # Chart references: exactly the expected set, each pinned to its haul digest.
    chart_stamps = [s for s in stamps if s.kind == "chart-digest"]
    assert {s.name for s in chart_stamps} == EXPECTED_CHART_PLACEHOLDERS
    for s in chart_stamps:
        assert s.tag == charts[s.name]

    # Images: exactly the expected components, each pinned to its haul tag.
    # verify_clean only sees a placeholder left behind, never one that vanished
    # (a dropped `tag:`, a renamed repository), which ships that component
    # frozen at whatever the chart defaults to -- so the set is counted here.
    for kind, expected in (
        ("image-values-tag", EXPECTED_IMAGE_VALUES_TAGS),
        ("image-inline", EXPECTED_INLINE_IMAGES),
    ):
        img_stamps = [s for s in stamps if s.kind == kind]
        assert Counter(s.name for s in img_stamps) == expected, kind
        for s in img_stamps:
            assert s.tag == images[s.name]

    # config-hash stamped to the truncated realm sha256.
    ch_stamps = [s for s in stamps if s.kind == "config-hash"]
    assert len(ch_stamps) == 1
    assert ch_stamps[0].tag == ch

    # No stamp kind beyond the ones counted above, so the totals add up to the
    # whole tree.
    assert {s.kind for s in stamps} == {
        "chart-digest",
        "config-hash",
        "image-values-tag",
        "image-inline",
    }
    assert len(stamps) == (
        len(EXPECTED_CHART_PLACEHOLDERS)
        + sum(EXPECTED_IMAGE_VALUES_TAGS.values())
        + sum(EXPECTED_INLINE_IMAGES.values())
        + 1
    )


def test_hl7_transformer_both_image_literals_move(haul, copy_deploy):
    """values.image AND the initContainer literal are stamped in lockstep."""
    images, charts = haul
    stamps = stamp_tree(copy_deploy, images, charts, compute_config_hash(REALM))
    hl7t = [
        s
        for s in stamps
        if s.name == "hl7-transformer"
        and s.kind in ("image-values-tag", "image-inline")
    ]
    assert len(hl7t) == 2
    assert {s.kind for s in hl7t} == {"image-values-tag", "image-inline"}
    assert all(s.tag == images["hl7-transformer"] for s in hl7t)

    # And the initContainer literal is really rewritten in the file on disk.
    text = (copy_deploy / "base" / "extractor" / "resources.yaml").read_text()
    assert "ghcr.io/washu-tag/hl7-transformer:latest" not in text
    assert "ghcr.io/washu-tag/hl7-transformer:" + images["hl7-transformer"] in text


def test_keycloak_and_superset_images_stamped(haul, copy_deploy):
    images, charts = haul
    stamps = stamp_tree(copy_deploy, images, charts, compute_config_hash(REALM))

    # keycloak CR inline image keyed by repo identity, not the concrete tag
    kc = [s for s in stamps if s.name == "keycloak"]
    assert [s.kind for s in kc] == ["image-inline"]
    assert kc[0].tag == images["keycloak"]
    kc_text = (
        copy_deploy / "base" / "keycloak" / "instance" / "resources.yaml"
    ).read_text()
    assert "ghcr.io/washu-tag/keycloak:" + images["keycloak"] in kc_text

    # superset appears in both server and dashboards
    ss = [s for s in stamps if s.name == "superset"]
    assert len(ss) == 2
    assert all(s.kind == "image-values-tag" and s.tag == images["superset"] for s in ss)


def test_config_hash_mirrors_ansible_formula():
    """8-char truncated sha256 of the realm bytes (roles/keycloak configure.yaml)."""
    expected = hashlib.sha256(REALM.read_bytes()).hexdigest()[:8]
    got = compute_config_hash(REALM)
    assert got == expected
    assert len(got) == 8
    # empty-content fallback is deterministic and non-zero.
    assert compute_config_hash("") == hashlib.sha256(b"").hexdigest()[:8]


def test_haul_preserves_image_and_chart_digests(haul):
    images, charts = haul
    assert all("@sha256:" in reference for reference in images.values())
    assert all("@sha256:" in reference for reference in charts.values())


def test_scout_chart_sources_pin_haul_bytes_in_the_existing_owner(haul, copy_deploy):
    images, charts = haul
    stamp_tree(copy_deploy, images, charts, "12345678")
    sources = []
    releases = []
    for path in copy_deploy.rglob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "OCIRepository" and doc["metadata"][
                "name"
            ].startswith("scout-chart-"):
                sources.append(doc)
            if doc.get("kind") == "HelmRelease" and "chartRef" in doc.get("spec", {}):
                releases.append(doc)
    assert len(sources) == len(EXPECTED_CHART_PLACEHOLDERS)
    for source in sources:
        chart = source["metadata"]["labels"]["scout.xnat.org/chart"]
        assert source["spec"]["url"] == "oci://ghcr.io/washu-tag/charts/" + chart
        assert source["spec"]["ref"] == {"digest": charts[chart].split("@", 1)[1]}
        assert source["spec"]["layerSelector"] == {
            "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
            "operation": "copy",
        }
    identities = {(s["metadata"]["namespace"], s["metadata"]["name"]) for s in sources}
    assert len(releases) == len(EXPECTED_CHART_PLACEHOLDERS)
    for release in releases:
        ref = release["spec"]["chartRef"]
        assert ref["kind"] == "OCIRepository"
        assert (ref["namespace"], ref["name"]) in identities
        assert "chart" not in release["spec"]


def test_mutating_chart_tag_cannot_retarget_stamped_config(haul, copy_deploy):
    images, charts = haul
    stamp_tree(copy_deploy, images, charts, "12345678")
    owner = copy_deploy / "base/hive/metastore/helmrepository.yaml"
    docs = list(yaml.safe_load_all(owner.read_text()))
    source = next(doc for doc in docs if doc["kind"] == "OCIRepository")
    assert source["spec"]["ref"] == {
        "digest": charts["hive-metastore"].split("@", 1)[1]
    }
    # Negative control: changing the source to select a same-version tag is
    # rejected even though the HR points to an OCIRepository by name.
    source["spec"]["ref"] = {"tag": charts["hive-metastore"].split("@", 1)[0]}
    owner.write_text(yaml.safe_dump_all(docs))
    assert any(
        "chart source is not digest-pinned" in p for p in verify_clean(copy_deploy)
    )


def test_missing_native_chart_source_fails_closed(haul, copy_deploy):
    images, charts = haul
    stamp_tree(copy_deploy, images, charts, "12345678")
    owner = copy_deploy / "base/hive/metastore/helmrepository.yaml"
    docs = [
        doc
        for doc in yaml.safe_load_all(owner.read_text())
        if doc["kind"] != "OCIRepository"
    ]
    owner.write_text(yaml.safe_dump_all(docs))
    assert any("chartRef has no digest-pinned" in p for p in verify_clean(copy_deploy))


@pytest.mark.parametrize("reference", ["0.20261007.1", "0.20261007.1@sha256:short"])
def test_verify_clean_rejects_mutable_or_malformed_image_refs(tmp_path, reference):
    (tmp_path / "image.yaml").write_text(
        "image:\n  repository: ghcr.io/washu-tag/launchpad\n" f"  tag: '{reference}'\n"
    )
    assert any("not digest-pinned" in problem for problem in verify_clean(tmp_path))


def test_absent_component_fails_closed(haul, copy_deploy):
    """A placeholder whose chart is missing from the haul is a hard error."""
    images, charts = haul
    broken = dict(charts)
    del broken["scout-opa"]  # opa base still has a version: '0.0.0'
    with pytest.raises(StampError, match="scout-opa.*no haul component"):
        stamp_tree(copy_deploy, images, broken, compute_config_hash(REALM))


def test_absent_image_component_fails_closed(haul, copy_deploy):
    images, charts = haul
    broken = dict(images)
    del broken["keycloak"]  # keycloak CR inline image no longer resolves
    with pytest.raises(StampError, match="keycloak.*no haul component"):
        stamp_tree(copy_deploy, broken, charts, compute_config_hash(REALM))


def test_key_order_independent_stamp(tmp_path, haul):
    """A values.image with `tag:` BEFORE `repository:` must still stamp (Kate #639):
    key order carries no YAML meaning, so the stamper can't depend on it."""
    images, charts = haul
    base = tmp_path / "base" / "x"
    base.mkdir(parents=True)
    (base / "resources.yaml").write_text(
        "spec:\n"
        "  values:\n"
        "    image:\n"
        "      tag: latest\n"  # tag BEFORE repository -- the reordered case
        "      repository: ghcr.io/washu-tag/hl7-transformer\n"
    )
    stamps = stamp_tree(tmp_path, images, charts, "0")
    assert any(
        s.kind == "image-values-tag" and s.name == "hl7-transformer" for s in stamps
    )
    text = (base / "resources.yaml").read_text()
    assert "tag: latest" not in text
    assert "tag: '" + images["hl7-transformer"] + "'" in text
    assert verify_clean(tmp_path) == []


def test_verify_clean_catches_reordered_residual(tmp_path):
    """verify_clean must flag a washu :latest even when tag precedes repository,
    so a stamp miss can never pass the fail-closed gate."""
    base = tmp_path / "base" / "y"
    base.mkdir(parents=True)
    (base / "r.yaml").write_text(
        "spec:\n"
        "  values:\n"
        "    image:\n"
        "      tag: latest\n"
        "      repository: ghcr.io/washu-tag/superset\n"
    )
    problems = verify_clean(tmp_path)
    assert any("latest" in p for p in problems)


def test_sequence_first_key_inline_image(tmp_path, haul):
    """An initContainer authored image-first (`- image:`) still stamps -- key/line
    placement carries no meaning (round-3 review)."""
    images, charts = haul
    base = tmp_path / "base" / "z"
    base.mkdir(parents=True)
    (base / "r.yaml").write_text(
        "spec:\n"
        "  values:\n"
        "    initContainers:\n"
        "      - image: ghcr.io/washu-tag/hl7-transformer:latest\n"  # `- image:` first
        "        name: wait\n"
    )
    stamps = stamp_tree(tmp_path, images, charts, "0")
    assert any(s.kind == "image-inline" and s.name == "hl7-transformer" for s in stamps)
    text = (base / "r.yaml").read_text()
    assert "hl7-transformer:latest" not in text
    assert "ghcr.io/washu-tag/hl7-transformer:" + images["hl7-transformer"] in text
    assert verify_clean(tmp_path) == []


def test_unparseable_yaml_fails_closed(tmp_path, haul):
    """A file the parser rejects is a hard error, never a silent skip -- the gate
    must fail closed (round-3 review)."""
    images, charts = haul
    base = tmp_path / "base" / "bad"
    base.mkdir(parents=True)
    (base / "r.yaml").write_text("note: scout: analytics\nversion: '0.0.0'\n")
    with pytest.raises(StampError, match="not valid YAML"):
        stamp_tree(tmp_path, images, charts, "0")


def test_flow_mapping_fails_closed(tmp_path, haul):
    """A flow mapping puts several values on the parent's line, so rewriting the
    whole line would corrupt it -- fail closed, don't stamp (Kate #639)."""
    images, charts = haul
    base = tmp_path / "base" / "flow"
    base.mkdir(parents=True)
    original = "spec:\n  values:\n    image: { repository: ghcr.io/washu-tag/hl7-transformer, tag: latest }\n"
    (base / "r.yaml").write_text(original)
    with pytest.raises(StampError):
        stamp_tree(tmp_path, images, charts, "0")
    # left untouched, not corrupted into `image: '<tag>'`
    assert (base / "r.yaml").read_text() == original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
