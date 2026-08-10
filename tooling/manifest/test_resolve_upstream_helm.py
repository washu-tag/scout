"""Offline tests for resolve_upstream_helm. No network (no helm lookup here)."""

import pytest

from resolve_upstream_helm import resolve_helm


def test_resolve_helm_substitutes_chart_version(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("temporal_version: '~1.2.0'\nopen_webui_helm_chart_version: ~15.2.0\n")
    m = tmp_path / "upstream-images-helm.txt"
    m.write_text(
        "# header comment\n"
        "\n"
        "temporalio/admin-tools https://temporal.example/ temporal temporal_version\n"
        "ghcr.io/open-webui/open-webui https://owui.example/ open-webui open_webui_helm_chart_version\n"
    )
    assert resolve_helm(str(m), str(v)) == [
        ("temporalio/admin-tools", "https://temporal.example/", "temporal", "~1.2.0"),
        ("ghcr.io/open-webui/open-webui", "https://owui.example/", "open-webui", "~15.2.0"),
    ]


def test_resolve_helm_raises_on_missing_var(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("other: 1\n")
    m = tmp_path / "map.txt"
    m.write_text("repo/x https://h.example/ chart missing_var\n")
    with pytest.raises(ValueError, match="missing_var"):
        resolve_helm(str(m), str(v))


def test_resolve_helm_raises_on_empty_var(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("empty_ver:\n")
    m = tmp_path / "map.txt"
    m.write_text("repo/x https://h.example/ chart empty_ver\n")
    with pytest.raises(ValueError, match="empty"):
        resolve_helm(str(m), str(v))


def test_resolve_helm_raises_on_malformed_mapping_line(tmp_path):
    v = tmp_path / "versions.yaml"
    v.write_text("some_var: 1\n")
    m = tmp_path / "map.txt"
    m.write_text("repo/x https://h.example/ some_var\n")  # 3 fields, not 4
    with pytest.raises(ValueError, match="image-repo"):
        resolve_helm(str(m), str(v))
