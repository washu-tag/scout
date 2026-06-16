#!/usr/bin/env python3
"""Custom Jinja2 filters for XNAT plugin Maven resolution.

Used by the nexus and xnat roles to turn coordinate-plugin metadata into the
shapes those roles need:

- ``xnat_maven_proxy``: a plugin ``repo_url`` -> a Nexus Maven proxy-repo
  definition (``{name, remoteUrl, blobStoreName}``). The name is a deterministic
  slug of the URL so re-runs are idempotent and the repo reads sensibly in the
  Nexus UI (rather than an opaque hash).

- ``maven_artifact_path``: a Maven ``-Dartifact`` coordinate
  (``groupId:artifactId:version[:packaging[:classifier]]``) -> the
  repository-relative path to the artifact, e.g.
  ``au/edu/qcif/.../openid-auth-plugin-1.5.0-xpl.jar``. Used by the air-gapped
  preflight to HEAD the artifact through the Nexus group.
"""

import hashlib
import re
from urllib.parse import urlsplit


def _slug(text):
    """Lowercase, collapse non-alnum runs to single hyphens, trim hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def xnat_maven_proxy(
    repo_url, snapshot_repo_urls=None, blob_store_name="default", max_name_len=60
):
    """Build a Nexus Maven proxy-repo definition from a plugin repo URL.

    The name is ``<host>-<path>`` slugified. If that exceeds max_name_len it is
    truncated and suffixed with a short hash of the full URL to keep names
    unique and stable.

    versionPolicy is MIXED when repo_url is in snapshot_repo_urls (a plugin
    opted that repo into snapshots), else RELEASE (the default — release-only).
    """
    if not repo_url:
        raise ValueError("xnat_maven_proxy: empty repo_url")

    parts = urlsplit(repo_url)
    base = _slug((parts.netloc + parts.path) or repo_url)
    if len(base) > max_name_len:
        digest = hashlib.md5(repo_url.encode("utf-8")).hexdigest()[:8]
        base = base[: max_name_len - 9].rstrip("-") + "-" + digest

    version_policy = "MIXED" if repo_url in (snapshot_repo_urls or []) else "RELEASE"
    return {
        "name": base,
        "remoteUrl": repo_url,
        "blobStoreName": blob_store_name,
        "versionPolicy": version_policy,
    }


def maven_artifact_path(coordinates):
    """Repository-relative path for a Maven -Dartifact coordinate string.

    coordinates: groupId:artifactId:version[:packaging[:classifier]]
    (packaging defaults to 'jar'). Example:
      au.edu.qcif.xnat.openid:openid-auth-plugin:1.5.0:jar:xpl
      -> au/edu/qcif/xnat/openid/openid-auth-plugin/1.5.0/openid-auth-plugin-1.5.0-xpl.jar
    """
    fields = coordinates.split(":")
    if len(fields) < 3:
        raise ValueError(
            "maven_artifact_path: expected groupId:artifactId:version[:packaging[:classifier]], "
            "got %r" % coordinates
        )
    group_id, artifact_id, version = fields[0], fields[1], fields[2]
    packaging = fields[3] if len(fields) >= 4 and fields[3] else "jar"
    classifier = fields[4] if len(fields) >= 5 and fields[4] else ""

    filename = "%s-%s%s.%s" % (
        artifact_id,
        version,
        "-" + classifier if classifier else "",
        packaging,
    )
    return "/".join([group_id.replace(".", "/"), artifact_id, version, filename])


class FilterModule(object):
    """Ansible filter plugin class."""

    def filters(self):
        return {
            "xnat_maven_proxy": xnat_maven_proxy,
            "maven_artifact_path": maven_artifact_path,
        }
