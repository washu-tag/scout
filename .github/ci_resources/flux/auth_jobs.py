#!/usr/bin/env python3
"""Prepare the existing data-authz jobs from an exact, verified config artifact.

The auth leg omits the ingest deployments, so get its seed image from the
artifact's stamped transformer HelmRelease, never from checkout placeholders.
Reading the one tar member avoids extracting artifact paths into the workspace.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tarfile

import yaml

ROOT = Path(__file__).resolve().parents[3]
RESOURCE = "base/extractor/resources.yaml"
REPOSITORY = "ghcr.io/washu-tag/hl7-transformer"


def transformer_image(archive: Path) -> str:
    with tarfile.open(archive, "r:gz") as bundle:
        members = [
            item
            for item in bundle.getmembers()
            if item.name.removeprefix("./") == RESOURCE
        ]
        if len(members) != 1:
            raise ValueError("config must contain one extractor resource file")
        member = members[0]
        if not member.isfile() or member.size > 1024 * 1024:
            raise ValueError("extractor resources must be a bounded regular file")
        stream = bundle.extractfile(member)
        assert stream is not None
        resources = list(yaml.safe_load_all(stream))

    releases = [
        resource
        for resource in resources
        if isinstance(resource, dict)
        and resource.get("kind") == "HelmRelease"
        and resource.get("metadata", {}).get("name") == "hl7-transformer"
    ]
    if len(releases) != 1:
        raise ValueError("config must contain one transformer HelmRelease")
    image = releases[0]["spec"]["values"]["image"]
    tag = image.get("tag")
    if (
        image.get("repository") != REPOSITORY
        or not isinstance(tag, str)
        or tag.split("@", 1)[0] in ("latest", "0.0.0")
        or not re.fullmatch(
            r"[\w][\w.-]{0,127}@sha256:[0-9a-f]{64}", tag, flags=re.ASCII
        )
    ):
        raise ValueError("config transformer image is not a stamped Scout image")
    return f"{REPOSITORY}:{tag}"


def prepare(archive: Path, output: Path, cluster_vars: Path) -> None:
    fixture = ROOT / ".github/ci_resources/flux"
    values = json.loads(cluster_vars.read_text())
    secrets = json.loads((fixture / "secret-values.json").read_text())
    image = transformer_image(archive)
    bucket = values["lake_bucket"]
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9.-]+", bucket):
        raise ValueError("invalid lake bucket")
    seed = yaml.safe_load((ROOT / "tests/data-authorization/seed/job.yaml").read_text())
    container = seed["spec"]["template"]["spec"]["containers"][0]
    container["image"] = image
    warehouse = [
        env for env in container["env"] if env["name"] == "SPARK_SQL_WAREHOUSE_DIR"
    ]
    if len(warehouse) != 1:
        raise ValueError("seed job must specify one warehouse")
    warehouse[0]["value"] = f"s3a://{bucket}/delta"
    credentials = {
        "KC_ADMIN_PASSWORD": "keycloak_bootstrap_admin_password",
        "SUPERSET_SVC_CLIENT_SECRET": "keycloak_superset_svc_client_secret",
        "REPORT_VIEWER_SVC_CLIENT_SECRET": ("keycloak_report_viewer_svc_client_secret"),
    }
    # Validate every credential before leaving partial output for kubectl.
    if any(
        not isinstance(secrets.get(key), str) or not secrets[key]
        for key in credentials.values()
    ):
        raise ValueError("data-authz credential fixture is incomplete")
    output.mkdir(parents=True, exist_ok=True)
    (output / "seed.json").write_text(json.dumps(seed))
    for env, key in credentials.items():
        path = output / env
        path.write_text(secrets[key])
        path.chmod(0o600)
    print(f"Data-authz seed image from verified config: {image}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("cluster_vars", type=Path)
    args = parser.parse_args()
    prepare(args.archive, args.output, args.cluster_vars)
