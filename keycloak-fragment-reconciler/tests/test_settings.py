"""Configuration, and specifically the shapes the chart actually renders.

pydantic-settings JSON-decodes list-typed fields before any validator runs, so
`TIER_ROLES=scout-user,scout-admin` -- the only form a Helm template renders
naturally -- needs `NoDecode` to reach the field at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_origin

import pytest
from pydantic_settings import NoDecode

from conftest import HOSTNAME, FakeK8s, FakeKeycloak, fragment_text

from scout_keycloak_fragment_reconciler import metrics
from scout_keycloak_fragment_reconciler.core import Reconciler
from scout_keycloak_fragment_reconciler.settings import (
    ENV_PREFIX,
    FRAGMENT_LABEL,
    RequiredSettings,
    Settings,
)

# Exactly what helm/keycloak-fragment-reconciler/templates/deployment.yaml
# renders, so a change to either side fails here rather than on a cluster.
CHART_ENV = {
    "KEYCLOAK_URL": "http://keycloak-service:8080",
    "REALM": "scout",
    "CLIENT_ID": "fragment_reconciler_svc",
    "CLIENT_SECRET_FILE": (
        "/var/run/secrets/keycloak-fragment-reconciler/fragment_reconciler_svc"
    ),
    "SERVER_HOSTNAME": "scout.example.edu",
    "TIER_ROLES": "scout-user,scout-admin",
    "WATCHED_NAMESPACES": "",
    "RESYNC_SECONDS": "300",
    "ORPHAN_GRACE_SECONDS": "300",
    "DEBOUNCE_SECONDS": "2",
    "DRY_RUN": "false",
    "PORT": "8080",
    "LOG_LEVEL": "INFO",
}

CHART_DEPLOYMENT = (
    Path(__file__).resolve().parents[2]
    / "helm/keycloak-fragment-reconciler/templates/deployment.yaml"
)

# The fragment label is the contract every app labels its ConfigMap against, so
# it is a constant with a field rather than a value a site may turn.
NOT_A_KNOB = {"FRAGMENT_LABEL"}


@pytest.fixture
def env(monkeypatch):
    def apply(**overrides: str):
        for key in list(CHART_ENV) + list(overrides):
            monkeypatch.delenv(ENV_PREFIX + key, raising=False)
        for key, value in {**CHART_ENV, **overrides}.items():
            monkeypatch.setenv(ENV_PREFIX + key, value)

    return apply


class TestTheChartsEnvironment:
    def test_it_parses(self, env):
        env()
        s = RequiredSettings()
        assert s.tier_roles == ["scout-user", "scout-admin"]
        assert s.watched_namespaces == []
        assert s.resync_seconds == 300
        assert s.dry_run is False
        assert s.label_selector == f"{FRAGMENT_LABEL}=true"

    def test_an_empty_allowlist_watches_nothing(self, env):
        """The chart's default, and the reason it creates no ClusterRole:
        installing the chart alone must not reconcile a realm."""
        env()
        s = RequiredSettings()
        assert not s.watches_all
        assert not s.watches("anything")
        assert not s.watches("")

    def test_an_empty_allowlist_is_not_a_list_with_one_empty_name(self, env):
        """`[""]` reads as configured-and-narrowed rather than unset, which is
        the difference between a warning at startup and silence."""
        env()
        assert RequiredSettings().watched_namespaces == []

    def test_all_watches_everything(self, env):
        """What both deploy lanes set. `ALL` is not a legal namespace name --
        they are lowercase -- so it cannot shadow a real one."""
        env(WATCHED_NAMESPACES="ALL")
        s = RequiredSettings()
        assert s.watches_all
        assert s.watches("anything")
        assert s.watches("")

    def test_all_is_case_sensitive(self, env):
        """`all` is a namespace name, and a plausible typo for the sentinel.
        Reading it as the sentinel would widen the scope on a typo."""
        env(WATCHED_NAMESPACES="all")
        s = RequiredSettings()
        assert not s.watches_all
        assert not s.watches("kube-system")

    def test_a_populated_allowlist_narrows(self, env):
        env(WATCHED_NAMESPACES="scout-demo, xnat ")
        s = RequiredSettings()
        assert s.watched_namespaces == ["scout-demo", "xnat"]
        assert s.watches("xnat")
        assert not s.watches("kube-system")

    def test_all_cannot_be_mixed_with_namespace_names(self, env):
        """Either half is a guess at what the operator meant, and guessing
        wide is a silent scope widening."""
        env(WATCHED_NAMESPACES="ALL,xnat")
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert f"{ENV_PREFIX}WATCHED_NAMESPACES" in str(exit_info.value)


class TestEveryFieldIsReachable:
    """A field no lane renders is a setting no operator can set.

    The chart is the only thing that writes this environment -- Ansible and
    Flux both pass it values -- so a variable missing from the Deployment is
    missing from every install.
    """

    def rendered(self) -> set[str]:
        text = CHART_DEPLOYMENT.read_text(encoding="utf-8")
        return set(re.findall(rf"{ENV_PREFIX}([A-Z0-9_]+)", text))

    def test_the_chart_renders_a_variable_for_every_field(self):
        fields = {name.upper() for name in RequiredSettings.model_fields}
        unreachable = sorted(fields - self.rendered() - NOT_A_KNOB)
        assert unreachable == [], f"no lane can set: {unreachable}"

    def test_the_fixture_is_what_the_chart_renders(self):
        """CHART_ENV stands in for the rendered pod, so it must not drift from
        the template whose shapes it claims to be testing."""
        assert self.rendered() == set(CHART_ENV)


class TestRequiredFields:
    def test_a_missing_credential_names_the_variable(self, env, capsys):
        env()
        import os

        del os.environ[ENV_PREFIX + "CLIENT_SECRET_FILE"]
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert f"{ENV_PREFIX}CLIENT_SECRET_FILE" in str(exit_info.value)

    def test_a_missing_hostname_names_the_variable(self, env):
        import os

        env()
        del os.environ[ENV_PREFIX + "SERVER_HOSTNAME"]
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert f"{ENV_PREFIX}SERVER_HOSTNAME" in str(exit_info.value)

    def test_plain_settings_needs_neither(self):
        """So the translator tests and golden files need no invented
        credential."""
        assert Settings().client_secret_file == ""


class TestOperatorErrors:
    def test_a_bad_integer_exits_with_the_variable_name(self, env):
        env(RESYNC_SECONDS="soon")
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        message = str(exit_info.value)
        assert f"{ENV_PREFIX}RESYNC_SECONDS" in message
        assert "integer" in message

    def test_a_json_list_is_refused_rather_than_mangled(self, env):
        """Splitting `["a","b"]` on commas yields `['["a"', '"b"]']`, which
        looks like a list and is junk -- it would surface later as a tier role
        that never matches anything."""
        env(TIER_ROLES='["scout-user","scout-admin"]')
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert "not JSON" in str(exit_info.value)

    def test_no_field_is_json_decoded_from_the_environment(self):
        """pydantic-settings raises SettingsError -- before the model, so
        outside the ValidationError that names the variable -- for a complex
        field it cannot JSON-decode. `CommaList` opts every one of them out;
        a plain `list[str]` field would reopen that path."""
        decoded = [
            name
            for name, field in RequiredSettings.model_fields.items()
            if get_origin(field.annotation) is not None
            and NoDecode not in field.metadata
        ]
        assert decoded == [], f"annotate with CommaList: {decoded}"


class TestResyncDisabled:
    def test_minus_one_is_accepted(self, env):
        env(RESYNC_SECONDS="-1")
        assert RequiredSettings().resync_seconds == -1

    def test_zero_is_accepted(self, env):
        """It parks on the watch exactly as -1 does."""
        env(RESYNC_SECONDS="0")
        assert RequiredSettings().resync_seconds == 0


class TestBounds:
    def exits_naming(self, env, variable: str, value: str) -> None:
        env(**{variable: value})
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert f"{ENV_PREFIX}{variable}" in str(exit_info.value)

    def test_a_resync_below_minus_one_exits_with_the_variable_name(self, env):
        self.exits_naming(env, "RESYNC_SECONDS", "-30")

    def test_a_privileged_port_exits_with_the_variable_name(self, env):
        """The pod runs as uid 65532, so binding 80 would be a PermissionError
        with no variable named."""
        self.exits_naming(env, "PORT", "80")

    def test_port_zero_exits_with_the_variable_name(self, env):
        """An ephemeral port never matches the containerPort the probes hit,
        so the pod would never go Ready."""
        self.exits_naming(env, "PORT", "0")

    def test_a_port_above_the_range_exits_with_the_variable_name(self, env):
        self.exits_naming(env, "PORT", "70000")

    def test_the_charts_port_is_accepted(self, env):
        env(PORT="8080")
        assert RequiredSettings().port == 8080

    def test_a_negative_grace_exits_with_the_variable_name(self, env):
        self.exits_naming(env, "ORPHAN_GRACE_SECONDS", "-1")

    def test_a_negative_debounce_exits_with_the_variable_name(self, env):
        self.exits_naming(env, "DEBOUNCE_SECONDS", "-0.5")


class TestMetricsAreWellFormed:
    """Rendering must not raise, and no two series may share a label set.

    A parse failure has no clientId to report, so two broken documents in one
    ConfigMap are the case that can collide.
    """

    def reconciler(self, k8s) -> Reconciler:
        return Reconciler(
            Settings(
                server_hostname=HOSTNAME,
                watched_namespaces=["ALL"],
            ),
            k8s,
            FakeKeycloak(),
        )

    def series(self, reconciler) -> list[str]:
        return [
            line.split(" ")[0]
            for line in metrics.render(reconciler).splitlines()
            if line.startswith(f"{metrics.PREFIX}_client_state{{")
        ]

    def test_two_broken_documents_are_two_distinct_series(self):
        k8s = FakeK8s()
        item = k8s.add_fragment("not: a fragment", key="one.yaml")
        item["data"]["two.yaml"] = "also: not a fragment"
        k8s.configmaps = [item]
        reconciler = self.reconciler(k8s)
        reconciler.reconcile_once()

        series = self.series(reconciler)
        assert len(series) == 2
        assert len(set(series)) == 2, f"duplicate label set: {series}"
        assert all('client_id=""' in s for s in series)
        assert {'document="one.yaml"', 'document="two.yaml"'} == {
            next(part for part in s.split(",") if "document=" in part).rstrip("}")
            for s in series
        }

    def test_an_applied_client_reports_its_client_id(self):
        k8s = FakeK8s()
        k8s.add_fragment(fragment_text())
        k8s.add_secret("s")
        reconciler = self.reconciler(k8s)
        reconciler.reconcile_once()

        text = metrics.render(reconciler)
        assert 'client_id="hello"' in text
        assert 'state="applied"' in text
        assert 'document=""' in text, "an applied client has no document to name"
