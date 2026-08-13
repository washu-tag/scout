#!/usr/bin/env python3
"""Generate the ``cluster-vars`` ConfigMap for the Phase 3 GitOps deploy lane.

The Flux Kustomizations under ``deploy/`` reference a ``cluster-vars`` ConfigMap
via ``postBuild.substituteFrom`` and run with StrictPostBuildSubstitutions, so
EVERY key in ``deploy/required-vars.txt`` must be present or the reconcile fails
(ADR 0031). This generator reads a site's flat values source and emits that
ConfigMap, computing the derived keys exactly the way the Ansible roles do so the
GitOps lane and the Ansible lane stay byte-identical.

Input format (``--values``): a JSON object mirroring the Ansible variable shape.
Mostly flat scalars; the four resource/parameter dicts that the Ansible roles
define nested (``postgres_parameters``, ``postgres_resources_default``,
``hl7log_extractor_resources_default``,
``cassandra_system_logger_resources_default``) stay nested and are flattened
here. A handful of Ansible intermediates that are NOT themselves cluster-vars but
feed a composite (``hive_metastore_instance``[``_readonly``],
``keycloak_subdomain``, ``trino_rw_release_name``, ``trino_rw_namespace``) are
also read from the input. See ``fixtures/cluster-vars.values.json``.

Derived keys and their Ansible sources (verified against the cited files):
  * sizing via jvm_memory_to_k8s (ansible/roles/*/tasks/deploy.yaml,
    extractor templates/hl7-transformer.values.yaml.j2):
      cassandra_memory_request/limit      = cassandra_max_heap * (1, 2)
      elasticsearch_memory_request/limit  = elasticsearch_max_heap * (2, 8)
      hl7_transformer_memory_request/limit= hl7_transformer_spark_memory * (1, 4)
  * postgres_parameters.* / postgres_resources_default.* flatten
    (ansible/roles/postgres/defaults/main.yaml)
  * hl7log_extractor_resources_default.* flatten
    (ansible/roles/extractor/defaults/main.yaml)
  * cassandra_system_logger_resources = the whole
    cassandra_system_logger_resources_default dict as a flow-style JSON scalar
    (ansible/roles/cassandra/defaults/main.yaml + tasks/deploy.yaml)
  * string composites (ansible/roles/scout_common/defaults/main.yaml +
    ansible/roles/extractor/defaults/main.yaml): hive_metastore_endpoint[_readonly],
    delta_lake_path, scratch_path, hl7_path, keycloak_realm_url,
    keycloak_internal_url, trino_rw_endpoint_host.

Fail closed: the emitted ``data`` key set must equal ``deploy/required-vars.txt``
EXACTLY. A missing key (a site forgot a var) or an extra key exits non-zero with
the diff. Dependency-free (stdlib only) so it runs on a CI runner with no install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jvm_memory import jvm_memory_to_k8s

# tooling/deploy/gen_cluster_vars.py -> repo root is two parents up.
DEFAULT_REQUIRED = Path(__file__).resolve().parents[2] / "deploy" / "required-vars.txt"


def load_required(path) -> list:
    """Ordered list of required var names (blank lines and # comments dropped)."""
    return [
        ln.strip()
        for ln in Path(path).read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def derive(values: dict) -> dict:
    """The keys the Ansible roles compute rather than read straight from vars.

    Raises KeyError (fail closed) if any derivation input is absent from
    ``values`` -- an incomplete site values file must never render a partial
    ConfigMap.
    """
    out = {}

    # --- sizing: JVM heap -> K8s memory (multipliers verified against the
    # actual Ansible tasks/templates, NOT the stale defaults comments) ---
    out["cassandra_memory_request"] = jvm_memory_to_k8s(values["cassandra_max_heap"], 1)
    out["cassandra_memory_limit"] = jvm_memory_to_k8s(values["cassandra_max_heap"], 2)
    out["elasticsearch_memory_request"] = jvm_memory_to_k8s(
        values["elasticsearch_max_heap"], 2
    )
    out["elasticsearch_memory_limit"] = jvm_memory_to_k8s(
        values["elasticsearch_max_heap"], 8
    )
    out["hl7_transformer_memory_request"] = jvm_memory_to_k8s(
        values["hl7_transformer_spark_memory"], 1
    )
    out["hl7_transformer_memory_limit"] = jvm_memory_to_k8s(
        values["hl7_transformer_spark_memory"], 4
    )

    # --- postgres_parameters.* -> postgres_<param> (uniform `postgres_` prefix;
    # the tuple keeps every param name visible for audit against the role) ---
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

    # --- postgres_resources_default.{requests,limits}.{cpu,memory} ---
    pr = values["postgres_resources_default"]
    out["postgres_cpu_request"] = str(pr["requests"]["cpu"])
    out["postgres_cpu_limit"] = str(pr["limits"]["cpu"])
    out["postgres_memory_request"] = str(pr["requests"]["memory"])
    out["postgres_memory_limit"] = str(pr["limits"]["memory"])

    # --- hl7log_extractor_resources_default.{requests,limits}.{cpu,memory} ---
    hr = values["hl7log_extractor_resources_default"]
    out["hl7log_extractor_resources_requests_cpu"] = str(hr["requests"]["cpu"])
    out["hl7log_extractor_resources_requests_memory"] = str(hr["requests"]["memory"])
    out["hl7log_extractor_resources_limits_cpu"] = str(hr["limits"]["cpu"])
    out["hl7log_extractor_resources_limits_memory"] = str(hr["limits"]["memory"])

    # --- cassandra_system_logger_resources: the WHOLE default dict as a flow
    # JSON scalar, so `systemLoggerResources: ${cassandra_system_logger_resources}`
    # renders as a valid YAML flow mapping after envsubst. ---
    out["cassandra_system_logger_resources"] = json.dumps(
        values["cassandra_system_logger_resources_default"]
    )

    # --- string composites (reproduced from the cited Ansible definitions) ---
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
            continue  # a derived key; do not let a stray input shadow it
        if key in values:
            out[key] = str(values[key])
    return out


def check(data: dict, required: list) -> tuple:
    """(missing, extra) between the built data and the required-vars set."""
    have = set(data)
    want = set(required)
    return sorted(want - have), sorted(have - want)


def _yaml_double_quoted(value) -> str:
    """A YAML double-quoted scalar for an arbitrary string (values may contain
    ``://``, braces, and the JSON quotes of the system-logger scalar)."""
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
