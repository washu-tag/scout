import io

import pytest
import yaml
from conftest import (  # noqa: F401
    configmap_yaml,
    fragment_yaml,
    setup,
    write_fragment,
)

from scout_app_manager import cli


@pytest.fixture
def in_pod(setup, monkeypatch):  # noqa: F811
    """`status` and `reconcile` run inside the pod; stand in for that wiring."""
    service, fragments, _ = setup
    monkeypatch.setattr(cli, "build_service", lambda: service)
    monkeypatch.setattr(cli, "build_store", lambda: (service.settings, service.store))
    return service, fragments


def test_validate_reports_the_effect_without_a_cluster(tmp_path, capsys):
    path = tmp_path / "fragment.yaml"
    path.write_text(fragment_yaml("hello"), encoding="utf-8")

    assert cli.main(["validate", str(path), "--domain", "scout.example.edu"]) == 0

    out = capsys.readouterr().out
    assert "OK" in out
    assert "client hello" in out
    assert "grants    hello-user -> everyone in scout-user" in out


def test_validate_fails_on_an_invalid_fragment(tmp_path, capsys):
    path = tmp_path / "fragment.yaml"
    path.write_text(fragment_yaml("hello", fullScopeAllowed=True), encoding="utf-8")
    assert cli.main(["validate", str(path)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_validate_accepts_the_wrapping_configmap(tmp_path, capsys):
    """What an author has in their chart is the ConfigMap, not the fragment."""
    path = tmp_path / "fragment.yaml"
    path.write_text(configmap_yaml(), encoding="utf-8")

    assert cli.main(["validate", str(path)]) == 0

    out = capsys.readouterr().out
    assert "OK" in out
    # The ConfigMap's own coordinates, so the ref matches what `status` prints
    # in the cluster rather than the name of a file on someone's laptop.
    assert "my-service/my-service-keycloak" in out
    assert "client hello" in out


def test_validate_picks_the_fragments_out_of_a_rendered_chart(tmp_path, capsys):
    """`helm template .` emits the whole chart; only fragments are ours."""
    stream = "\n---\n".join(
        [
            yaml.safe_dump({"apiVersion": "apps/v1", "kind": "Deployment"}),
            configmap_yaml(name="my-service-config", labelled=False, data={"a": "b"}),
            configmap_yaml(),
            yaml.safe_dump({"apiVersion": "v1", "kind": "Service"}),
        ]
    )
    path = tmp_path / "rendered.yaml"
    path.write_text(stream, encoding="utf-8")

    assert cli.main(["validate", str(path)]) == 0

    out = capsys.readouterr().out
    assert "my-service/my-service-keycloak" in out
    assert "my-service-config" not in out


def test_validate_reads_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(configmap_yaml()))
    assert cli.main(["validate", "-"]) == 0
    assert "my-service/my-service-keycloak" in capsys.readouterr().out


def test_validate_reports_a_fragment_that_is_not_labelled(tmp_path, capsys):
    """The silent failure otherwise: the reconciler never sees it at all."""
    path = tmp_path / "fragment.yaml"
    path.write_text(configmap_yaml(labelled=False), encoding="utf-8")

    assert cli.main(["validate", str(path)]) == 1

    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "not labelled keycloak.scout.xnat.org/fragment" in out


def test_validate_reports_a_data_key_the_sidecar_writes_but_scan_skips(
    tmp_path, capsys
):
    path = tmp_path / "fragment.yaml"
    path.write_text(
        configmap_yaml(data={"fragment": fragment_yaml()}), encoding="utf-8"
    )

    assert cli.main(["validate", str(path)]) == 1

    out = capsys.readouterr().out
    assert "no data key ends in .yaml, .yml or .json (fragment)" in out


def test_validate_uses_the_sites_signout_url_when_given_one(tmp_path, capsys):
    path = tmp_path / "fragment.yaml"
    path.write_text(configmap_yaml(), encoding="utf-8")

    assert (
        cli.main(
            [
                "validate",
                str(path),
                "--domain",
                "scout.example.edu",
                "--signout-url",
                "https://sso.example.edu/oauth2/sign_out",
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "redirect  https://sso.example.edu/oauth2/sign_out" in out
    assert "auth.scout.example.edu" not in out


def test_validate_says_nothing_was_found_rather_than_passing(tmp_path, capsys):
    path = tmp_path / "rendered.yaml"
    path.write_text(yaml.safe_dump({"kind": "Deployment"}), encoding="utf-8")

    assert cli.main(["validate", str(path)]) == 1
    assert "no fragments found" in capsys.readouterr().err


def test_validate_does_not_traceback_on_a_missing_file(capsys):
    assert cli.main(["validate", "/nope/fragment.yaml"]) == 1
    assert "cannot read /nope/fragment.yaml" in capsys.readouterr().err


def test_status_reports_every_fragment(in_pod, capsys):
    service, fragments = in_pod
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()

    assert cli.main(["status"]) == 0

    out = capsys.readouterr().out
    assert "INSTALLED  scout-demo/hello" in out
    assert "client hello" in out


def test_status_reports_why_a_fragment_was_excluded(in_pod, capsys):
    service, fragments = in_pod
    write_fragment(
        fragments, "evil", "takeover", fragment_yaml("launchpad", roles=[], grants={})
    )
    service.reconcile_once()

    assert cli.main(["status"]) == 0

    out = capsys.readouterr().out
    assert "REJECTED   evil/takeover" in out
    assert "already exists in the base realm" in out


def test_status_never_writes(in_pod, capsys):
    """An operator looking must not become a second writer."""
    service, fragments = in_pod
    service.settings.apply_mode = "apply"
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    jobs, secrets = dict(service.client.jobs), dict(service.client.secrets)

    assert cli.main(["status"]) == 0

    assert service.client.jobs == jobs
    assert service.client.secrets == secrets


def test_status_says_so_when_nothing_has_been_published(in_pod, capsys):
    assert cli.main(["status"]) == 1
    assert "no status published yet" in capsys.readouterr().err


def test_status_flags_a_fragment_edited_since_the_last_apply(in_pod, capsys):
    service, fragments = in_pod
    write_fragment(fragments, "scout-demo", "hello", fragment_yaml("hello"))
    service.reconcile_once()
    write_fragment(
        fragments, "scout-demo", "hello", fragment_yaml("hello", pkce="required")
    )

    assert cli.main(["status"]) == 0

    assert "differs; not yet applied" in capsys.readouterr().out


def test_reconcile_once_reports_the_result(in_pod, capsys):
    assert cli.main(["reconcile"]) == 0
    assert "reconciled at" in capsys.readouterr().out
