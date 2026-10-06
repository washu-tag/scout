#!/usr/bin/env python3
"""Prove independent site-artifact trust (ADR 0031) through live Flux status.

Positive sources must reconcile the exact signed OCI digest. Fresh negative
sources must reject signatures before publishing an artifact or applying a
harmless marker. Kubernetes API errors are failures, never evidence of absence.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml


NAMESPACE = "flux-system"
NEGATIVE_NAMESPACE = "ci-site-negative"
MARKER = "ci-site-trust-marker"
CASES = {
    "ci-site-wrong-key": "scout-site-wrong-pub",
    "ci-site-tampered": "scout-site-cosign-pub",
}
SOURCE_KIND = "ocirepositories.source.toolkit.fluxcd.io"
KUSTOMIZATION_KIND = "kustomizations.kustomize.toolkit.fluxcd.io"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
PENDING_REASONS = {"Progressing", "ProgressingWithRetry"}


class TrustError(RuntimeError):
    """A site trust invariant failed or could not be established."""


def kubectl(*args):
    try:
        result = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
            timeout=150,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrustError("kubectl could not complete " + args[0]) from exc
    if result.returncode:
        raise TrustError("kubectl failed " + " ".join(args[:3]))
    return result.stdout


def get(kind, name, namespace=NAMESPACE):
    args = ["get", kind, name, "--ignore-not-found", "-o", "json"]
    if namespace:
        args.extend(["-n", namespace])
    raw = kubectl(*args)
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise TrustError("kubectl returned invalid JSON") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
        raise TrustError("kubectl returned an invalid object")
    if obj["metadata"].get("name") != name or (
        namespace and obj["metadata"].get("namespace") != namespace
    ):
        raise TrustError("kubectl returned a different object")
    return obj


def condition(obj, kind):
    found = [
        c for c in obj.get("status", {}).get("conditions", []) if c.get("type") == kind
    ]
    if len(found) > 1:
        raise TrustError("duplicate " + kind + " conditions")
    return found[0] if found else {}


def current(obj, cond):
    generation = obj.get("metadata", {}).get("generation")
    return (
        type(generation) is int
        and generation > 0
        and type(cond.get("observedGeneration")) is int
        and cond["observedGeneration"] == generation
    )


def check_identity(obj, source, digest, key):
    spec = obj.get("spec", {})
    if (
        not isinstance(digest, str)
        or not DIGEST.fullmatch(digest)
        or spec.get("url") != source
        or spec.get("ref") != {"digest": digest}
        or spec.get("verify") != {"provider": "cosign", "secretRef": {"name": key}}
        or spec.get("suspend", False)
    ):
        raise TrustError(
            "OCI source identity or verification policy differs from expectation"
        )


def positive_ready(obj, source, digest, key):
    check_identity(obj, source, digest, key)
    ready, verified = condition(obj, "Ready"), condition(obj, "SourceVerified")
    if not current(obj, ready) or not current(obj, verified):
        return False
    if verified.get("status") == "False":
        raise TrustError("site signature verification failed")
    if ready.get("status") != "True" or verified.get("status") != "True":
        return False
    status = obj.get("status", {})
    if (
        type(status.get("observedGeneration")) is not int
        or status["observedGeneration"] != obj["metadata"]["generation"]
    ):
        return False
    # artifact.digest hashes the source-controller's stored archive, not the OCI
    # manifest. A digest-selected OCI source reports that digest as its revision.
    if (status.get("artifact") or {}).get("revision") != digest:
        raise TrustError("resolved OCI revision differs from the signed site digest")
    return True


def negative_rejected(obj, source, digest, key):
    check_identity(obj, source, digest, key)
    status = obj.get("status", {})
    ready, verified = condition(obj, "Ready"), condition(obj, "SourceVerified")
    if status.get("artifact"):
        raise TrustError("negative source published an artifact")
    if ready.get("status") == "True" or verified.get("status") == "True":
        raise TrustError("negative source was accepted")
    if (
        current(obj, ready)
        and ready.get("status") == "False"
        and ready.get("reason") not in PENDING_REASONS | {"VerificationError"}
    ):
        raise TrustError(
            "negative source failed for a reason other than signature verification"
        )
    if (
        current(obj, verified)
        and verified.get("status") == "False"
        and verified.get("reason") != "VerificationError"
    ):
        raise TrustError(
            "negative source failed for a reason other than signature verification"
        )
    if not current(obj, ready) or not current(obj, verified):
        return False
    # Recoverable signature errors need not advance status.observedGeneration;
    # each condition must still identify this spec generation.
    return (
        ready.get("status") == "False"
        and ready.get("reason") == "VerificationError"
        and verified.get("status") == "False"
        and verified.get("reason") == "VerificationError"
    )


def dependent_blocked(obj, name):
    spec = obj.get("spec", {})
    if (
        spec.get("sourceRef") != {"kind": "OCIRepository", "name": name}
        or spec.get("targetNamespace") != NEGATIVE_NAMESPACE
        or spec.get("path") != "./"
        or spec.get("prune") is not False
        or spec.get("suspend", False)
    ):
        raise TrustError("negative Kustomization differs from expectation")
    status = obj.get("status", {})
    ready = condition(obj, "Ready")
    if (
        status.get("lastAppliedRevision")
        or (status.get("inventory") or {}).get("entries")
        or ready.get("status") == "True"
    ):
        raise TrustError("negative Kustomization applied content")
    if not current(obj, ready):
        return False
    if ready.get("status") == "False" and ready.get("reason") not in PENDING_REASONS | {
        "ArtifactFailed"
    }:
        raise TrustError("negative Kustomization failed for an unrelated reason")
    return ready.get("status") == "False" and ready.get("reason") == "ArtifactFailed"


def evidence(obj, dependent=None):
    """Log controller verdict fields only, never condition messages or values."""
    if obj is None:
        return {"source": "missing"}
    status = obj.get("status", {})
    result = {"generation": obj.get("metadata", {}).get("generation")}
    for kind in ("Ready", "SourceVerified"):
        # Diagnostics must not mask the validator's duplicate-condition error.
        found = [c for c in status.get("conditions", []) if c.get("type") == kind]
        result[kind] = [
            {k: c.get(k) for k in ("status", "reason", "observedGeneration")}
            for c in found
        ]
    result["artifact.revision"] = (status.get("artifact") or {}).get("revision")
    if dependent is not None:
        result["dependent.lastAppliedRevision"] = dependent.get("status", {}).get(
            "lastAppliedRevision"
        )
    return result


def wait_positive(name, source, digest, key, timeout):
    deadline = time.monotonic() + timeout
    shown = None
    while True:
        obj = get(SOURCE_KIND, name)
        state = evidence(obj)
        if state != shown:
            print(name + ": " + json.dumps(state, sort_keys=True), flush=True)
            shown = state
        try:
            complete = obj is not None and positive_ready(obj, source, digest, key)
        except TrustError as exc:
            raise TrustError(name + ": " + str(exc)) from exc
        if complete:
            print(name + ": current signed OCI digest is verified and Ready")
            return
        if time.monotonic() >= deadline:
            raise TrustError(name + ": timed out waiting for verified site source")
        time.sleep(5)


def negative_specs(manifest):
    """Limit apply/cleanup to this test's five explicitly owned resources."""
    try:
        docs = list(yaml.safe_load_all(Path(manifest).read_text()))
        indexed = {
            (d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"]): d
            for d in docs
        }
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise TrustError("invalid negative test manifest") from exc
    expected = {("Namespace", "", NEGATIVE_NAMESPACE)} | {
        (kind, NAMESPACE, name)
        for name in CASES
        for kind in ("OCIRepository", "Kustomization")
    }
    if set(indexed) != expected or len(docs) != len(expected):
        raise TrustError(
            "negative manifest must contain exactly the five test resources"
        )
    versions = {
        "Namespace": "v1",
        "OCIRepository": "source.toolkit.fluxcd.io/v1",
        "Kustomization": "kustomize.toolkit.fluxcd.io/v1",
    }
    if any(d.get("apiVersion") != versions[d["kind"]] for d in docs):
        raise TrustError("negative manifest contains an unexpected API version")
    sources = {}
    for name, key in CASES.items():
        obj = indexed[("OCIRepository", NAMESPACE, name)]
        spec = obj.get("spec", {})
        source, digest = spec.get("url", ""), spec.get("ref", {}).get("digest", "")
        if not re.fullmatch(r"oci://[A-Za-z0-9.:-]+/ci-site-negative", source):
            raise TrustError("unexpected negative artifact repository")
        check_identity(obj, source, digest, key)
        dependent_blocked(indexed[("Kustomization", NAMESPACE, name)], name)
        sources[name] = (source, digest, key)
    if (
        len({s[0] for s in sources.values()}) != 1
        or len({s[1] for s in sources.values()}) != 2
    ):
        raise TrustError(
            "negative fixtures must share a repository and have distinct digests"
        )
    return sources


def run_negative(manifest, timeout):
    sources = negative_specs(manifest)
    # Never delete or reuse existing sources: a retained artifact would invalidate
    # the proof and cleanup must not remove resources this invocation did not own.
    if get("namespace", NEGATIVE_NAMESPACE, namespace=None) is not None:
        raise TrustError(NEGATIVE_NAMESPACE + ": negative namespace already exists")
    for name in CASES:
        for kind in (SOURCE_KIND, KUSTOMIZATION_KIND):
            if get(kind, name) is not None:
                raise TrustError(name + ": negative resource already exists")
    try:
        kubectl("apply", "-f", str(manifest))
        deadline = time.monotonic() + timeout
        shown = {}
        while True:
            complete = True
            pending = []
            for name, identity in sources.items():
                source, dependent = get(SOURCE_KIND, name), get(
                    KUSTOMIZATION_KIND, name
                )
                state = evidence(source, dependent)
                if shown.get(name) != state:
                    print(name + ": " + json.dumps(state, sort_keys=True), flush=True)
                    shown[name] = state
                try:
                    source_ok = source is not None and negative_rejected(
                        source, *identity
                    )
                    dependent_ok = dependent is not None and dependent_blocked(
                        dependent, name
                    )
                except TrustError as exc:
                    raise TrustError(name + ": " + str(exc)) from exc
                complete = complete and source_ok and dependent_ok
                if not source_ok or not dependent_ok:
                    pending.append(name)
            if get("configmap", MARKER, namespace=NEGATIVE_NAMESPACE) is not None:
                raise TrustError(
                    NEGATIVE_NAMESPACE + "/" + MARKER + ": negative marker was applied"
                )
            if complete:
                print(
                    "site trust: wrong key and tampered payload both rejected before apply"
                )
                return
            if time.monotonic() >= deadline:
                raise TrustError(
                    ", ".join(pending)
                    + ": timed out waiting for signature rejection and blocked dependents"
                )
            time.sleep(5)
    finally:
        original = sys.exc_info()[1]
        try:
            kubectl(
                "delete",
                "-f",
                str(manifest),
                "--ignore-not-found",
                "--wait=true",
                "--timeout=120s",
            )
        except TrustError as exc:
            if original is not None:
                raise TrustError(
                    str(original) + "; negative resource cleanup also failed"
                ) from exc
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    positive = commands.add_parser("positive")
    for flag in ("name", "source", "digest", "key"):
        positive.add_argument("--" + flag, required=True)
    negative = commands.add_parser("negative")
    negative.add_argument("--manifest", required=True, type=Path)
    for command in (positive, negative):
        command.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise TrustError("timeout must be positive")
        if args.command == "positive":
            wait_positive(args.name, args.source, args.digest, args.key, args.timeout)
        else:
            run_negative(args.manifest, args.timeout)
    except TrustError as exc:
        print("site trust: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
