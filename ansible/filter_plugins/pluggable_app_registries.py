#!/usr/bin/env python3
"""Registry logins for a ``pluggable_apps`` entry (ADR 0041).

``pluggable_app_logins`` checks an entry's ``registry`` or ``registries`` and
returns its logins, each with the image prefixes it covers and the Harbor project
that proxies them on an air-gapped site. ``pluggable_app_login_for`` finds the
login for a chart or image reference. ``pluggable_app_auths`` builds the pull
Secret's ``.dockerconfigjson``, and ``pluggable_app_image_routes`` maps each
prefix to its Harbor project.

Error messages name prefixes, never credentials.
"""

import base64


def _host(prefix):
    return prefix.split("/", 1)[0]


def _check_prefix(app, prefix):
    if not isinstance(prefix, str) or not prefix.rstrip("/"):
        raise ValueError("%s: a registry prefix must be a non-empty string" % app)
    prefix = prefix.rstrip("/")
    if "://" in prefix:
        raise ValueError(
            "%s: registry prefix %r must not have a scheme" % (app, prefix)
        )
    if "@" in prefix or ("/" in prefix and ":" in prefix.rsplit("/", 1)[1]):
        raise ValueError(
            "%s: registry prefix %r must not have a tag or digest" % (app, prefix)
        )
    host = _host(prefix)
    if not ("." in host or ":" in host or host == "localhost"):
        raise ValueError(
            "%s: registry prefix %r must start with a registry host, such as ghcr.io"
            % (app, prefix)
        )
    return prefix


def _covers(prefix, ref):
    return ref == prefix or ref.startswith(prefix + "/")


def pluggable_app_logins(entry):
    """The entry's logins, as ``[{host, prefixes, username, password, project}]``.

    ``entry`` holds the app's ``name``, ``chart``, ``registry`` and
    ``registries``; the last two may be None.
    """
    app = entry.get("name") or "(no name)"
    registry = entry.get("registry")
    registries = entry.get("registries")
    chart = entry.get("chart") or ""

    if registry is not None and registries is not None:
        raise ValueError("%s: set registry or registries, not both" % app)
    if registry is not None:
        if not chart.startswith("oci://"):
            raise ValueError(
                "%s: registry needs an oci:// chart; for a local chart, use registries"
                " with explicit prefixes" % app
            )
        if not isinstance(registry, dict):
            raise ValueError("%s: registry must be {username, password}" % app)
        registries = [dict(registry, prefixes=[_host(chart[len("oci://") :])])]
    if registries is None:
        return []
    if not isinstance(registries, list) or not registries:
        raise ValueError("%s: registries must be a non-empty list of logins" % app)

    logins = []
    for index, login in enumerate(registries):
        if not isinstance(login, dict):
            raise ValueError(
                "%s: each registries entry must be {prefixes, username, password}" % app
            )
        prefixes = login.get("prefixes")
        if not isinstance(prefixes, list) or not prefixes:
            raise ValueError(
                "%s: registries entry %d needs a non-empty prefixes list"
                % (app, index + 1)
            )
        prefixes = [_check_prefix(app, p) for p in prefixes]
        hosts = sorted({_host(p) for p in prefixes})
        if len(hosts) > 1:
            raise ValueError(
                "%s: one login's prefixes must share a host, not %s"
                % (app, ", ".join(hosts))
            )
        if not login.get("username") or not login.get("password"):
            raise ValueError(
                "%s: the login for %s needs a username and a password"
                % (app, ", ".join(prefixes))
            )
        logins.append(
            {
                "host": hosts[0],
                "prefixes": prefixes,
                "username": str(login["username"]),
                "password": str(login["password"]),
                "project": (
                    "app-%s" % app if index == 0 else "app-%s-%d" % (app, index + 1)
                ),
            }
        )

    every = [p for login in logins for p in login["prefixes"]]
    for i, p in enumerate(every):
        for q in every[i + 1 :]:
            if _covers(p, q) or _covers(q, p):
                raise ValueError(
                    "%s: registry prefixes %r and %r overlap" % (app, p, q)
                )
    return logins


def pluggable_app_login_for(logins, ref):
    """The login whose prefix covers ``ref`` (an image or chart), or None."""
    ref = ref[len("oci://") :] if ref.startswith("oci://") else ref
    for login in logins:
        if any(_covers(p, ref) for p in login["prefixes"]):
            return login
    return None


def pluggable_app_auths(logins):
    """A ``.dockerconfigjson`` with one ``auths`` entry per prefix.

    Kubelet compares a path prefix as a raw string, so a path ends in ``/`` to
    match whole segments.
    """
    auths = {}
    for login in logins:
        auth = base64.b64encode(
            ("%s:%s" % (login["username"], login["password"])).encode("utf-8")
        ).decode("ascii")
        for prefix in login["prefixes"]:
            key = prefix + "/" if "/" in prefix else prefix
            auths[key] = {
                "username": login["username"],
                "password": login["password"],
                "auth": auth,
            }
    return {"auths": auths}


def pluggable_app_image_routes(logins, harbor_host):
    """``{"<prefix>/": "<harbor_host>/<project>/<path>/"}`` for every prefix."""
    routes = {}
    for login in logins:
        for prefix in login["prefixes"]:
            path = prefix.split("/", 1)[1] + "/" if "/" in prefix else ""
            routes[prefix + "/"] = "%s/%s/%s" % (harbor_host, login["project"], path)
    return routes


class FilterModule(object):
    """Ansible filter plugin class."""

    def filters(self):
        return {
            "pluggable_app_logins": pluggable_app_logins,
            "pluggable_app_login_for": pluggable_app_login_for,
            "pluggable_app_auths": pluggable_app_auths,
            "pluggable_app_image_routes": pluggable_app_image_routes,
        }
