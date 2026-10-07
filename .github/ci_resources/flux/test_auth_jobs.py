"""Auth proof selects packaged inputs and cannot hide a failed test phase."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
import yaml

import auth_jobs


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / ".github/ci_resources/flux/run_auth_tests.sh"
DIGEST = "sha256:" + "a" * 64


def config_archive(path, *, tag="0.20261006.123", duplicate=False, link=False):
    release = {
        "kind": "HelmRelease",
        "metadata": {"name": "hl7-transformer"},
        "spec": {"values": {"image": {"repository": auth_jobs.REPOSITORY, "tag": tag}}},
    }
    content = yaml.safe_dump_all([release] * (2 if duplicate else 1)).encode()
    with tarfile.open(path, "w:gz") as bundle:
        member = tarfile.TarInfo(auth_jobs.RESOURCE)
        member.size = len(content)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
        bundle.addfile(member, io.BytesIO(content))
    return path


@pytest.fixture
def cluster_vars(tmp_path):
    values = tmp_path / "cluster-vars.values.json"
    values.write_text(json.dumps({"lake_bucket": "merged-ci-lake"}))
    return values


def test_render_uses_packaged_image_and_merged_warehouse(tmp_path, cluster_vars):
    archive = config_archive(tmp_path / "config.tar.gz")
    output = tmp_path / "jobs"
    auth_jobs.prepare(archive, output, cluster_vars)
    seed = json.loads((output / "seed.json").read_text())
    container = seed["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == ("ghcr.io/washu-tag/hl7-transformer:0.20261006.123")
    env = {item["name"]: item for item in container["env"]}
    assert env["SPARK_SQL_WAREHOUSE_DIR"]["value"] == "s3a://merged-ci-lake/delta"
    assert env["AWS_SECRET_ACCESS_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "lake-writer-creds",
        "key": "CONSOLE_SECRET_KEY",
    }
    fixture = json.loads(
        (ROOT / ".github/ci_resources/flux/secret-values.json").read_text()
    )
    for name, key in (
        ("KC_ADMIN_PASSWORD", "keycloak_bootstrap_admin_password"),
        ("SUPERSET_SVC_CLIENT_SECRET", "keycloak_superset_svc_client_secret"),
        (
            "REPORT_VIEWER_SVC_CLIENT_SECRET",
            "keycloak_report_viewer_svc_client_secret",
        ),
    ):
        path = output / name
        assert path.read_text() == fixture[key]
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("tag", ["latest", "0.0.0", "", None, "${VERSION}"])
def test_unstamped_image_leaves_no_jobs(tmp_path, cluster_vars, tag):
    archive = config_archive(tmp_path / "config.tar.gz", tag=tag)
    output = tmp_path / "jobs"
    with pytest.raises(ValueError, match="stamped Scout image"):
        auth_jobs.prepare(archive, output, cluster_vars)
    assert not output.exists()


def test_ambiguous_transformer_release_rejected(tmp_path):
    archive = config_archive(tmp_path / "config.tar.gz", duplicate=True)
    with pytest.raises(ValueError, match="one transformer"):
        auth_jobs.transformer_image(archive)


def test_archive_symlink_cannot_supply_seed_image(tmp_path):
    archive = config_archive(tmp_path / "config.tar.gz", link=True)
    with pytest.raises(ValueError, match="regular file"):
        auth_jobs.transformer_image(archive)


@pytest.fixture
def run_helper(tmp_path, cluster_vars):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    archive = config_archive(tmp_path / "config.tar.gz")
    ca = tmp_path / "ca.pem"
    ca.write_text("offline test CA placeholder")
    log = tmp_path / "commands.jsonl"
    # Execute the real orchestrator and renderer; replace only external clients.
    client = bin_dir / "client"
    client.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json
import os
from pathlib import Path
import shutil
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "bash" and args[0].endswith("run_auth_tests.sh"):
    os.execv("/bin/bash", ["/bin/bash", *args])
entry = {"command": name, "args": args}
if name == "npx":
    entry["strict_tls"] = os.environ["PLAYWRIGHT_IGNORE_HTTPS_ERRORS"] == "false"
if name == "kubectl" and args[:2] == ["apply", "-f"]:
    if args[2] == "-":
        sys.stdin.read()
    else:
        path = Path(args[2])
        if path.name == "seed.json":
            entry["seed"] = json.loads(path.read_text())
with open(os.environ["FAKE_LOG"], "a") as stream:
    stream.write(json.dumps(entry) + "\n")
if name == "oras":
    if os.environ.get("FAIL_PHASE") == "pull":
        sys.exit(7)
    target = Path(args[args.index("-o") + 1]) / "scout-config.tar.gz"
    shutil.copyfile(os.environ["FAKE_ARCHIVE"], target)
if name == "bash":
    if args[0].endswith("auth-curl-tests.sh"):
        sys.exit(7 if os.environ.get("FAIL_PHASE") == "curl" else 0)
    if args[0].endswith("wait-for-job.sh"):
        sys.exit(7 if os.environ.get("FAIL_PHASE") == args[2] else 0)
    sys.exit("unexpected bash invocation")
if name == "npx":
    sys.exit(7 if os.environ.get("FAIL_PHASE") == "browser" else 0)
"""
    )
    client.chmod(0o755)
    for name in ("bash", "oras", "kubectl", "npx"):
        (bin_dir / name).symlink_to(client)
    # Use the test interpreter, including PyYAML, in the renderer subprocess.
    (bin_dir / "python3").symlink_to(sys.executable)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "PLATFORM_CA_CERT": str(ca),
        "NODE_EXTRA_CA_CERTS": str(ca),
        "CONFIG_SOURCE": "127.0.0.1:5000/scout-config",
        "CONFIG_DIGEST": DIGEST,
        "CONFIG_INSECURE": "true",
        "FAKE_ARCHIVE": str(archive),
        "FAKE_LOG": str(log),
    }

    def run(phase="", mode="all", **overrides):
        result = subprocess.run(
            ["/bin/bash", str(HELPER), mode],
            cwd=ROOT,
            env={**env, "FAIL_PHASE": phase, **overrides},
            capture_output=True,
            text=True,
        )
        entries = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        return result, entries

    return run


@pytest.mark.parametrize("insecure", ["true", "false"])
def test_helper_pulls_exact_config_and_runs_scoped_suites(run_helper, insecure):
    result, entries = run_helper(CONFIG_INSECURE=insecure)
    assert result.returncode == 0, result.stderr
    pull = next(item for item in entries if item["command"] == "oras")
    expected = ["pull"] + (["--plain-http"] if insecure == "true" else [])
    expected.append(f"127.0.0.1:5000/scout-config@{DIGEST}")
    assert pull["args"][: len(expected)] == expected
    curl = next(
        item
        for item in entries
        if item["command"] == "bash" and "auth-curl" in item["args"][0]
    )
    assert curl["args"][1:] == ["scout.test", "--include", "keycloak,auth"]
    browser = next(item for item in entries if item["command"] == "npx")
    assert browser["args"][-2:] == ["test", "flux-platform.spec.ts"]
    assert browser["strict_tls"]
    seed = next(item["seed"] for item in entries if "seed" in item)
    container = seed["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].endswith(":0.20261006.123")
    env = {item["name"]: item for item in container["env"]}
    assert env["SPARK_SQL_WAREHOUSE_DIR"]["value"] == "s3a://merged-ci-lake/delta"
    assert any("data-authz-tests" in item["args"] for item in entries)


@pytest.mark.parametrize("phase", ["curl", "browser"])
def test_auth_failure_still_runs_other_proofs_and_fails(run_helper, phase):
    result, entries = run_helper(phase)
    assert result.returncode != 0
    assert any(item["command"] == "npx" for item in entries)
    assert any("data-authz-tests" in item["args"] for item in entries)


def test_seed_failure_does_not_run_queries_on_stale_data(run_helper):
    result, entries = run_helper("data-authz-seed")
    assert result.returncode != 0
    assert not any("data-authz-tests" in item["args"] for item in entries)


@pytest.mark.parametrize("phase", ["pull", "data-authz-tests"])
def test_data_failure_fails_combined_proof(run_helper, phase):
    result, _ = run_helper(phase)
    assert result.returncode != 0


def test_invalid_digest_cannot_pull_or_create_jobs(run_helper):
    result, entries = run_helper(mode="data-authz", CONFIG_DIGEST="main")
    assert result.returncode != 0
    assert not entries
