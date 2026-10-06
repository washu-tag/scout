#!/usr/bin/env python3
"""Negative cases for the on-prem secret contract, run against a live kustomize-controller.

1. ci-neg-strict: the Secret templates from a values Secret missing valkey_password. Strict
   substitution must fail the build, name the variable, and quote no value.
2. ci-neg-nodecrypt: a SOPS-encrypted Secret applied without decryption. It must be
   rejected, and no Secret may be created.

Both run before the app roots, and everything they create is deleted afterwards. Messages
are printed with every fixture value masked.

Usage: negative_cases.py <negative.yaml rendered> <secret values json>
"""

import json
import subprocess
import sys
import time

DROPPED = "valkey_password"
TIMEOUT = 300


def kubectl(*args, stdin=None, check=True):
    p = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True)
    if check and p.returncode:
        sys.exit("kubectl {} failed: {}".format(" ".join(args[:3]), p.stderr.strip()))
    return p


# Reasons a Kustomization reports before it has built anything (source not fetched yet).
TRANSIENT = {
    "ArtifactFailed",
    "DependencyNotReady",
    "Progressing",
    "ProgressingWithRetry",
}


def verdict(name):
    """(status, reason, message) once the Kustomization has tried a source revision."""
    p = kubectl(
        "get", "kustomization", name, "-n", "flux-system", "-o", "json", check=False
    )
    if p.returncode:
        return None
    obj = json.loads(p.stdout)
    st = obj.get("status", {})
    if not st.get("lastAttemptedRevision"):
        return None
    for c in st.get("conditions", []):
        if c["type"] == "Ready" and c.get("reason") not in TRANSIENT:
            return c["status"], c.get("reason", ""), c.get("message", "")
    return None


def wait_settled(name):
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        r = verdict(name)
        if r:
            return r
        time.sleep(5)
    return None


def secrets_in(namespace, names=None):
    p = kubectl("get", "secrets", "-n", namespace, "-o", "name", check=False)
    found = [l.split("/", 1)[1] for l in p.stdout.split() if l]
    return [n for n in found if names is None or n in names]


def main() -> None:
    manifest, values_path = sys.argv[1], sys.argv[2]
    values = json.load(open(values_path))
    secrets = sorted((v for v in values.values() if len(v) >= 8), key=len, reverse=True)

    def masked(text):
        for v in secrets:
            text = text.replace(v, "***")
        return text

    def leaks(text):
        return [k for k, v in sorted(values.items()) if len(v) >= 8 and v in text]

    neg_values = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "ci-neg-values", "namespace": "flux-system"},
        "type": "Opaque",
        "stringData": {k: v for k, v in values.items() if k != DROPPED},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(neg_values))
    kubectl("apply", "-f", manifest)
    problems = []

    r = wait_settled("ci-neg-strict")
    want = 'variable not set (strict mode): "{}"'.format(DROPPED)
    if r is None:
        problems.append("ci-neg-strict: no Ready verdict within {}s".format(TIMEOUT))
    else:
        print(
            "ci-neg-strict: Ready={} reason={}\n  {}".format(r[0], r[1], masked(r[2]))
        )
        if r[0] != "False" or want not in r[2]:
            problems.append(
                "ci-neg-strict: expected Ready=False naming {}".format(DROPPED)
            )
        if leaks(r[2]):
            problems.append(
                "ci-neg-strict: message quotes the values of {}".format(leaks(r[2]))
            )
    created = secrets_in("kube-system", {"oauth2-proxy", "oauth2-proxy-redis"})
    if created:
        problems.append("ci-neg-strict: created Secrets {}".format(created))

    r = wait_settled("ci-neg-nodecrypt")
    if r is None:
        problems.append("ci-neg-nodecrypt: no Ready verdict within {}s".format(TIMEOUT))
    else:
        print(
            "ci-neg-nodecrypt: Ready={} reason={}\n  {}".format(
                r[0], r[1], masked(r[2])
            )
        )
        # Rejected by the site's admission guard (flux-system/sops-guard.yaml), or by
        # kustomize-controller's own check ("<Secret> is SOPS encrypted, configuring
        # decryption is required ..."), which v1.9.6 skips (see sops-guard.yaml).
        if r[0] != "False" or not any(
            s in r[2] for s in ("reject-sops-ciphertext", "is SOPS encrypted")
        ):
            problems.append(
                "ci-neg-nodecrypt: expected Ready=False for a SOPS-encrypted Secret"
            )
    created = secrets_in("ci-negative")
    if created:
        problems.append(
            "ci-neg-nodecrypt: created Secrets {} in ci-negative".format(created)
        )

    # Clean up: nothing here was applied, so deleting cannot prune a real object.
    kubectl("delete", "-f", manifest, "--wait=true", "--timeout=120s", check=False)
    kubectl(
        "delete",
        "secret",
        "ci-neg-values",
        "-n",
        "flux-system",
        "--ignore-not-found",
        check=False,
    )

    for p in problems:
        print("::error::" + p)
    if problems:
        sys.exit(1)
    print("negative cases: both rejected as expected")


if __name__ == "__main__":
    main()
