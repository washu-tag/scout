#!/usr/bin/env python3
"""Generate the ``cluster-vars`` ConfigMap for the Phase 3 GitOps deploy lane (ADR 0031).

Flux Kustomizations under ``deploy/`` substituteFrom this ConfigMap under
StrictPostBuildSubstitutions, so every key in ``deploy/required-vars.txt`` must be
present. Reads a site's flat values source and emits the ConfigMap, computing
derived keys the way the Ansible roles do so both lanes stay byte-identical.

``--values`` is a JSON object mirroring the Ansible variable shape (mostly flat;
the four nested resource/parameter dicts are flattened here, plus a few Ansible
intermediates read only to feed composites). See fixtures/cluster-vars.values.json.

Derived keys mirror these Ansible sources (verified against the cited files):
sizing via jvm_memory_to_k8s (hl7-transformer request+limit multipliers per
tasks/deploy.yaml, NOT the stale defaults); postgres_parameters.* /
postgres_resources_default.* / hl7log_extractor_resources_default.* flattenings;
and the string composites (hive_metastore_endpoint[_readonly], delta_lake_path,
scratch_path, hl7_path, keycloak_realm_url, keycloak_internal_url,
trino_rw_endpoint_host).

Fail closed: emitted ``data`` key set must equal ``deploy/required-vars.txt``
EXACTLY; any missing/extra key exits non-zero. Dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
# Import the pure jvm_memory_to_k8s from the Ansible filter plugin rather than porting
# it: the plugin imports only `re` (FilterModule is just a wrapper class), so importing
# the function pulls in no Ansible -- and both lanes stay one implementation.
sys.path.insert(0, str(_REPO / "ansible" / "filter_plugins"))
from jvm_memory import jvm_memory_to_k8s  # noqa: E402

DEFAULT_REQUIRED = _REPO / "deploy" / "required-vars.txt"


def load_required(path) -> list:
    """Ordered list of required var names (blank lines and # comments dropped)."""
    return [
        ln.strip()
        for ln in Path(path).read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def derive(values: dict) -> dict:
    """Keys the Ansible roles compute rather than read straight from vars.

    Raises KeyError (fail closed) if any derivation input is absent -- an
    incomplete values file must never render a partial ConfigMap.
    """
    out = {}

    # sizing: JVM heap -> K8s memory (multipliers per the Ansible tasks/templates,
    # NOT the stale defaults comments)
    out["hl7_transformer_memory_request"] = jvm_memory_to_k8s(
        values["hl7_transformer_spark_memory"], 1
    )
    out["hl7_transformer_memory_limit"] = jvm_memory_to_k8s(
        values["hl7_transformer_spark_memory"], 4
    )

    # postgres_parameters.* -> postgres_<param> (tuple keeps names auditable)
    pp = values["postgres_parameters"]
    for param in (
        "max_connections",
        "shared_buffers",
        "effective_cache_size",
        "maintenance_work_mem",
        "wal_buffers",
        "default_statistics_target",
        "work_mem",
        "min_wal_size",
        "max_wal_size",
    ):
        out["postgres_" + param] = str(pp[param])

    # postgres_resources_default.{requests,limits}.{cpu,memory}
    pr = values["postgres_resources_default"]
    out["postgres_cpu_request"] = str(pr["requests"]["cpu"])
    out["postgres_cpu_limit"] = str(pr["limits"]["cpu"])
    out["postgres_memory_request"] = str(pr["requests"]["memory"])
    out["postgres_memory_limit"] = str(pr["limits"]["memory"])

    # hl7log_extractor_resources_default.{requests,limits}.{cpu,memory}
    hr = values["hl7log_extractor_resources_default"]
    out["hl7log_extractor_resources_requests_cpu"] = str(hr["requests"]["cpu"])
    out["hl7log_extractor_resources_requests_memory"] = str(hr["requests"]["memory"])
    out["hl7log_extractor_resources_limits_cpu"] = str(hr["limits"]["cpu"])
    out["hl7log_extractor_resources_limits_memory"] = str(hr["limits"]["memory"])

    # string composites (reproduced from the cited Ansible definitions)
    out["hive_metastore_endpoint"] = "thrift://{}.{}:9083".format(
        values["hive_metastore_instance"], values["hive_namespace"]
    )
    out["hive_metastore_endpoint_readonly"] = "thrift://{}.{}:9083".format(
        values["hive_metastore_instance_readonly"], values["hive_namespace"]
    )
    out["delta_lake_path"] = "s3a://{}/delta".format(values["lake_bucket"])
    out["scratch_path"] = "s3://{}".format(values["scratch_bucket"])
    out["hl7_path"] = "s3://{}/hl7".format(values["lake_bucket"])
    out["keycloak_realm_url"] = "https://{}.{}/realms/{}".format(
        values["keycloak_subdomain"],
        values["server_hostname"],
        values["keycloak_realm_name"],
    )
    out["keycloak_internal_url"] = "http://keycloak-service.{}:8080".format(
        values["keycloak_namespace"]
    )
    out["trino_rw_endpoint_host"] = "{}.{}".format(
        values["trino_rw_release_name"], values["trino_rw_namespace"]
    )

    return out


def build(values: dict, required: list) -> dict:
    """Full cluster-vars data: derived keys + pass-through of every other
    required var straight from the input (stringified, ConfigMap data is strings).
    """
    out = derive(values)
    for key in required:
        if key in out:
            continue  # derived; don't let a stray input shadow it
        if key in values:
            out[key] = str(values[key])
    return out


def check(data: dict, required: list) -> tuple:
    """(missing, extra) between the built data and the required-vars set."""
    have = set(data)
    want = set(required)
    return sorted(want - have), sorted(have - want)


def _yaml_double_quoted(value) -> str:
    """YAML double-quoted scalar for an arbitrary string (endpoint values contain
    ``://``); backslashes and quotes are escaped defensively."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"{}"'.format(s)


def render_configmap(data: dict, name: str, namespace: str = "") -> str:
    """Render the cluster-vars ConfigMap (keys sorted, like required-vars.txt)."""
    lines = [
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        "  name: {}".format(name),
    ]
    if namespace:
        lines.append("  namespace: {}".format(namespace))
    lines.append("data:")
    for key in sorted(data):
        lines.append("  {}: {}".format(key, _yaml_double_quoted(data[key])))
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--values", required=True, help="site values source (JSON)")
    ap.add_argument(
        "--required-vars",
        default=str(DEFAULT_REQUIRED),
        help="path to deploy/required-vars.txt (default: repo copy)",
    )
    ap.add_argument("--name", default="cluster-vars", help="ConfigMap metadata.name")
    ap.add_argument("--namespace", default="", help="optional metadata.namespace")
    ap.add_argument("-o", "--output", default="", help="output path (default: stdout)")
    args = ap.parse_args(argv)

    values = json.loads(Path(args.values).read_text())
    required = load_required(args.required_vars)

    try:
        data = build(values, required)
    except KeyError as exc:
        sys.stderr.write("missing derivation input in --values: {}\n".format(exc))
        raise SystemExit(1)

    missing, extra = check(data, required)
    if missing or extra:
        sys.stderr.write("cluster-vars key set does not match required-vars.txt:\n")
        for k in missing:
            sys.stderr.write("  MISSING (declared but not produced): {}\n".format(k))
        for k in extra:
            sys.stderr.write("  EXTRA (produced but not declared): {}\n".format(k))
        raise SystemExit(1)

    text = render_configmap(data, args.name, args.namespace)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
