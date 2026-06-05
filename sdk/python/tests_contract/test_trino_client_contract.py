"""Contract tests against the REAL trino-python-client + requests libraries.

The unit suite in tests/ stubs both libraries, so it cannot catch a behavior
change in either one. The Jupyter pass-through model depends on a three-link
chain that lives in those libraries, not in our code:

  1. trino.dbapi.Connection(user=None) must NOT backfill a default user
     (e.g. getpass.getuser()).
  2. The client puts that None user in the X-Trino-User header slot.
  3. requests' Session merge drops None-valued headers, so no X-Trino-User
     goes on the wire and Trino derives the session user from the JWT
     principal (jwt.principal-field=preferred_username).

If a trino-python-client upgrade ever backfills a default user, these tests
fail loudly instead of every Jupyter kernel silently sending
`X-Trino-User: jovyan` alongside the user's JWT (which Trino would treat as
an impersonation attempt and deny).

Kept out of tests/ because that suite's conftest stubs `trino` and
`requests` in sys.modules at collection time. Run separately:

    cd sdk/python && pytest tests_contract/ -v

(requires the real `trino` and `requests` packages -- CI installs them.)
"""

import requests
import trino.dbapi

STATEMENT_URL = "https://trino.invalid:8443/v1/statement"


def _prepared_headers(user):
    """The headers requests would actually send for a connection's request."""
    conn = trino.dbapi.Connection(host="trino.invalid", port=8443, user=user)
    headers = conn._create_request().http_headers
    session = requests.Session()
    prepared = session.prepare_request(
        requests.Request("POST", STATEMENT_URL, headers=headers)
    )
    return prepared.headers


def test_connection_without_user_does_not_backfill_a_default():
    conn = trino.dbapi.Connection(host="trino.invalid", port=8443, user=None)
    assert conn._client_session.user is None


def test_no_x_trino_user_header_on_the_wire_when_user_is_none():
    # The Jupyter pass-through path: no header means Trino falls back to the
    # JWT principal for the session user.
    assert "X-Trino-User" not in _prepared_headers(user=None)


def test_x_trino_user_header_present_for_impersonation_user():
    # The Voila path: the impersonated username must go on the wire.
    assert _prepared_headers(user="alice")["X-Trino-User"] == "alice"
