"""Terminal local build defects fail promptly; normal DAG startup keeps waiting."""

import copy

import pytest

import wait_ready as waiter


MISSING_LOGO = (
    "kustomize build failed: loading KV pairs: file sources: [logo.png=scout.jpg]: "
    "open /tmp/kustomization-2963167852/base/oauth2-proxy/scout.jpg: no such file or directory"
)


def kustomization(name="oauth2-proxy", reason="BuildFailed", message=MISSING_LOGO):
    return {
        "metadata": {"name": name, "namespace": "flux-system", "generation": 3},
        "spec": {},
        "status": {
            # A failed reconciliation can leave this behind the spec generation.
            "observedGeneration": 2,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": reason,
                    "message": message,
                    "observedGeneration": 3,
                }
            ],
        },
    }


def ready(name):
    obj = kustomization(name, "ReconciliationSucceeded", "Applied revision")
    obj["status"]["observedGeneration"] = 3
    obj["status"]["conditions"][0]["status"] = "True"
    return obj


def test_missing_packaged_logo_is_terminal_despite_old_top_generation():
    assert waiter.local_build_failure(kustomization()) == MISSING_LOGO


@pytest.mark.parametrize(
    "message",
    [
        "kustomize build failed: MalformedYAMLError: yaml: line 4: did not find expected key",
        "kustomize build failed: json: unknown field badField",
        "kustomize build failed: may not add resource with an already registered id: ConfigMap.v1.x",
        "kustomize build failed: failed to find unique target for patch Deployment.v1.apps/x",
        "kustomize build failed: add operation does not apply: doc is missing path: /spec/values/x",
    ],
)
def test_other_local_compile_errors_are_terminal(message):
    assert waiter.local_build_failure(kustomization(message=message)) == message


@pytest.mark.parametrize(
    "reason,message",
    [
        ("DependencyNotReady", "dependency 'flux-system/keycloak-realm' is not ready"),
        ("ArtifactFailed", "source artifact not found"),
        (
            "ReconciliationFailed",
            'no matches for kind "HelmRelease" in version "helm.toolkit.fluxcd.io/v2"',
        ),
        ("HealthCheckFailed", "timeout waiting for Deployment/oauth2-proxy"),
        ("Progressing", "Reconciliation in progress"),
        (
            "BuildFailed",
            "post build failed for 'oauth2-proxy': ConfigMap/cluster-vars not found",
        ),
        (
            "BuildFailed",
            "kustomize build failed: accumulating resources: git fetch https://example.test/base: i/o timeout",
        ),
        (
            "BuildFailed",
            "kustomize build failed: unknown error, not classified as deterministic",
        ),
    ],
)
def test_initial_controller_dag_and_remote_fetch_failures_keep_waiting(reason, message):
    assert (
        waiter.local_build_failure(kustomization(reason=reason, message=message))
        is None
    )


@pytest.mark.parametrize("generation", [2, None, True])
def test_old_or_missing_condition_generation_is_not_terminal(generation):
    obj = kustomization()
    obj["status"]["conditions"][0]["observedGeneration"] = generation
    assert waiter.local_build_failure(obj) is None


def test_success_or_suspension_cannot_be_mistaken_for_a_current_failed_build():
    obj = kustomization()
    obj["status"]["conditions"][0]["status"] = "True"
    assert waiter.local_build_failure(obj) is None
    obj = kustomization()
    obj["spec"]["suspend"] = True
    assert waiter.local_build_failure(obj) is None


def test_selected_compile_failure_exits_before_sleep_or_helm_poll(monkeypatch, capsys):
    def items(kind):
        assert kind == "kustomizations.kustomize.toolkit.fluxcd.io"
        return [
            kustomization(),
            kustomization(
                "edge-on-prem", "DependencyNotReady", "oauth2-proxy not ready"
            ),
        ]

    monkeypatch.setattr(waiter, "items", items)
    monkeypatch.setattr(
        waiter.time, "sleep", lambda _: pytest.fail("terminal build must not sleep")
    )
    with pytest.raises(SystemExit) as exc:
        waiter.wait_for_ready(["oauth2-proxy", "edge-on-prem"], 2700)
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "::error::Kustomization flux-system/oauth2-proxy cannot build:" in output
    assert "scout.jpg: no such file or directory" in output


def test_transient_startup_and_stale_build_recover_without_false_failure(monkeypatch):
    old_build = kustomization()
    old_build["status"]["conditions"][0]["observedGeneration"] = 2
    states = [
        [],  # root has not created its children yet
        [kustomization(reason="ArtifactFailed", message="source artifact not found")],
        [
            kustomization(
                reason="ReconciliationFailed", message="no matches for kind HelmRelease"
            )
        ],
        [old_build],
        [ready("oauth2-proxy")],
    ]
    polls, sleeps = [], []

    def items(kind):
        if kind.startswith("helmreleases"):
            return []
        polls.append(kind)
        return copy.deepcopy(states.pop(0))

    monkeypatch.setattr(waiter, "items", items)
    monkeypatch.setattr(waiter.time, "sleep", sleeps.append)
    waiter.wait_for_ready(["oauth2-proxy"], 2700)
    assert len(polls) == 5
    assert sleeps == [15] * 4


def test_unselected_negative_case_does_not_fail_the_wait(monkeypatch):
    monkeypatch.setattr(
        waiter, "items", lambda _: [ready("launchpad"), kustomization("ci-neg-build")]
    )
    monkeypatch.setattr(
        waiter.time, "sleep", lambda _: pytest.fail("selected leg is Ready")
    )
    waiter.wait_for_ready(["launchpad"], 2700)
