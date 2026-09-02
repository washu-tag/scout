import pytest
from conftest import fragment_yaml, setup, write_fragment  # noqa: F401

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
