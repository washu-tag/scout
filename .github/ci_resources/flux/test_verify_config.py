"""Exercise config verification ordering without registry or cluster access."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".github/ci_resources/flux/verify_config.sh"
DIGEST = "sha256:" + "a" * 64
BUNDLE = "sha256:" + "b" * 64


def verify(tmp_path, *, bundle=True, annotation=BUNDLE, signature_success=True):
    annotations = {
        "org.opencontainers.image.source": "https://github.com/washu-tag/scout",
        "org.opencontainers.image.revision": "a" * 40,
        "org.opencontainers.image.version": "0.20261006.1",
        "io.scout.build.run-id": "123",
        "io.scout.build.run-attempt": "2",
        "io.scout.build.manifest-digest": DIGEST,
    }
    if annotation is not None:
        annotations["io.scout.build.bundle-digest"] = annotation
    manifest = json.dumps({"schemaVersion": 2, "annotations": annotations}).encode()
    source = tmp_path / "source.json"
    source.write_bytes(manifest)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    oras = binaries / "oras"
    oras.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[:2] == ['manifest', 'fetch']\n"
        "assert args[-1] == os.environ['CONFIG_SOURCE'] + '@' + os.environ['CONFIG_DIGEST']\n"
        "pathlib.Path(args[args.index('--output') + 1]).write_bytes("
        "pathlib.Path(os.environ['FAKE_MANIFEST']).read_bytes())\n"
    )
    oras.chmod(0o755)
    cosign = binaries / "cosign"
    cosign.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "assert sys.argv[1] == 'verify'\n"
        "assert sys.argv[-1] == os.environ['CONFIG_SOURCE'] + '@' + os.environ['CONFIG_DIGEST']\n"
        "pathlib.Path(os.environ['RUNNER_TEMP'], 'cosign-called').write_text('yes')\n"
        "sys.exit(int(os.environ['SIGNATURE_EXIT']))\n"
    )
    cosign.chmod(0o755)
    env_file = tmp_path / "github-env"
    env_file.write_text("previous=value\n")
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "FAKE_MANIFEST": str(source),
        "SIGNATURE_EXIT": "0" if signature_success else "1",
        "CONFIG_INSECURE": "false",
        "CONFIG_SOURCE": "ghcr.io/washu-tag/manifests/scout-config",
        "CONFIG_DIGEST": "sha256:" + hashlib.sha256(manifest).hexdigest(),
        "IDENTITY_REPOSITORY": "washu-tag/scout",
        "TESTED_SHA": "a" * 40,
        "IDENTITY_RUN_ID": "123",
        "IDENTITY_RUN_ATTEMPT": "2",
        "VERSION": "0.20261006.1",
        "MANIFEST_DIGEST": DIGEST,
        "MANIFEST_REPO": "ghcr.io/washu-tag/manifests/scout-manifest",
        "ARTIFACT_MODE": "published",
        "GITHUB_ENV": str(env_file),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    env.pop("BUNDLE_DIGEST", None)
    if bundle:
        env["BUNDLE_DIGEST"] = BUNDLE
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True
    )
    return result, env_file


@pytest.mark.parametrize("bundle", [False, True])
def test_old_and_release_receipts_verify_before_environment_export(tmp_path, bundle):
    result, env_file = verify(
        tmp_path, bundle=bundle, annotation=BUNDLE if bundle else None
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((tmp_path / "scout-config-ref.json").read_text())
    assert receipt["schemaVersion"] == (2 if bundle else 1)
    assert receipt.get("bundleDigest") == (BUNDLE if bundle else None)
    assert (tmp_path / "cosign-called").exists()
    assert "CONFIG_DIGEST=" in env_file.read_text()


@pytest.mark.parametrize(
    "annotation", [None, "sha256:" + "c" * 64, BUNDLE + "\ninjected=yes"]
)
def test_bundle_pairing_failure_stops_before_signature_and_environment(
    tmp_path, annotation
):
    result, env_file = verify(tmp_path, annotation=annotation)
    assert result.returncode != 0
    assert "OCI annotation mismatch" in result.stderr
    assert not (tmp_path / "cosign-called").exists()
    assert env_file.read_text() == "previous=value\n"
    assert not (tmp_path / "summary").exists()


def test_signature_failure_never_exports_verified_config(tmp_path):
    result, env_file = verify(tmp_path, signature_success=False)
    assert result.returncode != 0
    assert (tmp_path / "cosign-called").exists()
    assert env_file.read_text() == "previous=value\n"
    assert not (tmp_path / "summary").exists()
