"""Exercise producer shell steps against a registry whose tags move after push."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REPO = "ghcr.io/washu-tag/manifests/scout-manifest"
BUNDLE_REPO = "ghcr.io/washu-tag/manifests/scout"
CONFIG_REPO = "ghcr.io/washu-tag/manifests/scout-config"
OLD_DIGEST = "sha256:" + "1" * 64
RACING_DIGEST = "sha256:" + "2" * 64
VERSION = "0.20261005.123"
REVISION = "a" * 40

# The fake registry changes version tags immediately after each successful push.
# Resolving a tag again therefore selects another run's bytes, just as a rerun can.
FAKE_TOOLS = r"""
import hashlib
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE"])
state = json.loads(state_path.read_text())
output = Path(os.environ["GITHUB_OUTPUT"]).read_text()
receipt = Path(os.environ["RUNNER_TEMP"]) / "scout-config-ref.json"
state["calls"].append({
    "tool": tool, "args": args, "output": output,
    "receipt_exists": receipt.exists(),
})
state_path.write_text(json.dumps(state))

def save():
    state_path.write_text(json.dumps(state))

def failing(operation, ref):
    repo = ref.split("@")[0].split(":")[0]
    role = {
        "scout-manifest": "manifest", "scout": "bundle", "scout-config": "config",
    }[repo.rsplit("/", 1)[-1]]
    return os.environ.get("FAIL_OPERATION") == operation + ":" + role

if tool == "cosign":
    if args[0] == "public-key":
        print("fixture-public-key")
    elif args[0] in ("sign", "verify"):
        if failing(args[0], args[-1]):
            sys.exit(9)
        if args[0] == "sign":
            state["signed"].append(args[-1])
            save()
    else:
        sys.exit("unexpected cosign invocation")
elif tool == "hauler":
    if args[:2] == ["store", "sync"]:
        if "--key" in args:
            for i in range(int(os.environ.get("VERIFIED_COUNT", "2"))):
                print(f"signature verified for image [component-{i}]")
    elif args[:2] == ["store", "save"]:
        Path(args[args.index("-f") + 1]).write_bytes(b"fixture haul")
    else:
        sys.exit("unexpected hauler invocation")
elif tool == "oras":
    if args[0] == "push":
        ref = args[1]
        if failing("push", ref):
            sys.exit(9)
        annotations = dict(
            args[i + 1].split("=", 1)
            for i, arg in enumerate(args) if arg == "--annotation"
        )
        manifest = json.dumps({"ref": ref, "annotations": annotations}).encode()
        Path(args[args.index("--export-manifest") + 1]).write_bytes(manifest)
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        state["pushed"][ref] = digest
        state["tags"][ref] = os.environ["RACING_DIGEST"]
        save()
    elif args[0] == "tag":
        ref, tag = args[1:]
        if failing("tag", ref):
            sys.exit(9)
        if "@" in ref:
            repo, digest = ref.split("@")
        else:
            repo = ref.rsplit(":", 1)[0]
            digest = state["tags"][ref]
        state["tags"][repo + ":" + tag] = digest
        save()
    elif args[:2] == ["manifest", "fetch"]:
        print(json.dumps({"digest": state["tags"][args[-1]]}))
    elif args[0] == "resolve":
        print(state["tags"][args[-1]])
    elif args[0] == "pull":
        destination = Path(args[args.index("-o") + 1])
        destination.mkdir(exist_ok=True)
        marker = "expected" if args[1].endswith("@" + os.environ["MANIFEST_DIGEST"]) else "future"
        (destination / "haul.yaml").write_text(marker)
    else:
        sys.exit("unexpected oras invocation")
"""


@pytest.fixture
def producer(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("hauler", "oras", "cosign"):
        executable = tools / name
        executable.write_text(f"#!{sys.executable}\n" + FAKE_TOOLS)
        executable.chmod(0o755)
    (tools / "python3").symlink_to(sys.executable)
    (tmp_path / "runner").mkdir()
    (tmp_path / "output").touch()
    (tmp_path / "haul.yaml").write_text(
        "metadata:\n  name: scout\nspec:\n  images:\n"
        "    - name: ghcr.io/fixture/one@sha256:abc\n"
        "    - name: ghcr.io/fixture/two@sha256:def\n"
    )
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "calls": [],
                "signed": [],
                "pushed": {},
                "tags": {
                    f"{repo}:main": OLD_DIGEST
                    for repo in (MANIFEST_REPO, BUNDLE_REPO, CONFIG_REPO)
                },
            }
        )
    )
    env = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "FAKE_STATE": str(state),
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "VERSION": VERSION,
        "CORE_MANIFEST": "haul.yaml",
        "UPSTREAM_MANIFEST": "",
        "MANIFEST_REPO": MANIFEST_REPO,
        "BUNDLE_REPO": BUNDLE_REPO,
        "CONFIG_REPO": CONFIG_REPO,
        "GITHUB_REPOSITORY": "washu-tag/scout",
        "GITHUB_SHA": REVISION,
        "GITHUB_RUN_ID": "321",
        "GITHUB_RUN_ATTEMPT": "2",
        "SOURCE_REPOSITORY": "washu-tag/scout",
        "SOURCE_REVISION": REVISION,
        "COSIGN_PRIVATE_KEY": "fixture-key",
        "COSIGN_PASSWORD": "fixture-password",
        "MANIFEST_DIGEST": OLD_DIGEST,
        "RACING_DIGEST": RACING_DIGEST,
    }

    def run(script, **overrides):
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result, json.loads(state.read_text())

    return tmp_path, run


def haul_script():
    action = yaml.safe_load(
        (ROOT / ".github/actions/publish-haul/action.yaml").read_text()
    )
    return action["runs"]["steps"][0]["run"]


def config_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())
    return workflow["jobs"]["config-artifact-publish"]


@pytest.fixture
def config_producer(producer):
    directory, run = producer
    config = directory / "config-artifact"
    config.mkdir()
    for name in ("base", "flux", "modes"):
        (config / name).mkdir()
    for name in ("required-vars.txt", "required-secret-values.txt"):
        (config / name).write_text("fixture contract\n")
    helper = directory / "tooling/deploy/artifact_identity.py"
    helper.parent.mkdir(parents=True)
    helper.symlink_to(ROOT / "tooling/deploy/artifact_identity.py")
    step = next(
        step
        for step in config_job()["steps"]
        if step.get("name") == "Publish + sign the scout-config artifact"
    )
    return directory, run, step["run"]


@pytest.mark.parametrize("attempt", ["1", "3"])
def test_config_receipt_binds_signed_export_to_source_run_and_attempt(
    config_producer, attempt
):
    directory, run, script = config_producer
    result, state = run(script, GITHUB_RUN_ATTEMPT=attempt)
    assert result.returncode == 0, result.stderr
    raw = (directory / "runner/scout-config-manifest.json").read_bytes()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    receipt = json.loads((directory / "runner/scout-config-ref.json").read_text())
    assert receipt == {
        "schemaVersion": 1,
        "repository": "washu-tag/scout",
        "revision": REVISION,
        "runId": 321,
        "runAttempt": int(attempt),
        "version": VERSION,
        "manifestDigest": OLD_DIGEST,
        "configDigest": digest,
    }
    assert json.loads(raw)["annotations"] == {
        "org.opencontainers.image.source": "https://github.com/washu-tag/scout",
        "org.opencontainers.image.revision": REVISION,
        "org.opencontainers.image.version": VERSION,
        "io.scout.build.run-id": "321",
        "io.scout.build.run-attempt": attempt,
        "io.scout.build.manifest-digest": OLD_DIGEST,
    }
    assert state["tags"][f"{CONFIG_REPO}:{VERSION}"] == RACING_DIGEST
    assert state["signed"] == [f"{CONFIG_REPO}@{digest}"]
    assert state["tags"][f"{CONFIG_REPO}:main"] == digest
    assert not any(call["receipt_exists"] for call in state["calls"])
    operations = [call["args"][0] for call in state["calls"]]
    assert operations.index("sign") < operations.index("tag")


@pytest.mark.parametrize("operation", ["push", "sign", "tag"])
def test_failed_config_publish_never_emits_receipt(config_producer, operation):
    directory, run, script = config_producer
    result, state = run(script, FAIL_OPERATION=f"{operation}:config")
    assert result.returncode != 0
    assert not (directory / "runner/scout-config-ref.json").exists()
    assert state["tags"][f"{CONFIG_REPO}:main"] == OLD_DIGEST
    if operation != "tag":
        assert not any(call["args"][0] == "tag" for call in state["calls"])


def test_haul_signs_and_aliases_exported_bytes_despite_moving_tags(producer):
    directory, run = producer
    result, state = run(haul_script())
    assert result.returncode == 0, result.stderr
    expected = {}
    for role, repo in (("manifest", MANIFEST_REPO), ("bundle", BUNDLE_REPO)):
        manifest = (directory / "runner" / f"haul-{role}.json").read_bytes()
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        expected[f"{role}-digest"] = digest
        assert state["pushed"][f"{repo}:{VERSION}"] == digest
        assert state["tags"][f"{repo}:{VERSION}"] == RACING_DIGEST
        assert f"{repo}@{digest}" in state["signed"]
        assert state["tags"][f"{repo}:main"] == digest
        assert json.loads(manifest)["annotations"] == {
            "org.opencontainers.image.source": "https://github.com/washu-tag/scout",
            "org.opencontainers.image.revision": REVISION,
            "org.opencontainers.image.version": VERSION,
        }
    assert (
        dict(
            line.split("=", 1)
            for line in (directory / "output").read_text().splitlines()
        )
        == expected
    )
    assert all(call["output"] == "" for call in state["calls"])
    signs = [i for i, c in enumerate(state["calls"]) if c["args"][0] == "sign"]
    aliases = [i for i, c in enumerate(state["calls"]) if c["args"][0] == "tag"]
    assert max(signs) < min(aliases)


@pytest.mark.parametrize("role", ["manifest", "bundle"])
@pytest.mark.parametrize("operation", ["push", "sign"])
def test_failed_publish_or_sign_never_advances_aliases(producer, role, operation):
    directory, run = producer
    result, state = run(haul_script(), FAIL_OPERATION=f"{operation}:{role}")
    assert result.returncode != 0
    assert (directory / "output").read_text() == ""
    assert not any(call["args"][0] == "tag" for call in state["calls"])
    assert all(
        state["tags"][f"{repo}:main"] == OLD_DIGEST
        for repo in (MANIFEST_REPO, BUNDLE_REPO)
    )


@pytest.mark.parametrize("role", ["manifest", "bundle"])
def test_failed_alias_never_emits_success_outputs(producer, role):
    directory, run = producer
    result, _ = run(haul_script(), FAIL_OPERATION=f"tag:{role}")
    assert result.returncode != 0
    assert (directory / "output").read_text() == ""


@pytest.mark.parametrize("verified_count", ["0", "1", "3"])
def test_incomplete_or_excess_hauler_verification_fails_closed(
    producer, verified_count
):
    directory, run = producer
    result, state = run(haul_script(), VERIFIED_COUNT=verified_count)
    assert result.returncode != 0
    assert "verification incomplete" in result.stdout
    assert (directory / "output").read_text() == ""
    assert not state["pushed"]


def test_empty_core_manifest_fails_closed(producer):
    directory, run = producer
    (directory / "haul.yaml").write_text(
        "metadata:\n  name: scout\nspec:\n  images: []\n"
    )
    result, state = run(haul_script(), VERIFIED_COUNT="0")
    assert result.returncode != 0
    assert "verification incomplete" in result.stdout
    assert not state["pushed"]


@pytest.mark.parametrize("upstream", ["", "upstream haul.yaml"])
def test_upstream_contents_are_optional_and_do_not_bypass_core_verification(
    producer, upstream
):
    directory, run = producer
    if upstream:
        (directory / upstream).write_text("upstream content")
    result, state = run(haul_script(), UPSTREAM_MANIFEST=upstream)
    assert result.returncode == 0, result.stderr
    syncs = [
        call["args"]
        for call in state["calls"]
        if call["tool"] == "hauler" and call["args"][:2] == ["store", "sync"]
    ]
    assert "--key" in syncs[0]
    assert len(syncs) == (2 if upstream else 1)
    if upstream:
        assert syncs[1] == ["store", "sync", "-f", upstream]
        push = next(c for c in state["calls"] if c["tool"] == "oras")
        assert f"{upstream}:application/yaml" in push["args"]


@pytest.mark.parametrize("failure", ["", "verify:manifest", "bad-digest"])
def test_config_stamps_only_the_verified_job_digest(producer, failure):
    directory, run = producer
    (directory / "deploy").mkdir()
    stamper = directory / "tooling/deploy/stamp_config.py"
    stamper.parent.mkdir(parents=True)
    stamper.write_text(
        textwrap.dedent(
            """\
            from pathlib import Path
            import sys
            haul = Path(sys.argv[sys.argv.index('--haul') + 1])
            Path('stamped-from').write_text(haul.read_text())
            """
        )
    )
    step = next(
        s
        for s in config_job()["steps"]
        if s.get("name", "").startswith("Stamp deploy/")
    )
    overrides = {"FAIL_OPERATION": failure}
    if failure == "bad-digest":
        overrides["MANIFEST_DIGEST"] = "sha256:bad\nmain"
    result, state = run(step["run"], **overrides)
    if failure:
        assert result.returncode != 0
        assert not (directory / "stamped-from").exists()
        assert not any(c["tool"] == "oras" for c in state["calls"])
    else:
        assert result.returncode == 0, result.stderr
        assert (directory / "stamped-from").read_text() == "expected"
        assert state["calls"][0]["tool"] == "cosign"
        assert state["calls"][0]["args"][-1] == f"{MANIFEST_REPO}@{OLD_DIGEST}"
        assert state["calls"][1]["args"][1] == f"{MANIFEST_REPO}@{OLD_DIGEST}"


def test_config_publish_requires_a_ready_successful_haul():
    job = config_job()
    assert "needs.publish-haul.result == 'success'" in job["if"]
    assert "needs.publish-haul.outputs.ready == 'true'" in job["if"]
    assert (
        job["env"]["MANIFEST_DIGEST"]
        == "${{ needs.publish-haul.outputs.manifest-digest }}"
    )
