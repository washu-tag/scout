"""The API client's failure surface.

Everything the reconciler does to the cluster goes through `Client.request`,
and every caller guards it with `except ApiError` -- several of them promising
in their docstrings that they cannot fail a reconcile. So what that guard
covers is the interesting part, not the happy path.
"""

import httpx2 as httpx
import pytest

from scout_app_manager import k8s
from scout_app_manager.k8s import ApiError, Client, TransportError


class Refusing:
    """An httpx client where the request never reaches an answer."""

    def __init__(self, exc):
        self.exc = exc

    def request(self, *args, **kwargs):
        raise self.exc

    def stream(self, *args, **kwargs):
        raise self.exc


@pytest.fixture
def client(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("t0ken", encoding="utf-8")
    monkeypatch.setattr(k8s, "TOKEN_PATH", token)
    monkeypatch.setattr(k8s, "CA_PATH", tmp_path / "absent-ca.crt")
    return Client()


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
def test_a_transport_failure_arrives_as_an_api_error(client, monkeypatch, exc):
    """The likeliest failure of all, and the one `except ApiError` has to cover."""
    monkeypatch.setattr(client, "_http", Refusing(exc))

    with pytest.raises(ApiError) as caught:
        client.get_configmap("scout-core", "keycloak-base-realm")

    assert isinstance(caught.value, TransportError)
    assert caught.value.status == 0
    assert "unreachable" in str(caught.value)


def test_an_unreachable_api_is_not_a_404(client, monkeypatch):
    """`_get` turns 404 into None; an unanswered request must not look like one."""
    monkeypatch.setattr(client, "_http", Refusing(httpx.ConnectError("refused")))

    with pytest.raises(ApiError):
        client.get_secret("scout-core", "keycloak-client-secrets")


def test_a_stream_that_cannot_connect_is_an_api_error(client, monkeypatch):
    monkeypatch.setattr(client, "_http", Refusing(httpx.ConnectError("refused")))

    with pytest.raises(TransportError):
        with client.stream("/api/v1/namespaces/scout-core/secrets?watch=1"):
            pass


def test_deleting_a_job_waits_for_its_pods(client, monkeypatch):
    """Foreground propagation, so waiting for the Job is waiting for config-cli."""
    seen = []
    monkeypatch.setattr(
        client, "request", lambda method, path, **kw: seen.append((method, path)) or {}
    )

    client.delete_job("scout-core", "app-manager-apply-abc")

    assert seen == [
        (
            "DELETE",
            "/apis/batch/v1/namespaces/scout-core/jobs/app-manager-apply-abc"
            "?propagationPolicy=Foreground",
        )
    ]
