"""The couplings between this service and hello-scout's chart.

hello-scout is the worked example apps copy, and each of these breaks silently:
a wrong Secret key or label reads as a fragment that was simply never noticed.

Greps the chart rather than rendering it, because `helm` is not a test
dependency and the values that matter are literals in the template.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout_keycloak_fragment_reconciler.fragment import API_VERSION, KIND, SecretRef
from scout_keycloak_fragment_reconciler.settings import FRAGMENT_LABEL

CHART = Path(__file__).resolve().parents[2] / "helm" / "hello-scout" / "templates"

pytestmark = pytest.mark.skipif(
    not CHART.is_dir(), reason="hello-scout chart is not in this tree"
)


def chart_file(name: str) -> str:
    return (CHART / name).read_text(encoding="utf-8")


def test_the_fragment_label_matches():
    """The label is the whole of discovery. A mismatch is a fragment that is
    never seen, with nothing logged anywhere to say so."""
    assert f"{FRAGMENT_LABEL}: 'true'" in chart_file("fragment.yaml")


def test_the_secret_key_matches_the_default():
    """The chart's Secret template writes this key and its fragment does not
    name one, so the reconciler's default has to be the same string."""
    assert f"{SecretRef.model_fields['key'].default}:" in chart_file(
        "client-secret.yaml"
    )


def test_the_fragment_declares_the_version_this_reconciler_speaks():
    fragment = chart_file("fragment.yaml")
    assert f"apiVersion: {API_VERSION}" in fragment
    assert f"kind: {KIND}" in fragment


def test_the_chart_grants_a_resourcenames_scoped_get_and_nothing_more():
    """The grant travels with the app, which is what lets the reconciler hold
    no standing cluster-wide Secret read. `resourceNames` cannot scope `list`
    or `watch`, so either verb appearing in the grant would mean the narrow
    grant had quietly become a broad one.

    Reads the `verbs:` lines rather than the file, because the chart's own
    comment says the words `list` and `watch` while explaining why it does not
    use them.
    """
    rbac = chart_file("reconciler-rbac.yaml")
    assert "resourceNames:" in rbac
    verbs = [
        line.strip() for line in rbac.splitlines() if line.strip().startswith("verbs:")
    ]
    assert verbs == ["verbs: ['get']"], verbs


def test_the_chart_names_the_reconcilers_service_account():
    """That name is public contract: every pluggable app types it into a
    RoleBinding, so it cannot be renamed without breaking them."""
    values = (CHART.parent / "values.yaml").read_text(encoding="utf-8")
    assert f"reconcilerServiceAccount: {SERVICE_ACCOUNT}" in values
    assert "reconcilerNamespace:" in values


OWN_CHART = CHART.parents[1] / "keycloak-fragment-reconciler"
SERVICE_ACCOUNT = "scout-keycloak-fragment-reconciler"


@pytest.mark.skipif(
    not OWN_CHART.is_dir(), reason="the reconciler chart is not in this tree"
)
class TestAvailabilityFloor:
    """Asserted against the chart, because it is not expressible in Python.

    Two reconcilers writing the same realm objects is the failure this service
    is designed against, and the in-memory grace clock assumes one process.
    """

    def deployment(self) -> str:
        return (OWN_CHART / "templates" / "deployment.yaml").read_text(encoding="utf-8")

    def test_exactly_one_replica_hardcoded(self):
        deployment = self.deployment()
        assert "replicas: 1" in deployment
        # Not templated from values: an operator scaling this up would get two
        # writers, so the knob should not exist.
        assert ".Values.replica" not in deployment

    def test_recreate_not_rolling_update(self):
        """A rolling update briefly runs the old and new pods together, which
        is exactly the two-writer state being avoided."""
        assert "type: Recreate" in self.deployment()

    def test_no_autoscaler_ships_with_the_chart(self):
        kinds = [
            path.name
            for path in (OWN_CHART / "templates").iterdir()
            if "autoscal" in path.name or "hpa" in path.name
        ]
        assert kinds == []

    def test_the_service_account_name_is_the_contracted_one(self):
        values = (OWN_CHART / "values.yaml").read_text(encoding="utf-8")
        assert f"name: {SERVICE_ACCOUNT}" in values

    def test_the_chart_grants_no_configmap_patch(self):
        """Writing status back onto a Flux- or Helm-managed fragment would
        start a revert loop, so the verb is deliberately absent."""
        rbac = (OWN_CHART / "templates" / "rbac.yaml").read_text(encoding="utf-8")
        configmap_rule = rbac.split("resources: ['configmaps']")[1].split("-")[0]
        assert "patch" not in configmap_rule
        assert "create" not in configmap_rule
        assert "delete" not in configmap_rule

    def test_the_chart_grants_no_secret_access_at_all(self):
        """Per-app `resourceNames`-scoped grants replace a standing one."""
        rbac = (OWN_CHART / "templates" / "rbac.yaml").read_text(encoding="utf-8")
        assert "resources: ['secrets']" not in rbac

    def test_the_cluster_read_is_gated_on_watched_namespaces(self):
        """The chart's default watches nothing, and must grant nothing to
        match: `helm install` with no values may not produce a pod holding a
        cluster-wide ConfigMap read. The ServiceAccount stays unconditional,
        since the Deployment runs as it and apps bind to it by name."""
        rbac = (OWN_CHART / "templates" / "rbac.yaml").read_text(encoding="utf-8")
        before, _, gated = rbac.partition("{{- if .Values.watchedNamespaces }}")
        assert gated, "the ClusterRole is no longer conditional"
        assert "kind: ServiceAccount" in before
        assert "kind: ClusterRole\n" in gated
        assert "kind: ClusterRoleBinding" in gated

    def test_the_chart_default_watches_nothing(self):
        values = (OWN_CHART / "values.yaml").read_text(encoding="utf-8")
        assert "\nwatchedNamespaces: []\n" in values
