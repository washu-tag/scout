"""Unit tests for the SDK's top-level query/connect/current_user surface: the
JupyterHub access-token pass-through (no impersonation), its caching and
refresh, and the anonymous fail-safe.
"""

import logging

import pytest

import scout
from scout import _identity, _query

from conftest import (
    FakeHttpError,
    jwt_with_claims,
    pandas_stub,
    requests_stub,
    set_hub_auth_state,
    sqlalchemy_stub,
    trino_dbapi_stub,
)


def _connect_kwargs():
    """kwargs passed to the (stubbed) trino.dbapi.connect on the last call."""
    return trino_dbapi_stub.connect.call_args.kwargs


def test_connect_passes_the_users_token_without_impersonation():
    set_hub_auth_state()

    scout.connect()

    kwargs = _connect_kwargs()
    # No X-Trino-User: connect() omits `user` so the trino client sends no
    # impersonation header and Trino reads identity from the JWT principal. A
    # stray user here would become an impersonation attempt.
    assert "user" not in kwargs
    # SDK uses a dynamic JWT auth that re-fetches per request; assert on type
    # rather than equality with a static-token JWTAuthentication.
    assert isinstance(kwargs["auth"], _query._DynamicJWTAuthentication)
    assert kwargs["http_scheme"] == "https"
    assert kwargs["host"] == "trino.scout-analytics"
    assert kwargs["port"] == 8443
    assert kwargs["verify"] == "/etc/trino-ca/ca.crt"


def test_token_is_cached_across_calls():
    set_hub_auth_state(expires_in=3600)

    provider = _identity._resolve_provider()
    provider()
    provider()
    provider()

    # One Hub fetch, reused until near expiry.
    assert requests_stub.get.call_count == 1


def test_token_refreshes_after_expiry():
    # expires_in below the refresh-before window -> immediately considered stale.
    set_hub_auth_state(expires_in=1)

    provider = _identity._resolve_provider()
    provider()
    provider()

    assert requests_stub.get.call_count == 2


def test_auth_session_pins_ca_against_env_bundle_override():
    # set_http_session must disable trust_env: requests substitutes
    # REQUESTS_CA_BUNDLE/CURL_CA_BUNDLE for the request-level verify and that
    # overrides session.verify (where the trino client puts TRINO_CA_CERT) —
    # package-proxy deployments set those vars to the staging bundle, which
    # lacks Trino's CA.
    import types

    auth = _query._DynamicJWTAuthentication(lambda: "tok")
    session = types.SimpleNamespace(trust_env=True, auth=None)

    auth.set_http_session(session)

    assert session.trust_env is False
    assert isinstance(session.auth, _query._DynamicBearerAuth)


def test_is_unauthorized_detects_401_direct_and_chained():
    assert _query._is_unauthorized(FakeHttpError("error 401: b'JWT expired'"))
    # SQLAlchemy/pandas wrap the trino error; we walk the chain.
    try:
        try:
            raise FakeHttpError("error 401: unauthorized")
        except FakeHttpError as inner:
            raise ValueError("Execution failed on sql ...") from inner
    except ValueError as wrapped:
        assert _query._is_unauthorized(wrapped)


def test_is_unauthorized_excludes_403_and_non_auth_errors():
    # 403 is an authorization denial, not a stale bearer -- must NOT retry.
    assert not _query._is_unauthorized(FakeHttpError("error 403: Access Denied"))
    assert not _query._is_unauthorized(FakeHttpError("error 500: internal"))
    assert not _query._is_unauthorized(ValueError("table not found"))


def test_with_auth_retry_invalidates_and_retries_once_on_401():
    set_hub_auth_state()
    provider = _identity._resolve_provider()
    provider()  # prime the cached bearer
    assert provider._token is not None

    calls = []

    def run():
        calls.append(1)
        if len(calls) == 1:
            raise FakeHttpError("error 401: b'JWT expired'")
        return "rows"

    assert _query._with_auth_retry(run) == "rows"
    assert len(calls) == 2  # failed once, retried once
    assert provider._token is None  # cache was invalidated between attempts


def test_with_auth_retry_does_not_retry_on_403():
    set_hub_auth_state()
    provider = _identity._resolve_provider()
    provider()
    assert provider._token is not None

    calls = []

    def run():
        calls.append(1)
        raise FakeHttpError("error 403: Access Denied")

    with pytest.raises(FakeHttpError):
        _query._with_auth_retry(run)
    assert len(calls) == 1  # denial is not retried
    assert provider._token is not None  # and the bearer is NOT invalidated


def test_with_auth_retry_passes_non_auth_errors_through_without_retry():
    calls = []

    def run():
        calls.append(1)
        raise ValueError("not an auth problem")

    with pytest.raises(ValueError):
        _query._with_auth_retry(run)
    assert len(calls) == 1  # no retry


def test_provider_invalidate_forces_refetch():
    set_hub_auth_state(expires_in=3600)
    provider = _identity._resolve_provider()
    provider()
    provider()
    assert requests_stub.get.call_count == 1  # cached

    _identity.invalidate()
    provider()
    assert requests_stub.get.call_count == 2  # re-fetched after invalidate


def test_bearer_is_user_token_fetched_from_hub():
    token = set_hub_auth_state()

    bearer = _identity._resolve_provider()()

    assert bearer == token
    # Fetches the owning user's record with the kernel's own Hub API token.
    call = requests_stub.get.call_args
    assert call.args[0] == "http://hub:8081/hub/api/users/alice"
    assert call.kwargs["headers"]["Authorization"] == "token hub-tok"


def test_raises_when_auth_state_has_no_token():
    set_hub_auth_state(include_token=False)

    with pytest.raises(RuntimeError, match="auth_state has no access_token"):
        _identity._resolve_provider()()


def test_current_user_is_the_hub_user(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USER", "hub-user")
    assert scout.current_user() == "hub-user"


def test_current_user_none_when_no_identity(monkeypatch):
    # The public current_user() returns None for "anonymous" so callers can
    # distinguish "no identity" from a real user named anonymous.
    monkeypatch.delenv("JUPYTERHUB_USER")
    assert scout.current_user() is None


# --- _is_near_expiry: the cache-refresh clock and its fail-safe branches ---


def test_is_near_expiry_buffer_scales_with_lifetime_when_iat_present(monkeypatch):
    # With iat present the buffer is 20% of the lifetime (exp - iat), so the
    # refresh point scales with the lifespan instead of the fixed 60s fallback.
    now = 1_000_000.0
    monkeypatch.setattr(_identity.time, "time", lambda: now)
    # 1000s token, 100s (10%) remaining -> refresh, even though 100s is well
    # outside the 60s fixed fallback; proves the buffer tracks the lifetime.
    assert (
        _identity._is_near_expiry(jwt_with_claims({"iat": now - 900, "exp": now + 100}))
        is True
    )
    # Same 1000s token with 300s (30%) remaining -> not yet (300 > 20%).
    assert (
        _identity._is_near_expiry(jwt_with_claims({"iat": now - 700, "exp": now + 300}))
        is False
    )


@pytest.mark.parametrize(
    "undecodable",
    [
        "not-a-jwt",  # no dots: too few segments to unpack
        "only.two",  # 2 segments
        "a.b.c.d",  # 4 segments
        "header.%%%not-base64%%%.sig",  # middle segment isn't valid base64
        "header.bm90anNvbg.sig",  # valid base64, but decodes to non-JSON
    ],
)
def test_is_near_expiry_treats_undecodable_token_as_stale(undecodable):
    # A token we can't parse must be treated as expired (fail-safe). Returning
    # False would pin a garbage cached token forever, never re-fetching.
    assert _identity._is_near_expiry(undecodable) is True


@pytest.mark.parametrize("claims", [{}, {"exp": None}, {"exp": "soon"}])
def test_is_near_expiry_treats_missing_or_nonnumeric_exp_as_stale(claims):
    # Decodable, but no usable numeric exp -> can't time refresh, so treat as stale.
    assert _identity._is_near_expiry(jwt_with_claims(claims)) is True


# --- the dynamic bearer: stamps the header and re-fetches per request ---


def test_dynamic_bearer_auth_stamps_bearer_and_refetches_each_request():
    import types

    tokens = iter(["tok-1", "tok-2"])
    auth = _query._DynamicBearerAuth(lambda: next(tokens))

    first = types.SimpleNamespace(headers={})
    auth(first)
    assert first.headers["Authorization"] == "Bearer tok-1"

    # A second request re-invokes the provider, so a rotation performed
    # out-of-band is picked up without rebuilding the engine/connection.
    second = types.SimpleNamespace(headers={})
    auth(second)
    assert second.headers["Authorization"] == "Bearer tok-2"


# --- reactive retry: exactly once, and only through query()'s real path ---


def test_with_auth_retry_gives_up_after_one_retry_on_persistent_401():
    # ADR 0024: retry EXACTLY once. A bearer rejected twice (e.g. the Hub keeps
    # handing back an already-stale token) must surface, not loop.
    set_hub_auth_state()
    provider = _identity._resolve_provider()
    provider()  # prime the cached bearer

    calls = []

    def run():
        calls.append(1)
        raise FakeHttpError("error 401: b'JWT expired'")

    with pytest.raises(FakeHttpError):
        _query._with_auth_retry(run)
    assert len(calls) == 2  # initial attempt + one retry, then give up


def test_get_engine_is_cached_across_calls():
    # One long-lived engine (ADR 0024): the dynamic bearer refreshes per
    # request, so the engine is never disposed/rebuilt on rotation.
    set_hub_auth_state()

    first = _query._get_engine()
    second = _query._get_engine()

    assert first is second
    assert sqlalchemy_stub.create_engine.call_count == 1


def test_query_runs_through_engine_and_self_heals_on_401():
    # End-to-end: query() builds the cached engine and wraps pd.read_sql in the
    # reactive 401 retry, so a bearer rejected in-flight self-heals instead of
    # surfacing to the notebook.
    set_hub_auth_state()

    attempts = []

    def read_sql(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise FakeHttpError("error 401: b'JWT expired'")
        return "dataframe"

    pandas_stub.read_sql.side_effect = read_sql

    assert _query.query("SELECT 1") == "dataframe"
    assert len(attempts) == 2  # failed once (401), retried, succeeded


# --- single-flight: concurrent first calls fetch one token, not N ---


def test_provider_fetches_once_under_concurrent_first_calls():
    # The per-provider lock (ADR 0024) collapses a burst of first-time callers
    # into a single Hub fetch. Without it, threads that all observe _token is
    # None would each fetch.
    import threading
    import time
    from unittest.mock import MagicMock

    def slow_fetch(*args, **kwargs):
        time.sleep(0.02)  # widen the window a missing lock would expose
        response = MagicMock()
        token = jwt_with_claims({"exp": int(time.time()) + 3600})
        response.json.return_value = {
            "name": "alice",
            "auth_state": {"access_token": token},
        }
        response.raise_for_status.return_value = None
        return response

    requests_stub.get.side_effect = slow_fetch

    provider = _identity._resolve_provider()
    gate = threading.Event()
    results, errors = [], []

    def worker():
        gate.wait()  # release all threads at once to maximize overlap
        try:
            results.append(provider())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()

    assert not errors
    assert requests_stub.get.call_count == 1
    assert all(token == results[0] for token in results)


# --- provider resolution + fetch failure surfaces ---


def test_resolve_provider_raises_when_no_identity_in_env(monkeypatch):
    # Outside JupyterHub -> a clear configuration error.
    monkeypatch.delenv("JUPYTERHUB_API_TOKEN")
    with pytest.raises(RuntimeError, match="no identity source detected"):
        _identity._resolve_provider()


def test_hub_fetch_failure_propagates():
    # If the Hub rejects the request, the error must surface via
    # raise_for_status -- never yield an empty/None bearer.
    from unittest.mock import MagicMock

    response = MagicMock()
    response.raise_for_status.side_effect = FakeHttpError("error 503: hub down")
    requests_stub.get.return_value = response

    provider = _identity._resolve_provider()
    with pytest.raises(FakeHttpError):
        provider()


def test_warns_once_when_hub_returns_near_expiry_token(caplog):
    # The Hub controls refresh timing; if it hands back an already-stale token
    # we warn the user (once) to re-login rather than silently re-fetching.
    set_hub_auth_state(expires_in=1)  # near-expiry on arrival

    provider = _identity._resolve_provider()
    with caplog.at_level(logging.WARNING, logger="scout._identity"):
        provider()  # fetch #1 -> warns
        provider()  # token still near-expiry -> fetch #2, but no second warning

    stale_warnings = [
        r for r in caplog.records if "already at/near expiry" in r.message
    ]
    assert len(stale_warnings) == 1
    assert requests_stub.get.call_count == 2  # re-fetched each call, warned once
