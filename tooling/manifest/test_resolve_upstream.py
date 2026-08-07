"""Offline tests for resolve_upstream. No network."""

import pytest

from resolve_upstream import load_versions, resolve


def test_load_versions_parses_flat_pairs(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text(
        "# a comment\n"
        "\n"
        "opa_image_tag: 1.16.2\n"
        "helm_diff_plugin_version: 'v3.15.10'\n"  # quoted value
        "orthanc_version: latest  # trailing note\n"  # inline comment
        "nested:\n"
        "  child: ignored\n"  # indented -> not a top-level pair
    )
    out = load_versions(str(v))
    assert out["opa_image_tag"] == "1.16.2"
    assert out["helm_diff_plugin_version"] == "v3.15.10"  # quotes stripped
    assert out["orthanc_version"] == "latest"  # inline comment stripped
    assert "child" not in out  # nested key skipped


def test_resolve_maps_repo_and_var_to_ref(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("hive_image_tag: 3.1.3-e.15\nopa_image_tag: 1.16.2\n")
    m = tmp_path / "upstream-images.txt"
    m.write_text(
        "# header comment\n"
        "\n"
        "starburstdata/hive hive_image_tag\n"
        "openpolicyagent/opa opa_image_tag\n"
    )
    assert resolve(str(m), str(v)) == [
        "starburstdata/hive:3.1.3-e.15",
        "openpolicyagent/opa:1.16.2",
    ]


def test_resolve_raises_on_missing_var(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("other: 1\n")
    m = tmp_path / "map.txt"
    m.write_text("repo/x missing_var\n")
    with pytest.raises(ValueError, match="missing_var"):
        resolve(str(m), str(v))


def test_resolve_raises_on_empty_var(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("empty_tag:\n")
    m = tmp_path / "map.txt"
    m.write_text("repo/x empty_tag\n")
    with pytest.raises(ValueError, match="empty"):
        resolve(str(m), str(v))


def test_resolve_raises_on_malformed_mapping_line(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("t: 1\n")
    m = tmp_path / "map.txt"
    m.write_text("only-one-token\n")
    with pytest.raises(ValueError, match="version-var"):
        resolve(str(m), str(v))
