"""Superset DB_CONNECTION_MUTATOR for Trino with per-user impersonation.

Pattern: Superset connects to Trino as the `superset_svc` Keycloak service
principal (client_credentials grant), and impersonates the logged-in
Superset user via the X-Trino-User HTTP header. Trino's OPA policy
permits superset_svc to impersonate any user; the actual data-access
decision uses the impersonated user's Keycloak attributes
(allowed_facilities, redact_select_identifiers).

Token caching: a single service-principal token is reused across all
connections until ~80% of its lifetime has elapsed, then refreshed. The
cache is process-local — each Superset worker mints its own token.
"""

import os
import time
import threading
import requests
from flask import has_request_context
from trino.auth import JWTAuthentication

_TOKEN_CACHE: dict = {"access_token": None, "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()
_REFRESH_BEFORE_EXPIRY_SECONDS = 60


def _mint_token() -> str:
    """Fetch a fresh client_credentials token from Keycloak."""
    response = requests.post(
        os.environ["KEYCLOAK_TOKEN_URL"],
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["KEYCLOAK_SUPERSET_SVC_CLIENT_ID"],
            "client_secret": os.environ["KEYCLOAK_SUPERSET_SVC_CLIENT_SECRET"],
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    _TOKEN_CACHE["access_token"] = payload["access_token"]
    _TOKEN_CACHE["expires_at"] = (
        time.time() + payload["expires_in"] - _REFRESH_BEFORE_EXPIRY_SECONDS
    )
    return _TOKEN_CACHE["access_token"]


def _get_token() -> str:
    """Return a cached token if still valid, else refresh."""
    with _TOKEN_LOCK:
        if _TOKEN_CACHE["access_token"] and time.time() < _TOKEN_CACHE["expires_at"]:
            return _TOKEN_CACHE["access_token"]
        return _mint_token()


def _in_celery_task() -> bool:
    """True when executing inside a Celery worker task.

    Async SQL Lab, Alerts & Reports, and cache warm-up run queries in a
    Celery worker with no Flask request context, but under override_user()
    (see superset/sql_lab.py get_sql_results), so `username` here is the real
    querying user and the connection should still impersonate.

    Detected via Celery's task-local stack: `current_task` is a Proxy over
    _task_stack.top, which is None outside a task -- so the `.request`
    attribute access raises and we return False. Inside a task it resolves to
    the running task whose `request.id` is set. (`current_task is None` is NOT
    usable: the Proxy is never `is None`.)
    """
    try:
        from celery import current_task

        return current_task.request.id is not None
    except Exception:
        return False


def DB_CONNECTION_MUTATOR(
    uri, params, username, security_manager, source
):  # noqa: N802
    """Inject service-principal JWT + user impersonation into Trino connections.

    Called by Superset on every database connection open. The second
    argument is `params` — the kwargs dict passed to
    `sqlalchemy.create_engine(...)`. DB-API connection args go in
    `params["connect_args"]`, not at the top level (SQLAlchemy raises
    TypeError on unrecognized top-level kwargs).

    For non-Trino backends this is a no-op. For Trino we set:
      - connect_args["auth"]: JWTAuthentication(<service token>) — the
        trino-python-client wires this into the http session's auth
        attribute and adds Authorization: Bearer to every request.
      - connect_args["http_scheme"]: "https" — explicit so URL construction
        targets the HTTPS listener (the URL query param sometimes drops).
      - connect_args["verify"]: path to the Trino CA bundle (the trino
        client passes this through to requests for cert verification).
      - connect_args["user"]: the Superset logged-in user; trino-python-
        client sends it as X-Trino-User. OPA verifies superset_svc is
        allowed to impersonate, then evaluates all subsequent operations
        as the impersonated user.
    """
    if uri.get_backend_name() != "trino":
        return uri, params
    connect_args = params.setdefault("connect_args", {})
    connect_args["auth"] = JWTAuthentication(_get_token())
    connect_args.setdefault("http_scheme", "https")
    connect_args.setdefault("verify", os.environ.get("TRINO_CA_CERT", True))
    # Set X-Trino-User to impersonate the end user in any *real-user* context:
    #   - a Flask request (Superset UI, sync SQL Lab, API, dashboard render)
    #   - a Celery task (async SQL Lab, Alerts & Reports, cache warm-up) --
    #     these run under override_user(), so `username` is the real querying
    #     user even though there's no request context.
    #
    # The one context we deliberately skip is the bare import CLI -- the
    # scout-dashboards-import Job's `superset import-dashboards -u admin`,
    # which is neither a request nor a Celery task. Its `username` is the
    # Superset bootstrap admin, which doesn't exist in OPA's data.users;
    # skipping impersonation lets the connection fall through to the JWT's
    # own principal (Trino user-mapping strips `service-account-` to leave
    # `superset_svc`, which OPA's is_system_identity rule allows) so the
    # import's SHOW CATALOGS / SHOW TABLES dataset validation works.
    #
    # Gating on request-context alone would wrongly skip the Celery paths and
    # clamp every async query to zero rows; gating on username alone can't
    # tell the bootstrap admin apart from a real user named "admin".
    if username and (has_request_context() or _in_celery_task()):
        connect_args["user"] = username
    return uri, params
