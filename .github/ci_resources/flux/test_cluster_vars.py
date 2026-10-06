"""CI inherits shared fixture changes while preserving intentional overrides."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

import cluster_vars


def test_overlay_contains_only_intentional_differences():
    base = json.loads((cluster_vars.ROOT / cluster_vars.BASE).read_text())
    overlay = json.loads((cluster_vars.ROOT / cluster_vars.OVERLAY).read_text())

    def check(base, override):
        for key, value in override.items():
            if key == "_comment":
                continue
            if isinstance(value, dict):
                assert value, f"empty CI override: {key}"
                check(base[key], value)
            else:
                assert value != base[key], f"duplicated shared default: {key}"

    check(base, overlay)


def test_shared_fixture_changes_flow_through_nested_ci_overrides(tmp_path):
    base = json.loads((cluster_vars.ROOT / cluster_vars.BASE).read_text())
    overlay = json.loads((cluster_vars.ROOT / cluster_vars.OVERLAY).read_text())
    base["timezone"] = "Etc/UTC"
    base["postgres_resources_default"]["limits"]["cpu"] = "3"
    base["hl7log_extractor_resources_default"]["requests"]["cpu"] = "200m"
    base["postgres_parameters"]["future_parameter"] = "on"
    for relative, value in ((cluster_vars.BASE, base), (cluster_vars.OVERLAY, overlay)):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    values = cluster_vars.load_values(tmp_path)
    assert values["timezone"] == "Etc/UTC"
    assert values["postgres_resources_default"] == {
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {"cpu": "3", "memory": "1Gi"},
    }
    assert values["hl7log_extractor_resources_default"]["requests"] == {
        "cpu": "200m",
        "memory": "512Mi",
    }
    assert values["postgres_parameters"]["future_parameter"] == "on"
    assert values["lake_bucket"] == "ci-lake"
    assert "_comment" not in values


def test_merging_does_not_mutate_shared_values():
    base = {"resources": {"memory": "1Gi", "cpu": "100m"}}
    result = cluster_vars.merge_values(base, {"resources": {"memory": "512Mi"}})
    result["resources"]["cpu"] = "200m"
    assert base == {"resources": {"memory": "1Gi", "cpu": "100m"}}


@pytest.mark.parametrize(
    "overlay",
    [{"typo": "x"}, {"resources": {"typo": "x"}}, {"resources": "512Mi"}],
)
def test_malformed_overlay_cannot_silently_drop_values(overlay):
    with pytest.raises(ValueError, match="CI cluster-vars override"):
        cluster_vars.merge_values({"resources": {"memory": "1Gi"}}, overlay)


def test_cli_uses_shared_loader_and_runtime_ingest_path(tmp_path):
    output = tmp_path / "merged.json"
    ingest = tmp_path / "checkout/tests/ingest/staging_test_data"
    subprocess.run(
        [
            sys.executable,
            str(Path(cluster_vars.__file__)),
            "--extractor-data-dir",
            str(ingest),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        check=True,
    )
    assert json.loads(output.read_text()) == cluster_vars.load_values(
        extractor_data_dir=ingest
    )
    assert json.loads(output.read_text())["extractor_data_dir"] == str(ingest)
