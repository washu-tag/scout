#!/usr/bin/env python3
"""
Unit tests for the pluggable_app_registries filter plugin.

Run from ansible/: pytest tests/unit/filter_plugins/test_pluggable_app_registries.py -v
"""

import base64
import sys
from pathlib import Path

filter_plugins_path = Path(__file__).parent.parent.parent.parent / "filter_plugins"
sys.path.insert(0, str(filter_plugins_path))

import pytest
from pluggable_app_registries import (
    pluggable_app_auths,
    pluggable_app_image_routes,
    pluggable_app_login_for,
    pluggable_app_logins,
)

CHART = "oci://ghcr.io/vendor/charts/app"
LOGIN = {"username": "svc", "password": "s3cret"}


def entry(**kwargs):
    return dict(
        {"name": "app", "chart": CHART, "registry": None, "registries": None}, **kwargs
    )


def login(*prefixes, **kwargs):
    return dict(LOGIN, prefixes=list(prefixes), **kwargs)


class TestLogins:
    def test_no_logins(self):
        assert pluggable_app_logins(entry()) == []

    def test_registry_covers_the_chart_host(self):
        logins = pluggable_app_logins(entry(registry=LOGIN))
        assert logins == [
            {
                "host": "ghcr.io",
                "prefixes": ["ghcr.io"],
                "username": "svc",
                "password": "s3cret",
                "project": "app-app",
            }
        ]

    def test_registries_get_a_project_each(self):
        logins = pluggable_app_logins(
            entry(
                registries=[login("ghcr.io/a"), login("ghcr.io/b"), login("quay.io/c")]
            )
        )
        assert [l["project"] for l in logins] == ["app-app", "app-app_2", "app-app_3"]
        assert [l["host"] for l in logins] == ["ghcr.io", "ghcr.io", "quay.io"]

    def test_later_projects_differ_from_other_apps_first(self):
        two = pluggable_app_logins(
            entry(registries=[login("ghcr.io/a"), login("quay.io/b")])
        )
        other = pluggable_app_logins(
            entry(name="app-2", registries=[login("ghcr.io/c")])
        )
        assert two[1]["project"] != other[0]["project"]

    def test_local_chart_with_registries(self):
        logins = pluggable_app_logins(
            entry(chart="./charts/app", registries=[login("ghcr.io/vendor")])
        )
        assert logins[0]["prefixes"] == ["ghcr.io/vendor"]

    def test_trailing_slash_is_dropped(self):
        logins = pluggable_app_logins(entry(registries=[login("ghcr.io/vendor/")]))
        assert logins[0]["prefixes"] == ["ghcr.io/vendor"]

    def test_host_with_port(self):
        logins = pluggable_app_logins(
            entry(registries=[login("registry.example:5000/team")])
        )
        assert logins[0]["host"] == "registry.example:5000"

    def test_values_become_strings(self):
        logins = pluggable_app_logins(
            entry(
                registries=[
                    {"prefixes": ["ghcr.io/v"], "username": "u", "password": 1234}
                ]
            )
        )
        assert logins[0]["password"] == "1234"

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            (dict(registry=LOGIN, registries=[login("ghcr.io/v")]), "not both"),
            (dict(chart="./charts/app", registry=LOGIN), "oci:// chart"),
            (dict(registry=["svc"]), "{username, password}"),
            (dict(registries=[]), "non-empty list"),
            (dict(registries={"prefixes": ["ghcr.io"]}), "non-empty list"),
            (dict(registries=["ghcr.io"]), "{prefixes, username, password}"),
            (dict(registries=[dict(LOGIN)]), "non-empty prefixes"),
            (dict(registries=[login()]), "non-empty prefixes"),
            (dict(registries=[login("")]), "non-empty string"),
            (dict(registries=[login("https://ghcr.io/v")]), "scheme"),
            (dict(registries=[login("ghcr.io/v/app:1.0")]), "tag or digest"),
            (dict(registries=[login("ghcr.io/v/app@sha256:abc")]), "tag or digest"),
            (dict(registries=[login("vendor/app")]), "registry host"),
            (dict(registries=[login("/ghcr.io/v")]), "registry host"),
            (dict(registries=[login("ghcr.io/a", "quay.io/b")]), "share a host"),
            (
                dict(registries=[{"prefixes": ["ghcr.io/v"], "username": "svc"}]),
                "username and a password",
            ),
            (dict(registries=[login("ghcr.io/v", "ghcr.io/v/sub")]), "overlap"),
            (dict(registries=[login("ghcr.io/v"), login("ghcr.io/v")]), "overlap"),
            (dict(registries=[login("ghcr.io"), login("ghcr.io/v")]), "overlap"),
        ],
    )
    def test_rejects(self, kwargs, message):
        with pytest.raises(
            ValueError, match=message.replace("{", r"\{").replace("}", r"\}")
        ):
            pluggable_app_logins(entry(**kwargs))

    def test_sibling_prefixes_do_not_overlap(self):
        logins = pluggable_app_logins(
            entry(registries=[login("ghcr.io/vendor"), login("ghcr.io/vendor-labs")])
        )
        assert len(logins) == 2

    def test_errors_never_show_the_password(self):
        with pytest.raises(ValueError) as error:
            pluggable_app_logins(entry(registries=[login("ghcr.io/a", "quay.io/b")]))
        assert "s3cret" not in str(error.value)


class TestLoginFor:
    LOGINS = pluggable_app_logins(
        entry(registries=[login("ghcr.io/vendor"), login("quay.io")])
    )

    @pytest.mark.parametrize(
        "ref, project",
        [
            ("oci://ghcr.io/vendor/charts/app", "app-app"),
            ("ghcr.io/vendor/app:1.0", "app-app"),
            ("ghcr.io/vendor", "app-app"),
            ("quay.io/anyone/thing@sha256:abc", "app-app_2"),
            ("ghcr.io/vendor-labs/app:1.0", None),
            ("ghcr.io/other/app", None),
            ("docker.io/vendor/app", None),
        ],
    )
    def test_matches_whole_segments(self, ref, project):
        found = pluggable_app_login_for(self.LOGINS, ref)
        assert (found and found["project"]) == project


class TestAuths:
    def test_one_entry_per_prefix(self):
        logins = pluggable_app_logins(
            entry(
                registries=[
                    login("ghcr.io/a", "ghcr.io/b"),
                    login("quay.io", username="q", password="p"),
                ]
            )
        )
        auths = pluggable_app_auths(logins)["auths"]
        assert sorted(auths) == ["ghcr.io/a/", "ghcr.io/b/", "quay.io"]
        assert base64.b64decode(auths["ghcr.io/a/"]["auth"]).decode() == "svc:s3cret"
        assert auths["quay.io"] == {
            "username": "q",
            "password": "p",
            "auth": base64.b64encode(b"q:p").decode(),
        }


class TestImageRoutes:
    def test_routes_to_the_login_project(self):
        logins = pluggable_app_logins(
            entry(
                registries=[
                    login("ghcr.io/vendor", "ghcr.io/vendor2/sub"),
                    login("quay.io"),
                ]
            )
        )
        assert pluggable_app_image_routes(logins, "harbor.example.org") == {
            "ghcr.io/vendor/": "harbor.example.org/app-app/vendor/",
            "ghcr.io/vendor2/sub/": "harbor.example.org/app-app/vendor2/sub/",
            "quay.io/": "harbor.example.org/app-app_2/",
        }
