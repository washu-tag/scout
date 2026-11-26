"""Scout platform SDK.

Public API for querying Scout's data lake from JupyterHub notebooks, as the
signed-in user. Read-only by design.

Usage:

    import scout

    df = scout.query("SELECT * FROM reports LIMIT 10")
    df = scout.query(
        "SELECT * FROM reports WHERE sending_facility = :f",
        params={"f": "BJH"},
    )

    with scout.connect() as conn:
        cur = conn.cursor()
        cur.execute("...")

    user = scout.current_user()
"""

from ._identity import resolve_audit_user
from ._query import connect, query


def current_user() -> str | None:
    """Return the JupyterHub user's name, or None if no identity is
    available in the current context (outside JupyterHub, a misconfigured
    deployment)."""
    user = resolve_audit_user()
    return None if user == "anonymous" else user


__all__ = ["connect", "current_user", "query"]
