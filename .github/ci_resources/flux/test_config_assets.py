"""The config source must retain packaged binary inputs to Kustomize generators.

The live auth leg covers source-controller's actual reconciliation. This offline
check covers the producer archive, copied-layer extraction, and real Kustomize
render, with a missing-logo control that reproduces the original build failure.
"""

import base64
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest
import yaml


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def test_config_source_preserves_packaged_generator_assets(tmp_path):
    source = yaml.safe_load((HERE / "site/scout-config-source.yaml").read_text())
    # source-controller v1.9.6's default extract/rearchive uses sourceignore's
    # *.jpg exclusion. Copy bypasses that filter and keeps the producer tarball.
    # https://github.com/fluxcd/source-controller/blob/v1.9.6/internal/controller/ocirepository_controller.go#L1098-L1121
    assert source["spec"]["layerSelector"] == {
        "mediaType": "application/gzip",
        "operation": "copy",
    }
    assert source["spec"]["ref"] == {"digest": "@CONFIG_DIGEST@"}
    assert source["spec"]["verify"]["secretRef"]["name"] == "scout-cosign-pub"
    kustomize = shutil.which("kustomize")
    if not kustomize:
        pytest.skip("kustomize required; validate-deploy installs the pinned CLI")

    # The same layout packaged by both the producer and local artifact workflow.
    archive = tmp_path / "scout-config.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in (
            "base",
            "flux",
            "modes",
            "required-vars.txt",
            "required-secret-values.txt",
        ):
            bundle.add(REPO / "deploy" / name, arcname=name)
    extracted = tmp_path / "copied-layer"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        # This archive was created immediately above from trusted checkout files;
        # explicit regular-file extraction also supports Python before 3.12.
        for member in bundle.getmembers():
            if member.isfile():
                destination = extracted / member.name
                assert destination.resolve().is_relative_to(extracted.resolve())
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(bundle.extractfile(member).read())
    logo = (REPO / "deploy/base/oauth2-proxy/scout.jpg").read_bytes()
    assert (extracted / "base/oauth2-proxy/scout.jpg").read_bytes() == logo

    def render():
        return subprocess.run(
            [kustomize, "build", str(extracted / "base/oauth2-proxy")],
            capture_output=True,
            text=True,
            timeout=30,
        )

    result = render()
    assert result.returncode == 0, result.stderr
    secret = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc["kind"] == "Secret" and doc["metadata"]["name"] == "oauth2-proxy-logo"
    )
    encoded = "".join(secret["data"]["logo.png"].split())
    assert base64.b64decode(encoded, validate=True) == logo

    # Sensitivity check: this is the observed controller-default failure, not a
    # fixture that would keep passing after the generator lost its binary input.
    (extracted / "base/oauth2-proxy/scout.jpg").unlink()
    result = render()
    assert result.returncode != 0
    assert "scout.jpg" in result.stderr
