"""Offline tests for the Hauler content-manifest renderer. No network."""

import pytest

from haul import APIVERSION, render_images

IMG = "ghcr.io/washu-tag/hl7-listener:0.20260803.1@sha256:" + "a" * 64
CHART = "ghcr.io/washu-tag/charts/hl7-listener:0.20260803.1@sha256:" + "b" * 64


def _load(yaml_text):
    """Parse the rendered manifest if PyYAML is available, else skip."""
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(yaml_text)


def test_renders_images_and_charts_under_kind_images():
    out = render_images([IMG, CHART], name="scout")
    assert f"apiVersion: {APIVERSION}" in out
    assert "kind: Images" in out
    assert "  name: scout" in out
    assert f"    - name: {IMG}" in out
    assert f"    - name: {CHART}" in out


def test_order_is_preserved():
    out = render_images([CHART, IMG])
    assert out.index(CHART) < out.index(IMG)


def test_default_name_is_scout():
    assert "  name: scout\n" in render_images([IMG])


def test_unpinned_ref_fails_closed():
    with pytest.raises(ValueError, match="digest-pinned"):
        render_images([IMG, "ghcr.io/washu-tag/keycloak:0.20260803.1"])


def test_duplicate_ref_rejected():
    with pytest.raises(ValueError, match="duplicate refs"):
        render_images([IMG, IMG])


def test_empty_rejected():
    with pytest.raises(ValueError, match="at least one ref"):
        render_images([])


def test_output_is_valid_yaml_with_expected_shape():
    doc = _load(render_images([IMG, CHART], name="scout-build"))
    assert doc["apiVersion"] == APIVERSION
    assert doc["kind"] == "Images"
    assert doc["metadata"]["name"] == "scout-build"
    assert [i["name"] for i in doc["spec"]["images"]] == [IMG, CHART]
    # every entry is digest-pinned
    assert all("@sha256:" in i["name"] for i in doc["spec"]["images"])
