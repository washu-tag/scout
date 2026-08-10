"""Resolve upstream image refs whose tag is an upstream Helm chart's appVersion.

Two wrapper-chart images take their tag from an UPSTREAM Helm chart's appVersion
rather than a tag var in versions.yaml (temporalio/admin-tools tracks the Temporal
chart; ghcr.io/open-webui/open-webui tracks the Open WebUI chart -- see the
DEFERRED note in upstream-images.txt). versions.yaml pins the CHART version (a
semver range); the image tag is that chart's *resolved* appVersion, matching what
Scout deploys.

The appVersion lookup needs ``helm show chart`` (a network call + the helm
binary), so it can't run here. This stdlib-only resolver just substitutes each
chart-version var from versions.yaml and prints a TSV row
``<image-repo>\t<helm-repo-url>\t<chart>\t<chart-version>`` for the workflow to
feed to ``helm show chart --repo <url> --version <ver> | awk appVersion``.

Reuses resolve_upstream.load_versions so both resolvers parse versions.yaml the
same way.
"""

from __future__ import annotations

import sys

from resolve_upstream import load_versions


def resolve_helm(mapping_path: str, versions_path: str) -> list[tuple[str, str, str, str]]:
    versions = load_versions(versions_path)
    rows: list[tuple[str, str, str, str]] = []
    for raw in open(mapping_path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(
                f"expected '<image-repo> <helm-repo-url> <chart> <chart-version-var>', got: {raw!r}"
            )
        repo, url, chart, var = parts
        if var not in versions:
            raise ValueError(f"{var!r} not found in {versions_path}")
        ver = versions[var]
        if not ver:
            raise ValueError(f"{var!r} is empty in {versions_path}")
        rows.append((repo, url, chart, ver))
    return rows


if __name__ == "__main__":
    for repo, url, chart, ver in resolve_helm(sys.argv[1], sys.argv[2]):
        print(f"{repo}\t{url}\t{chart}\t{ver}")
