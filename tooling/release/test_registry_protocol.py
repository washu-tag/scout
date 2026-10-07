"""Opt-in protocol proof with an actual OCI registry and ephemeral managed key.

The GitHub API is a stateful fixture, not a live release. Package payloads are
small fixtures: this proves identity/signature/alias/recovery, not bundle restore.
Run with SCOUT_RELEASE_REGISTRY_PROOF=1 and registry/oras/cosign on PATH.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

import pytest

import promote as p
from test_promote import BOUNDARY, FakeGitHub, SHA, VERSION, promote

pytestmark = pytest.mark.skipif(
    os.getenv("SCOUT_RELEASE_REGISTRY_PROOF") != "1",
    reason="opt-in real registry protocol proof",
)


def tool(args, *, cwd=None, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


class LocalOCI(p.OCI):
    """Redirect fixed production repository names into the disposable registry."""

    def __init__(self, public_key, host):
        super().__init__(public_key)
        self.host = host
        self.fail = None
        self.mutations = []

    def local(self, reference):
        assert reference.startswith("ghcr.io/washu-tag/")
        return reference.replace("ghcr.io/washu-tag/", self.host + "/scout/", 1)

    def manifest(self, reference):
        return super().manifest(self.local(reference))

    def resolve(self, reference, *, missing_ok=False):
        return super().resolve(self.local(reference), missing_ok=missing_ok)

    def verify(self, reference):
        return super().verify(self.local(reference))

    def blob(self, repository, descriptor):
        return super().blob(self.local(repository), descriptor)

    def tag(self, reference, version):
        if self.fail == reference.split("@", 1)[0]:
            self.fail = None
            raise p.PromotionError("injected registry write interruption")
        self.mutations.append(reference)
        return super().tag(self.local(reference), version)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    for name in ("registry", "oras", "cosign"):
        assert shutil.which(name), name + " is required for the opt-in proof"
    root = tmp_path_factory.mktemp("release-registry")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    host = "localhost:" + str(port)
    with (root / "registry.log").open("wb") as log:
        # github.com/google/go-containerregistry/cmd/registry, pinned in CI.
        process = subprocess.Popen(
            ["registry", "-port", str(port)], stdout=log, stderr=subprocess.STDOUT
        )
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(
                        "http://" + host + "/v2/", timeout=0.5
                    ) as response:
                        assert response.status == 200
                    break
                except OSError:
                    assert process.poll() is None, "registry exited early"
                    time.sleep(0.1)
            else:
                pytest.fail("registry did not start")
            yield root, host
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_real_registry_signatures_exact_aliases_and_recovery(
    registry, monkeypatch, tmp_path
):
    root, host = registry
    monkeypatch.setenv("COSIGN_PASSWORD", "")
    tool(["cosign", "generate-key-pair", "--output-key-prefix", str(root / "cosign")])
    monkeypatch.setenv("COSIGN_PRIVATE_KEY", (root / "cosign.key").read_text())
    oci = LocalOCI(root / "cosign.pub", host)
    producer = dict(
        schemaVersion=2,
        repository=p.REPOSITORY,
        revision=SHA,
        runId=101,
        runAttempt=2,
        version="0.20261005.42",
    )
    annotations = {
        "org.opencontainers.image.source": "https://github.com/" + p.REPOSITORY,
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.version": producer["version"],
        "io.scout.build.run-id": "101",
        "io.scout.build.run-attempt": "2",
        "io.scout.build.carry-policy": "predecessor-v1",
    }

    def push(repository, version, payload, media_type, anno=None):
        args = [
            "oras",
            "push",
            oci.local(repository + ":" + version),
            payload + ":" + media_type,
        ]
        for key, value in (anno or {}).items():
            args.extend(["--annotation", key + "=" + value])
        tool(args, cwd=root)
        sha = oci.resolve(repository + ":" + version)
        reference = oci.local(repository + "@" + sha)
        tool(
            [
                "cosign",
                "sign",
                "--key",
                "env://COSIGN_PRIVATE_KEY",
                "--use-signing-config=false",
                "--tlog-upload=false",
                "--yes",
                reference,
            ]
        )
        return sha

    (root / "component.txt").write_text("fixture component bytes\n")
    lines = [
        "apiVersion: content.hauler.cattle.io/v1",
        "kind: Images",
        "spec:",
        "  images:",
    ]
    for name in p.IMAGES:
        repository = "ghcr.io/washu-tag/" + name
        sha = push(repository, VERSION, "component.txt", "application/octet-stream")
        lines.append(
            "    - name: " + repository + ":" + producer["version"] + "@" + sha
        )
    for name in p.CHARTS:
        push(
            "ghcr.io/washu-tag/charts/" + name,
            VERSION,
            "component.txt",
            "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
        )
    (root / "haul.yaml").write_text("\n".join(lines) + "\n")
    (root / "bundle.tar.zst").write_bytes(
        b"fixture bundle identity, not a restore proof\n"
    )
    (root / "config.tar.gz").write_bytes(
        b"fixture config identity; tests simulated by the GitHub fixture\n"
    )
    for field, payload, media in (
        ("manifestDigest", "haul.yaml", "application/yaml"),
        ("bundleDigest", "bundle.tar.zst", "application/zstd"),
        (
            "configDigest",
            "config.tar.gz",
            "application/vnd.cncf.flux.content.v1.tar+gzip",
        ),
    ):
        anno = dict(annotations)
        if field in ("configDigest", "bundleDigest"):
            anno.update(
                {
                    "io.scout.build.manifest-digest": producer["manifestDigest"],
                }
            )
        producer[field] = push(
            p.REGISTRIES[field], producer["version"], payload, media, anno
        )
    api = FakeGitHub(producer)

    # A missing exact alias must be recognized without hiding auth/transport errors.
    assert (
        oci.resolve(p.REGISTRIES["configDigest"] + ":" + VERSION, missing_ok=True)
        is None
    )
    # Move mutable build tags to unrelated bytes: all promotion reads stay by digest.
    for repository in p.REGISTRIES.values():
        push(
            repository, producer["version"], "component.txt", "application/octet-stream"
        )
    # A real wrong verification key must reject before any draft/alias mutation.
    tool(["cosign", "generate-key-pair", "--output-key-prefix", str(root / "wrong")])
    wrong = LocalOCI(root / "wrong.pub", host)
    with pytest.raises(p.PromotionError):
        promote(api, wrong, tmp_path)
    assert api.events == [] and wrong.mutations == []

    # Re-signing after a failure before draft creation must work with a bundle
    # file left in the same work directory by the interrupted invocation.
    api.fail = "create-draft"
    with pytest.raises(p.PromotionError, match="create-draft"):
        promote(api, oci, tmp_path)
    assert api.release is None and oci.mutations == []

    oci.fail = p.REGISTRIES["bundleDigest"]
    with pytest.raises(p.PromotionError, match="interruption"):
        promote(api, oci, tmp_path)
    assert api.release["draft"] is True
    assert len(oci.mutations) == 1
    original = dict(api.assets)
    # Next retry reaches publication and fails there, leaving all exact aliases and draft.
    api.fail = "publish"
    with pytest.raises(p.PromotionError, match="publish"):
        promote(api, oci, tmp_path)
    assert api.release["draft"] is True
    assert api.assets == original
    record = promote(api, oci, tmp_path)
    assert api.release["draft"] is False
    bundle = tmp_path / ("scout-release-" + VERSION + ".sigstore.json")
    oci.verify_record(record, bundle)
    for field, repository in p.REGISTRIES.items():
        assert oci.resolve(repository + ":" + VERSION) == producer[field]
    assert len(oci.mutations) == 3
    before = list(api.events), list(oci.mutations)
    promote(api, oci, tmp_path)
    assert before == (api.events, oci.mutations)
    tampered = tmp_path / "tampered.yaml"
    tampered.write_bytes(record.read_bytes() + b" ")
    with pytest.raises(p.PromotionError):
        oci.verify_record(tampered, bundle)
    # Replacing a public alias causes conflict; never silently retag it.
    push(
        p.REGISTRIES["configDigest"],
        VERSION,
        "component.txt",
        "application/octet-stream",
    )
    with pytest.raises(p.PromotionError, match="different content"):
        promote(api, oci, tmp_path)
    assert before == (api.events, oci.mutations)
