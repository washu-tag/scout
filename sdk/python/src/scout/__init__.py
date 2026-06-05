"""Scout platform SDK.

Public API for querying Scout's data lake from notebooks and playbooks.
Identity is detected from the runtime context (JupyterHub or Voila).

Read-only by design. Write paths (e.g. reviewer annotations) are
playbook-runtime helpers shipped with the Voila chart, not part of this
package.

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
    """Return the OIDC-authed user's preferred_username, or None if no
    identity is available in the current context (Voila preheat,
    misconfigured deployment, etc.)."""
    user = resolve_audit_user()
    return None if user == "anonymous" else user


__all__ = ["connect", "current_user", "query"]
