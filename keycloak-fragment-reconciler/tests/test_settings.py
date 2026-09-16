"""Configuration, and specifically the shapes the chart actually renders.

pydantic-settings JSON-decodes list-typed fields before any validator runs, so
`TIER_ROLES=scout-user,scout-admin` -- the only form a Helm template renders
naturally -- needs `NoDecode` to reach the field at all.
"""

from __future__ import annotations

import pytest

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
    "CLIENT_SECRET": "from-the-secret",
    "SERVER_HOSTNAME": "scout.example.edu",
    "TIER_ROLES": "scout-user,scout-admin",
    "WATCHED_NAMESPACES": "",
    "RESYNC_SECONDS": "300",
    "ORPHAN_GRACE_SECONDS": "300",
    "DRY_RUN": "false",
    "PORT": "8080",
    "LOG_LEVEL": "INFO",
}


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

    def test_an_empty_allowlist_watches_everything(self, env):
        env()
        s = RequiredSettings()
        assert s.watches("anything")
        assert s.watches("")

    def test_an_empty_allowlist_is_not_a_list_with_one_empty_name(self, env):
        """`[""]` would match no namespace and silently disable discovery."""
        env()
        assert RequiredSettings().watched_namespaces == []

    def test_a_populated_allowlist_narrows(self, env):
        env(WATCHED_NAMESPACES="scout-demo, xnat ")
        s = RequiredSettings()
        assert s.watched_namespaces == ["scout-demo", "xnat"]
        assert s.watches("xnat")
        assert not s.watches("kube-system")


class TestRequiredFields:
    def test_a_missing_credential_names_the_variable(self, env, capsys):
        env()
        import os

        del os.environ[ENV_PREFIX + "CLIENT_SECRET"]
        with pytest.raises(SystemExit) as exit_info:
            RequiredSettings()
        assert f"{ENV_PREFIX}CLIENT_SECRET" in str(exit_info.value)

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
        assert Settings().client_secret == ""


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

    def test_an_undecodable_value_is_not_a_traceback(self, env):
        """pydantic-settings raises SettingsError before the model for
        anything it cannot decode, and that path has to exit cleanly too."""
        env(TIER_ROLES="fine")
        assert RequiredSettings().tier_roles == ["fine"]


class TestResyncDisabled:
    def test_minus_one_is_accepted(self, env):
        env(RESYNC_SECONDS="-1")
        assert RequiredSettings().resync_seconds == -1


class TestMetricsAreWellFormed:
    """Rendering must not raise, and no two series may share a label set.

    A parse failure has no clientId to report, so two broken documents in one
    ConfigMap are the case that can collide.
    """

    def reconciler(self, k8s) -> Reconciler:
        return Reconciler(
            Settings(server_hostname=HOSTNAME, client_secret="x"), k8s, FakeKeycloak()
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
