"""Tests for gen_cluster_vars: derived-value parity + fail-closed key coverage."""

import json
import re
from pathlib import Path

import pytest

from gen_cluster_vars import (
    build,
    check,
    derive,
    load_required,
    main,
    render_configmap,
)

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "cluster-vars.values.json"
REQUIRED = HERE.parents[1] / "deploy" / "required-vars.txt"


@pytest.fixture
def values():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def required():
    return load_required(REQUIRED)


def test_sizing_computations_exact(values):
    """The 2 jvm_memory_to_k8s sizings, to exact strings."""
    d = derive(values)
    # hl7_transformer_spark_memory=1G: request x1, limit x4
    assert d["hl7_transformer_memory_request"] == "1Gi"
    assert d["hl7_transformer_memory_limit"] == "4Gi"


def test_nested_flattenings_exact(values):
    """The 17 nested flattenings (9 postgres_parameters + 4 postgres_resources
    + 4 hl7log_extractor_resources), to exact strings."""
    d = derive(values)
    assert d["postgres_max_connections"] == "100"
    assert d["postgres_shared_buffers"] == "256MB"
    assert d["postgres_effective_cache_size"] == "1GB"
    assert d["postgres_maintenance_work_mem"] == "64MB"
    assert d["postgres_wal_buffers"] == "4MB"
    assert d["postgres_default_statistics_target"] == "100"
    assert d["postgres_work_mem"] == "4MB"
    assert d["postgres_min_wal_size"] == "1GB"
    assert d["postgres_max_wal_size"] == "4GB"

    assert d["postgres_cpu_request"] == "250m"
    assert d["postgres_cpu_limit"] == "2"
    assert d["postgres_memory_request"] == "1Gi"
    assert d["postgres_memory_limit"] == "2Gi"

    assert d["hl7log_extractor_resources_requests_cpu"] == "100m"
    assert d["hl7log_extractor_resources_requests_memory"] == "1Gi"
    assert d["hl7log_extractor_resources_limits_cpu"] == "2"
    assert d["hl7log_extractor_resources_limits_memory"] == "2Gi"


def test_string_composites_exact(values):
    """The 8 string composites, reproduced from the Ansible definitions."""
    d = derive(values)
    assert d["hive_metastore_endpoint"] == "thrift://hive-metastore.scout-data:9083"
    assert (
        d["hive_metastore_endpoint_readonly"]
        == "thrift://hive-metastore-readonly.scout-data:9083"
    )
    assert d["delta_lake_path"] == "s3a://lake/delta"
    assert d["scratch_path"] == "s3://scratch"
    assert d["hl7_path"] == "s3://lake/hl7"
    assert d["keycloak_realm_url"] == "https://keycloak.scout.example.edu/realms/scout"
    assert d["keycloak_internal_url"] == "http://keycloak-service.scout-core:8080"
    assert d["trino_rw_endpoint_host"] == "trino-rw.scout-extractor"


def test_output_key_set_equals_required_vars(values, required):
    """Fail-closed contract: built data key set == required-vars.txt EXACTLY."""
    data = build(values, required)
    missing, extra = check(data, required)
    assert missing == [], "declared but not produced: {}".format(missing)
    assert extra == [], "produced but not declared: {}".format(extra)
    assert set(data) == set(required)


def test_rendered_configmap_keys_match_required(values, required, tmp_path):
    """End-to-end via main(): the emitted ConfigMap data keys == required-vars."""
    out = tmp_path / "cluster-vars.yaml"
    main(
        [
            "--values",
            str(FIXTURE),
            "--required-vars",
            str(REQUIRED),
            "-o",
            str(out),
        ]
    )
    text = out.read_text()
    assert "kind: ConfigMap" in text
    assert "name: cluster-vars" in text
    # split on the standalone data: line, not the "data:" inside "metadata:"
    body = text.split("\ndata:\n", 1)[1]
    keys = set(re.findall(r"^  ([A-Za-z0-9_]+):", body, re.MULTILINE))
    assert keys == set(required)


def test_missing_input_fails_closed(values, required):
    """A site values file missing a required direct var must not render partial."""
    broken = dict(values)
    del broken["server_hostname"]  # a keycloak_realm_url derivation input -> KeyError
    with pytest.raises(KeyError):
        build(broken, required)


def test_missing_direct_var_reported(values, required):
    """A required var that is neither derived nor in the input is reported missing."""
    broken = dict(values)
    del broken["timezone"]  # plain pass-through required var
    data = build(broken, required)
    missing, extra = check(data, required)
    assert "timezone" in missing


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
