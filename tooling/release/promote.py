#!/usr/bin/env python3
"""Promote an exact, proven Scout package before publishing its GitHub Release.

GitHub attempts/artifacts establish build and test provenance; managed-key OCI
signatures establish artifact identity. The signed release record binds both.
Mutable build tags and commit statuses are never eligibility evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import zipfile
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from artifact_identity import (  # noqa: E402
    DIGEST,
    REVISION,
    IdentityError,
    _invalid_constant,
    _json_object,
    _object,
    validate_manifest,
    validate_receipt,
)
from proof_receipt import validate_proof  # noqa: E402

REPOSITORY = "washu-tag/scout"
REGISTRIES = {
    "manifestDigest": "ghcr.io/washu-tag/manifests/scout-manifest",
    "bundleDigest": "ghcr.io/washu-tag/manifests/scout",
    "configDigest": "ghcr.io/washu-tag/manifests/scout-config",
}
IMAGES = "hl7log-extractor hl7-transformer hl7-listener scout-notebook launchpad report-viewer keycloak-fragment-reconciler".split()
CHARTS = "hl7-transformer dcm4chee hive-metastore hl7-listener hl7log-extractor keycloak-config-cli keycloak-fragment-reconciler launchpad open-webui-bootstrap orthanc report-viewer scout-dashboards scout-opa temporal-bootstrap voila".split()
VERSION = re.compile(r"[1-9][0-9]*\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
MAX_JSON = 16384
MAX_MANIFEST = 1024 * 1024
REQUIRED_JOBS = (
    "Resolve exact published config",
    "deploy-and-test-flux (ingest)",
    "deploy-and-test-flux (auth)",
    "Record published artifact proof",
)


class PromotionError(ValueError):
    """Missing or conflicting release evidence; no silent fallback is permitted."""


class Pending(PromotionError):
    """The selected producer/proof is still running or has not appeared."""


def require(ok, message):
    if not ok:
        raise PromotionError(message)


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical(value):
    # JSON is also valid YAML 1.2. Stable bytes make partial publication resumable.
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def object_bytes(raw, limit=MAX_JSON):
    require(len(raw) <= limit, "JSON evidence exceeds size limit")
    return _json_object(raw)


def positive(value):
    require(
        type(value) is int and value > 0,
        "run and attempt identifiers must be positive integers",
    )
    return value


def command(args, *, output_limit=MAX_MANIFEST, env=None):
    proc = subprocess.run(args, capture_output=True, env=env)
    # Do not print external output: API/registry data can contain log commands.
    require(proc.returncode == 0, f"{args[0]} operation failed")
    require(len(proc.stdout) <= output_limit, f"{args[0]} output exceeds size limit")
    return proc.stdout


class GitHub:
    """Use gh's credential handling and redirects, retaining real HTTP status."""

    @staticmethod
    def mutation_env():
        env = os.environ.copy()
        if env.get("RELEASE_GH_TOKEN"):
            env["GH_TOKEN"] = env["RELEASE_GH_TOKEN"]
        return env

    def request(self, path, *, method="GET", data=None, missing_ok=False):
        require(
            path.startswith("/repos/") and "\n" not in path, "invalid GitHub API path"
        )
        args = ["gh", "api", "--include", "--method", method, path]
        payload = None
        if data is not None:
            args += ["--input", "-"]
            payload = canonical(data)
        release_access = re.match(r"/repos/[^/]+/[^/]+/releases(?:/|\?|$)", path)
        result = subprocess.run(
            args,
            input=payload,
            capture_output=True,
            env=self.mutation_env() if method != "GET" or release_access else None,
        )
        raw = result.stdout.replace(b"\r\n", b"\n")
        match = re.match(rb"HTTP/\S+ (\d{3})[^\n]*\n", raw)
        require(match is not None, "GitHub API returned no HTTP status")
        status = int(match.group(1))
        require(b"\n\n" in raw, "GitHub API response has no body boundary")
        body = raw.split(b"\n\n", 1)[1]
        if status == 404 and missing_ok:
            return None
        require(
            result.returncode == 0 and 200 <= status < 300,
            f"GitHub API request failed (HTTP {status})",
        )
        require(len(body) <= 8 * MAX_MANIFEST, "GitHub API response exceeds size limit")
        if not body.strip():
            return {}
        try:
            value = json.loads(
                body, object_pairs_hook=_object, parse_constant=_invalid_constant
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise PromotionError("invalid GitHub API JSON") from exc
        require(isinstance(value, (dict, list)), "invalid GitHub API response type")
        return value

    def download(self, path):
        # gh follows the artifact's signed storage redirect; no URL from JSON is executed.
        return command(["gh", "api", path], output_limit=MAX_MANIFEST)

    def upload(self, repository, release_id, file):
        # Address the selected draft by id; do not resolve a public tag again.
        url = f"https://uploads.github.com/repos/{repository}/releases/{positive(release_id)}/assets?name={quote(file.name, safe='')}"
        command(
            [
                "gh",
                "api",
                "--method",
                "POST",
                "-H",
                "Content-Type: application/octet-stream",
                "--input",
                str(file),
                url,
            ],
            env=self.mutation_env(),
        )

    def asset(self, repository, asset_id):
        return command(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/octet-stream",
                f"/repos/{repository}/releases/assets/{positive(asset_id)}",
            ],
            output_limit=MAX_MANIFEST,
            env=self.mutation_env(),
        )


def pages(api, path, key):
    separator = "&" if "?" in path else "?"
    out = []
    for page in range(1, 101):
        result = api.request(f"{path}{separator}per_page=100&page={page}")
        items = (
            result
            if key is None
            else result.get(key) if isinstance(result, dict) else None
        )
        require(
            isinstance(items, list) and all(isinstance(i, dict) for i in items),
            "invalid paginated GitHub response",
        )
        out.extend(items)
        if len(items) < 100:
            return out
    raise PromotionError("GitHub pagination exceeds bounded evidence search")


def workflow_id(api, repository, filename):
    item = api.request(f"/repos/{repository}/actions/workflows/{filename}")
    require(
        item.get("path") == f".github/workflows/{filename}", "unexpected workflow path"
    )
    return positive(item.get("id"))


def run_attempt(
    api, repository, run_id, attempt, workflow, event, *, revision=None, completed=True
):
    run_id, attempt = positive(run_id), positive(attempt)
    run = api.request(f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    expected = {
        "id": run_id,
        "run_attempt": attempt,
        "workflow_id": workflow,
        "event": event,
        "head_branch": "main",
    }
    for key, value in expected.items():
        require(
            type(run.get(key)) is type(value) and run[key] == value,
            "workflow attempt identity mismatch: " + key,
        )
    for key in ("repository", "head_repository"):
        require(
            isinstance(run.get(key), dict) and run[key].get("full_name") == repository,
            "workflow attempt repository mismatch",
        )
    require(
        isinstance(run.get("head_sha"), str) and REVISION.fullmatch(run["head_sha"]),
        "invalid workflow revision",
    )
    if revision is not None:
        require(
            run["head_sha"] == revision, "workflow attempt tested a different revision"
        )
    if run.get("status") != "completed":
        raise Pending("workflow attempt is not completed")
    if completed:
        require(run.get("conclusion") == "success", "workflow attempt did not succeed")
    return run


def artifact_json(api, repository, run, name, filename, *, optional=False):
    found = [
        a
        for a in pages(
            api, f"/repos/{repository}/actions/runs/{run['id']}/artifacts", "artifacts"
        )
        if a.get("name") == name
    ]
    if not found and optional:
        return None
    require(len(found) == 1, "missing or ambiguous attempt-specific evidence artifact")
    artifact = found[0]
    require(artifact.get("expired") is False, "evidence artifact expired")
    require(
        type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= MAX_MANIFEST,
        "invalid evidence archive size",
    )
    context = artifact.get("workflow_run")
    require(
        isinstance(context, dict)
        and context.get("id") == run["id"]
        and context.get("head_sha") == run["head_sha"],
        "artifact belongs to a different workflow run",
    )
    raw = api.download(
        f"/repos/{repository}/actions/artifacts/{positive(artifact.get('id'))}/zip"
    )
    require(len(raw) <= MAX_MANIFEST, "evidence archive exceeds size limit")
    advertised = artifact.get("digest")
    if advertised is not None:
        require(
            DIGEST.fullmatch(advertised) and digest(raw) == advertised,
            "evidence archive digest mismatch",
        )
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            require(
                len(items) == 1 and items[0].filename == filename,
                "unexpected evidence archive members",
            )
            item = items[0]
            mode = item.external_attr >> 16
            require(
                not item.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG),
                "evidence archive member is not a regular file",
            )
            require(
                item.file_size <= MAX_JSON and not item.flag_bits & 1,
                "invalid evidence archive member",
            )
            return object_bytes(archive.read(item))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise PromotionError("unreadable evidence archive") from exc


def validate_jobs(api, repository, run):
    jobs = pages(
        api,
        f"/repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",
        "jobs",
    )
    for name in REQUIRED_JOBS:
        matched = [job for job in jobs if job.get("name") == name]
        require(len(matched) == 1, "missing or ambiguous required proof job")
        job = matched[0]
        require(
            job.get("run_id") == run["id"] and job.get("head_sha") == run["head_sha"],
            "proof job belongs to a different workflow",
        )
        require(
            job.get("run_attempt", run["run_attempt"]) == run["run_attempt"],
            "proof job belongs to another attempt",
        )
        require(
            job.get("status") == "completed" and job.get("conclusion") == "success",
            "required proof job did not succeed in this attempt",
        )


def evidence(
    api,
    repository,
    revision,
    producer_id,
    producer_attempt,
    consumer_id,
    consumer_attempt,
):
    require(repository == REPOSITORY, "promotion is limited to the upstream repository")
    require(
        isinstance(revision, str) and REVISION.fullmatch(revision),
        "invalid source revision",
    )
    producer_run = run_attempt(
        api,
        repository,
        producer_id,
        producer_attempt,
        workflow_id(api, repository, "ci.yaml"),
        "push",
        revision=revision,
    )
    producer = artifact_json(
        api,
        repository,
        producer_run,
        f"scout-config-ref-{producer_attempt}",
        "scout-config-ref.json",
    )
    validate_receipt(
        producer,
        repository=repository,
        revision=revision,
        run_id=producer_id,
        run_attempt=producer_attempt,
        require_bundle=True,
    )
    consumer_run = run_attempt(
        api,
        repository,
        consumer_id,
        consumer_attempt,
        workflow_id(api, repository, "deploy-flux.yaml"),
        "workflow_run",
    )
    validate_jobs(api, repository, consumer_run)
    name = f"scout-release-proof-{producer_id}-{producer_attempt}-{consumer_attempt}"
    proof = artifact_json(api, repository, consumer_run, name, name + ".json")
    validate_proof(
        proof,
        producer=producer,
        repository=repository,
        run_id=consumer_id,
        run_attempt=consumer_attempt,
        revision=consumer_run["head_sha"],
    )
    return producer, proof


def find_evidence(api, repository, revision):
    require(
        repository == REPOSITORY and REVISION.fullmatch(revision),
        "unsupported repository or revision",
    )
    producer_workflow = workflow_id(api, repository, "ci.yaml")
    candidates = pages(
        api,
        f"/repos/{repository}/actions/workflows/{producer_workflow}/runs?event=push&branch=main&head_sha={revision}",
        "workflow_runs",
    )
    candidates = [r for r in candidates if r.get("head_sha") == revision]
    require(len(candidates) <= 1, "ambiguous producer runs for the release revision")
    if not candidates:
        raise Pending("producer run has not appeared")
    current = candidates[0]
    producer_id, attempt = positive(current.get("id")), positive(
        current.get("run_attempt")
    )
    producer_run = run_attempt(
        api,
        repository,
        producer_id,
        attempt,
        producer_workflow,
        "push",
        revision=revision,
    )
    producer = artifact_json(
        api,
        repository,
        producer_run,
        f"scout-config-ref-{attempt}",
        "scout-config-ref.json",
    )
    validate_receipt(
        producer,
        repository=repository,
        revision=revision,
        run_id=producer_id,
        run_attempt=attempt,
        require_bundle=True,
    )
    consumer_workflow = workflow_id(api, repository, "deploy-flux.yaml")
    created = producer_run.get("created_at")
    require(
        isinstance(created, str)
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created
        ),
        "invalid producer creation time",
    )
    since = quote(">=" + created, safe="")
    consumers = pages(
        api,
        f"/repos/{repository}/actions/workflows/{consumer_workflow}/runs?event=workflow_run&branch=main&created={since}",
        "workflow_runs",
    )
    matches = []
    for candidate in consumers:
        if candidate.get("created_at", "") < producer_run.get("created_at", ""):
            continue
        if (
            candidate.get("status") != "completed"
            or candidate.get("conclusion") != "success"
        ):
            continue
        consumer_id, consumer_attempt = positive(candidate.get("id")), positive(
            candidate.get("run_attempt")
        )
        name = f"scout-release-proof-{producer_id}-{attempt}-{consumer_attempt}"
        proof = artifact_json(
            api, repository, candidate, name, name + ".json", optional=True
        )
        if proof is not None:
            matches.append((consumer_id, consumer_attempt))
    if not matches:
        raise Pending("published artifact proof has not succeeded")
    # More than one independently successful proof is allowed. Freeze one concrete
    # attempt; subsequent promotion never consults a moving latest status.
    consumer_id, consumer_attempt = max(matches)
    producer, proof = evidence(
        api, repository, revision, producer_id, attempt, consumer_id, consumer_attempt
    )
    return producer, proof


class OCI:
    def __init__(self, public_key):
        self.public_key = str(public_key)

    def manifest(self, reference):
        return command(["oras", "manifest", "fetch", reference])

    def resolve(self, reference, *, missing_ok=False):
        result = subprocess.run(
            ["oras", "manifest", "fetch", "--descriptor", reference],
            capture_output=True,
        )
        if result.returncode:
            # Registry auth, timeouts and transport errors must never mean absent.
            absent = re.search(
                rb"\b(?:MANIFEST_UNKNOWN|manifest unknown)\b", result.stderr
            )
            absent = absent or result.stderr.strip().endswith(
                (reference + ": not found").encode()
            )
            if missing_ok and absent:
                return None
            raise PromotionError("OCI descriptor lookup failed")
        value = object_bytes(result.stdout).get("digest")
        require(
            isinstance(value, str) and DIGEST.fullmatch(value),
            "invalid OCI descriptor digest",
        )
        return value

    def verify(self, reference):
        command(
            [
                "cosign",
                "verify",
                "--key",
                self.public_key,
                "--insecure-ignore-tlog",
                reference,
            ],
            output_limit=4 * MAX_MANIFEST,
        )

    def blob(self, repository, descriptor):
        require(
            type(descriptor.get("size")) is int
            and 0 < descriptor["size"] <= MAX_MANIFEST,
            "invalid haul layer size",
        )
        value = descriptor.get("digest")
        require(
            isinstance(value, str) and DIGEST.fullmatch(value),
            "invalid haul layer digest",
        )
        raw = command(
            ["oras", "blob", "fetch", "--output", "-", repository + "@" + value]
        )
        require(
            len(raw) == descriptor["size"] and digest(raw) == value,
            "haul layer digest/size mismatch",
        )
        return raw

    def tag(self, reference, version):
        command(["oras", "tag", reference, version])

    def sign_record(self, record, bundle):
        command(
            [
                "cosign",
                "sign-blob",
                "--key",
                "env://COSIGN_PRIVATE_KEY",
                "--use-signing-config=false",
                "--tlog-upload=false",
                "--yes",
                "--bundle",
                str(bundle),
                str(record),
            ]
        )
        self.verify_record(record, bundle)

    def verify_record(self, record, bundle):
        command(
            [
                "cosign",
                "verify-blob",
                "--key",
                self.public_key,
                "--insecure-ignore-tlog",
                "--bundle",
                str(bundle),
                str(record),
            ]
        )


def verify_package(oci, producer):
    documents = {}
    for field, repository in REGISTRIES.items():
        reference = repository + "@" + producer[field]
        raw = oci.manifest(reference)
        require(digest(raw) == producer[field], "package OCI manifest digest mismatch")
        manifest = object_bytes(raw, MAX_MANIFEST)
        if field == "configDigest":
            validate_manifest(raw, producer)
        else:
            annotations = manifest.get("annotations", {})
            expected = {
                "org.opencontainers.image.source": "https://github.com/"
                + producer["repository"],
                "org.opencontainers.image.revision": producer["revision"],
                "org.opencontainers.image.version": producer["version"],
                "io.scout.build.run-id": str(producer["runId"]),
                "io.scout.build.run-attempt": str(producer["runAttempt"]),
            }
            require(
                isinstance(annotations, dict)
                and all(annotations.get(k) == v for k, v in expected.items()),
                "package OCI producer annotations mismatch",
            )
            require(
                annotations.get("io.scout.build.carry-policy") == "predecessor-v1",
                "package lacks validated producer carry policy",
            )
        oci.verify(reference)
        documents[field] = manifest
    return documents


def compatibility(oci, version, manifest):
    # Read the existing signed Hauler inventory; this is not a second component BOM.
    layers = manifest.get("layers", [])
    require(
        isinstance(layers, list) and 1 <= len(layers) <= 8,
        "invalid haul manifest layers",
    )
    refs = {}
    for layer in layers:
        require(
            isinstance(layer, dict) and layer.get("mediaType") == "application/yaml",
            "unexpected haul manifest layer",
        )
        raw = oci.blob(REGISTRIES["manifestDigest"], layer)
        for match in re.finditer(rb"^\s*- name:\s*(\S+)\s*$", raw, re.MULTILINE):
            reference = match.group(1).decode("ascii")
            repository, separator, sha = reference.partition("@")
            require(
                separator and DIGEST.fullmatch(sha),
                "haul inventory has an unpinned component",
            )
            repository = repository.rsplit(":", 1)[0]
            require(
                repository not in refs, "haul inventory contains a duplicate component"
            )
            refs[repository] = sha
    result = {
        "scope": "legacy Ansible release outputs; release-version charts are separately packaged and were not tested by the Flux proof",
        "images": {},
        "charts": {},
    }
    for name in IMAGES:
        repository = "ghcr.io/washu-tag/" + name
        expected = refs.get(repository)
        require(expected is not None, "versioned image is absent from signed haul")
        require(
            oci.resolve(repository + ":" + version) == expected,
            "legacy release image differs from tested package",
        )
        reference = repository + "@" + expected
        oci.verify(reference)
        result["images"][name] = reference
    for name in CHARTS:
        repository = "ghcr.io/washu-tag/charts/" + name
        sha = oci.resolve(repository + ":" + version)
        reference = repository + "@" + sha
        oci.verify(reference)
        result["charts"][name] = reference
    return result


def release_record(version, producer, proof, legacy, boundary_sha):
    return {
        "schemaVersion": 1,
        "kind": "ScoutRelease",
        "version": version,
        "repository": producer["repository"],
        "revision": producer["revision"],
        "boundaryRevision": boundary_sha,
        "producer": producer,
        "verification": proof,
        "artifacts": {
            field: repository + "@" + producer[field]
            for field, repository in REGISTRIES.items()
        },
        "compatibility": legacy,
        "scope": "Exact published config passed the on-prem core ingest/auth proof. The co-produced haul is signed; bundle restore and disconnected completeness are not certified.",
    }


def release_assets(api, repository, release, names, *, starters=None):
    assets = pages(
        api, f"/repos/{repository}/releases/{positive(release.get('id'))}/assets", None
    )
    require(
        all(
            asset.get("state") != "starter" or asset.get("name") in names
            for asset in assets
        ),
        "unexpected incomplete release asset",
    )
    result = {}
    for name in names:
        matches = [a for a in assets if a.get("name") == name]
        require(len(matches) <= 1, "duplicate release evidence asset")
        if matches:
            asset = matches[0]
            asset_id = positive(asset.get("id"))
            require(
                type(asset.get("size")) is int, "invalid release evidence asset size"
            )
            if asset.get("state") == "starter" and asset["size"] == 0:
                require(
                    starters is not None and release.get("draft") is True,
                    "incomplete evidence is allowed only in a recoverable draft",
                )
                starters.append(
                    {"id": asset_id, "name": name, "state": "starter", "size": 0}
                )
                continue
            require(
                asset.get("state") == "uploaded" and 0 < asset["size"] <= MAX_MANIFEST,
                "invalid release evidence asset state or size",
            )
            result[name] = api.asset(repository, asset_id)
    return result


def remove_empty_starters(api, repository, release, starters):
    """Remove only rechecked empty placeholders, after promotion's full preflight."""
    current_release = api.request(
        f"/repos/{repository}/releases/{positive(release.get('id'))}"
    )
    require(
        current_release.get("id") == release["id"]
        and current_release.get("draft") is True
        and current_release.get("tag_name") == release["tag_name"],
        "release changed before incomplete upload recovery",
    )
    # Check the complete deletion set before deleting any member. A completed
    # upload, renamed asset or another release state must never be overwritten.
    for expected in starters:
        current = api.request(f"/repos/{repository}/releases/assets/{expected['id']}")
        require(
            isinstance(current, dict)
            and all(
                type(current.get(key)) is type(value) and current[key] == value
                for key, value in expected.items()
            ),
            "incomplete upload changed before recovery",
        )
    for expected in starters:
        api.request(
            f"/repos/{repository}/releases/assets/{expected['id']}", method="DELETE"
        )


def existing_release(api, repository, version):
    # The tag endpoint is documented as published-only. Drafts are visible in
    # this listing with the release App's write credentials.
    matches = [
        release
        for release in pages(api, f"/repos/{repository}/releases", None)
        if release.get("tag_name") == "v" + version
    ]
    require(len(matches) <= 1, "multiple releases use the requested version")
    if not matches:
        return None
    result = api.request(
        f"/repos/{repository}/releases/{positive(matches[0].get('id'))}"
    )
    require(
        result.get("tag_name") == "v" + version and type(result.get("draft")) is bool,
        "release metadata changed during discovery",
    )
    return result


def promote(
    api,
    oci,
    *,
    repository,
    version,
    revision,
    producer_id,
    producer_attempt,
    consumer_id,
    consumer_attempt,
    boundary_sha,
    work_dir,
):
    require(VERSION.fullmatch(version), "release version must be X.Y.Z with major >= 1")
    require(REVISION.fullmatch(boundary_sha), "invalid release boundary revision")
    producer, proof = evidence(
        api,
        repository,
        revision,
        producer_id,
        producer_attempt,
        consumer_id,
        consumer_attempt,
    )
    manifests = verify_package(oci, producer)
    legacy = compatibility(oci, version, manifests["manifestDigest"])
    record = canonical(release_record(version, producer, proof, legacy, boundary_sha))
    require(len(record) <= MAX_JSON, "release record exceeds size limit")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    record_path = work_dir / f"scout-release-{version}.yaml"
    bundle_path = work_dir / f"scout-release-{version}.sigstore.json"
    record_path.write_bytes(record)
    names = (record_path.name, bundle_path.name)
    existing = existing_release(api, repository, version)
    starters = []
    saved = (
        release_assets(api, repository, existing, names, starters=starters)
        if existing
        else {}
    )
    if record_path.name in saved:
        require(
            saved[record_path.name] == record,
            "existing release record conflicts with selected evidence",
        )
    if bundle_path.name in saved:
        bundle_path.write_bytes(saved[bundle_path.name])
        oci.verify_record(record_path, bundle_path)
    if existing and existing.get("draft") is not True:
        require(
            set(saved) == set(names),
            "published release lacks the exact signed promotion record",
        )
        require(
            existing.get("tag_name") == "v" + version, "published release tag mismatch"
        )
    # Preflight every alias before changing any of them. Registries do not provide
    # a cross-repository transaction; workflow serialization protects these writes.
    for field, registry in REGISTRIES.items():
        current = oci.resolve(registry + ":" + version, missing_ok=True)
        require(
            current in (None, producer[field]),
            "release alias already identifies different content",
        )
        if existing and existing.get("draft") is not True:
            require(current == producer[field], "published release alias is missing")
    tag_path = f"/repos/{repository}/git/ref/tags/v{version}"
    tag = api.request(tag_path, missing_ok=True)
    if tag:
        require(
            tag.get("object", {}).get("type") == "commit",
            "release boundary must be a lightweight commit tag",
        )
        require(
            tag["object"].get("sha") in (boundary_sha, revision),
            "release tag points to an unexpected commit",
        )
    # Prove the planned move is a fast-forward. Never force-rewrite the source tag.
    if boundary_sha != revision:
        compare = api.request(
            f"/repos/{repository}/compare/{boundary_sha}...{revision}"
        )
        require(
            compare.get("status") == "ahead"
            and compare.get("merge_base_commit", {}).get("sha") == boundary_sha,
            "release build does not descend from the boundary",
        )
    if existing and existing.get("draft") is not True:
        require(
            tag and tag["object"].get("sha") == revision,
            "published release source tag mismatch",
        )
        return record_path
    if starters:
        remove_empty_starters(api, repository, existing, starters)
        rescanned = release_assets(api, repository, existing, names)
        require(
            rescanned == saved,
            "release evidence changed during incomplete upload recovery",
        )
    if bundle_path.name not in saved:
        oci.sign_record(record_path, bundle_path)
    if existing is None:
        existing = api.request(
            f"/repos/{repository}/releases",
            method="POST",
            data={
                "tag_name": "v" + version,
                "target_commitish": revision,
                "name": "Scout v" + version,
                "draft": True,
            },
        )
    require(
        existing.get("draft") is True and existing.get("tag_name") == "v" + version,
        "release is not the expected draft",
    )
    for path in (record_path, bundle_path):
        if path.name not in saved:
            api.upload(repository, positive(existing.get("id")), path)
    attached = release_assets(api, repository, existing, names)
    require(
        attached.get(record_path.name) == record
        and attached.get(bundle_path.name) == bundle_path.read_bytes(),
        "uploaded release evidence differs",
    )
    for field, registry in REGISTRIES.items():
        if oci.resolve(registry + ":" + version, missing_ok=True) is None:
            oci.tag(registry + "@" + producer[field], version)
        require(
            oci.resolve(registry + ":" + version) == producer[field],
            "release alias verification failed",
        )
    # Creating a draft may also create the target tag. Re-read rather than
    # assuming that the preflight snapshot still describes this mutable ref.
    tag = api.request(tag_path, missing_ok=True)
    if tag:
        require(
            tag.get("object", {}).get("type") == "commit"
            and tag["object"].get("sha") in (boundary_sha, revision),
            "release tag changed during promotion",
        )
    if tag is None:
        api.request(
            f"/repos/{repository}/git/refs",
            method="POST",
            data={"ref": "refs/tags/v" + version, "sha": revision},
        )
    elif tag["object"]["sha"] != revision:
        api.request(
            f"/repos/{repository}/git/refs/tags/v{version}",
            method="PATCH",
            data={"sha": revision, "force": False},
        )
    final_tag = api.request(tag_path)
    require(
        final_tag.get("object", {}).get("sha") == revision,
        "release source tag verification failed",
    )
    publication = {"draft": False}
    # GitHub ignores target_commitish while this tag exists. Generate notes only
    # after its final target is verified, so restamped fixes are not omitted.
    # Preserve notes a maintainer has deliberately added to an existing draft.
    current_release = api.request(
        f"/repos/{repository}/releases/{positive(existing.get('id'))}"
    )
    require(
        current_release.get("draft") is True
        and current_release.get("tag_name") == "v" + version,
        "release changed before publication",
    )
    if not current_release.get("body"):
        notes = api.request(
            f"/repos/{repository}/releases/generate-notes",
            method="POST",
            data={"tag_name": "v" + version, "target_commitish": revision},
        )
        require(isinstance(notes.get("body"), str), "release notes are missing")
        publication["body"] = notes["body"]
    # Narrow the legacy mutable-tag window before publishing. The documented
    # merge hold through dev reset is still necessary; these are snapshot checks.
    for kind in ("images", "charts"):
        for reference in legacy[kind].values():
            registry, expected = reference.split("@", 1)
            require(
                oci.resolve(registry + ":" + version) == expected,
                "legacy release alias changed during promotion",
            )
    published = api.request(
        f"/repos/{repository}/releases/{positive(existing.get('id'))}",
        method="PATCH",
        data=publication,
    )
    require(
        published.get("draft") is False and published.get("tag_name") == "v" + version,
        "release did not publish",
    )
    return record_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    wait = sub.add_parser("wait")
    publish = sub.add_parser("promote")
    for item in (wait, publish):
        item.add_argument("--repository", required=True)
        item.add_argument("--revision", required=True)
    wait.add_argument("--timeout", type=int, default=5400)
    wait.add_argument("--github-output", type=Path, required=True)
    publish.add_argument("--version", required=True)
    for name in (
        "producer-run-id",
        "producer-run-attempt",
        "consumer-run-id",
        "consumer-run-attempt",
    ):
        publish.add_argument("--" + name, type=int, required=True)
    publish.add_argument("--boundary-sha", required=True)
    publish.add_argument("--public-key", type=Path, required=True)
    publish.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        api = GitHub()
        if args.command == "wait":
            require(0 <= args.timeout <= 7200, "invalid proof wait timeout")
            deadline = time.monotonic() + args.timeout
            while True:
                try:
                    producer, proof = find_evidence(api, args.repository, args.revision)
                    break
                except Pending:
                    require(
                        time.monotonic() < deadline,
                        "timed out waiting for exact published proof",
                    )
                    print(
                        "Waiting for the exact producer and published artifact proof...",
                        flush=True,
                    )
                    time.sleep(min(30, max(0, deadline - time.monotonic())))
            outputs = {
                "run_id": producer["runId"],
                "run_attempt": producer["runAttempt"],
                "consumer_run_id": proof["consumer"]["runId"],
                "consumer_run_attempt": proof["consumer"]["runAttempt"],
                "build_version": producer["version"],
                "sha": producer["revision"],
            }
            with args.github_output.open("a") as output:
                for key, value in outputs.items():
                    output.write(f"{key}={value}\n")
        else:
            require(args.public_key.is_file(), "public verification key is missing")
            path = promote(
                api,
                OCI(args.public_key),
                repository=args.repository,
                version=args.version,
                revision=args.revision,
                producer_id=args.producer_run_id,
                producer_attempt=args.producer_run_attempt,
                consumer_id=args.consumer_run_id,
                consumer_attempt=args.consumer_run_attempt,
                boundary_sha=args.boundary_sha,
                work_dir=args.work_dir,
            )
            print("Verified release record: " + path.name)
    except (PromotionError, IdentityError, OSError, ValueError) as exc:
        # Deliberately do not echo remote payloads or tool stderr to workflow logs.
        print("release promotion rejected: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
