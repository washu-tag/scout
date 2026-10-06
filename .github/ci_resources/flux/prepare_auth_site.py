#!/usr/bin/env python3
"""Add the CI ingress trust bundle and auth-only site patches.

The verified config stays immutable. Patches are applied by a scratch copy of
the trusted checkout's app roots to child Flux Kustomizations, which then patch
their own rendered resources. The public CA is part of the signed site artifact.
"""

import argparse
import base64
import json
from pathlib import Path
import ssl

import yaml


CA_NAME = "ci-ingress-ca"
CA_PATH = "/etc/scout-ci-ca/ca.crt"
ATTRIBUTE_FILTERS = {
    "allowed_facilities": {"column": "sending_facility"},
    "allowed_modalities": {"column": "modality"},
}


def patch(kind, name, operations):
    return {"target": {"kind": kind, "name": name}, "patch": json.dumps(operations)}


def add(path, value):
    return {"op": "add", "path": path, "value": value}


def resources(request_memory, limit_memory, limit_cpu=2):
    return {
        "requests": {"cpu": "100m", "memory": request_memory},
        "limits": {"cpu": limit_cpu, "memory": limit_memory},
    }


def auth_patches():
    """Mirror the small smoke-test fixture, retaining JWT, TLS and network policy."""
    ca_volume = {"name": CA_NAME, "configMap": {"name": CA_NAME}}
    ca_mount = {"name": CA_NAME, "mountPath": "/etc/scout-ci-ca", "readOnly": True}
    trino = [
        add("/spec/values/server/workers", 1),
        add("/spec/values/server/config/query/maxMemory", "0.15GB"),
    ]
    for component in ("coordinator", "worker"):
        prefix = f"/spec/values/{component}"
        trino.extend(
            [
                add(f"{prefix}/jvm/maxHeapSize", "512M"),
                add(f"{prefix}/config/query/maxMemoryPerNode", "0.15GB"),
                add(f"{prefix}/resources", resources("512Mi", "2Gi", 4)),
            ]
        )
    opa_data = {
        "filtered_tables": [
            {"catalog": "delta", "schema": "default", "table": "test_reports"}
        ],
        "baseline_hidden_tables": [
            {
                "catalog": "delta",
                "schema": "default",
                "table": "${report_delta_table_name}_report_patient_mapping",
            },
            {
                "catalog": "delta",
                "schema": "default",
                "table": "${report_delta_table_name}_report_patient_mapping_history",
            },
        ],
        "hidden_tables": [],
        "view_owner_principals": ["trino"],
        "attribute_filters": ATTRIBUTE_FILTERS,
        "masked_columns": ["patient_name", "full_patient_name", "zip_or_postal_code"],
    }
    return {
        "apps-scout": {
            "trino-ro": [patch("HelmRelease", "trino", trino)],
            "hive-metastore": [
                patch(
                    "HelmRelease",
                    "hive-metastore(-readonly)?",
                    [
                        add("/spec/values/resources", resources("256Mi", "512Mi")),
                    ],
                )
            ],
            "keycloak-instance": [
                patch(
                    "Keycloak",
                    "keycloak",
                    [
                        add("/spec/resources", resources("512Mi", "2Gi")),
                    ],
                )
            ],
            "keycloak-realm": [
                patch(
                    "HelmRelease",
                    "keycloak-config-cli",
                    [
                        add("/spec/values/trinoAttributeFilters", ATTRIBUTE_FILTERS),
                        add("/spec/values/resources", resources("256Mi", "1Gi")),
                    ],
                )
            ],
            "opa": [
                patch(
                    "HelmRelease",
                    "scout-opa",
                    [
                        # The tests poll OPA then query Trino; one replica removes the
                        # race where the other replica still holds yesterday's bundle.
                        add("/spec/values/replicaCount", 1),
                        add("/spec/values/data/json", json.dumps(opa_data, indent=2)),
                    ],
                )
            ],
            "valkey": [
                patch(
                    "HelmRelease",
                    "valkey",
                    [
                        add("/spec/values/resources", resources("128Mi", "512Mi", 1)),
                    ],
                )
            ],
            "launchpad": [
                patch(
                    "HelmRelease",
                    "launchpad",
                    [
                        add(
                            "/spec/values/env",
                            [{"name": "NODE_EXTRA_CA_CERTS", "value": CA_PATH}],
                        ),
                        add("/spec/values/volumes", [ca_volume]),
                        add("/spec/values/volumeMounts", [ca_mount]),
                        add("/spec/values/resources", resources("128Mi", "768Mi", 1)),
                    ],
                )
            ],
        },
        "apps-scout-on-prem": {
            "oauth2-proxy": [
                patch(
                    "HelmRelease",
                    "oauth2-proxy",
                    [
                        add(
                            "/spec/values/extraArgs",
                            {
                                "provider-ca-file": CA_PATH,
                                "use-system-trust-store": "true",
                            },
                        ),
                        # Preserve production templates and logo mounts.
                        add("/spec/values/extraVolumes/-", ca_volume),
                        add("/spec/values/extraVolumeMounts/-", ca_mount),
                        add("/spec/values/resources", resources("64Mi", "256Mi", 1)),
                    ],
                )
            ],
            "edge-on-prem": [
                patch(
                    "Middleware",
                    "oauth2-proxy-auth",
                    [
                        add(
                            "/spec/forwardAuth/tls",
                            {"caSecret": CA_NAME, "insecureSkipVerify": False},
                        ),
                    ],
                )
            ],
        },
    }


def prepare(site, ca_path, roots_path, output):
    if roots_path.resolve() == output.resolve():
        raise ValueError(
            "output must be a scratch copy, not the tracked roots template"
        )
    pem = ca_path.read_text()
    # Never copy a key, even if accidentally supplied a combined PEM file.
    if "PRIVATE KEY" in pem or pem.count("-----BEGIN CERTIFICATE-----") != 1:
        raise ValueError("--ca must contain one public certificate and no private key")
    ssl.PEM_cert_to_DER_cert(pem)
    roots = [doc for doc in yaml.safe_load_all(roots_path.read_text()) if doc]
    expected = {"apps-scout": "./flux", "apps-scout-on-prem": "./modes/on-prem"}
    if len(roots) != 2 or {doc["metadata"]["name"] for doc in roots} != set(expected):
        raise ValueError("expected the two auth application roots")
    patches = auth_patches()
    for root in roots:
        name = root["metadata"]["name"]
        if (
            root["kind"] != "Kustomization"
            or root["spec"]["path"] != expected[name]
            or root["spec"]["sourceRef"]
            != {"kind": "OCIRepository", "name": "scout-config"}
        ):
            raise ValueError("unexpected application root source or path")
        for child, child_patches in patches[name].items():
            root["spec"].setdefault("patches", []).append(
                patch("Kustomization", child, [add("/spec/patches", child_patches)])
            )

    trust = []
    for namespace in ("scout-core", "kube-system"):
        trust.append(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": CA_NAME, "namespace": namespace},
                "data": {"ca.crt": pem},
            }
        )
    # Traefik's CRD accepts caSecret; its ca.crt is public, not a credential.
    trust.append(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": CA_NAME, "namespace": "kube-system"},
            "data": {"ca.crt": base64.b64encode(pem.encode()).decode()},
        }
    )
    kustomization_path = site / "kustomization.yaml"
    kustomization = yaml.safe_load(kustomization_path.read_text())
    if "ci-ingress-ca.yaml" in kustomization.get("resources", []):
        raise ValueError("auth site is already prepared")
    kustomization.setdefault("resources", []).append("ci-ingress-ca.yaml")
    (site / "ci-ingress-ca.yaml").write_text(yaml.safe_dump_all(trust, sort_keys=False))
    kustomization_path.write_text(yaml.safe_dump(kustomization, sort_keys=False))
    output.write_text(yaml.safe_dump_all(roots, sort_keys=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("site", "ca", "roots", "output"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.site, args.ca, args.roots, args.output)


if __name__ == "__main__":
    main()
