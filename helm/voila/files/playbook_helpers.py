"""Helpers for Scout playbook code (the Voila-rendered notebooks under
analytics/notebooks/). Chart-shipped, not part of the public scout SDK,
because the operations here only work inside a Voila playbook context.

Currently exposes:

    connect_writeback() -> trino.dbapi.Connection
        Connect to the write-enabled Trino instance for reviewer-annotation
        writes (ADR 0019). Voila-only - the trino-rw NetworkPolicy
        allow-lists Voila-labeled pods; a Jupyter kernel calling this would
        hit a TCP timeout.

Future helpers (e.g. save_annotations) would also live here.
"""

import os

import trino.dbapi
from scout._identity import resolve_audit_user


def connect_writeback() -> trino.dbapi.Connection:
    """Return a Trino DB-API connection to the write-enabled instance.

    trino-rw is unauthenticated inside its NetworkPolicy boundary; the
    `user` here is an audit label, not a credential. Fails OPEN to
    user='anonymous' - failing closed would block a reviewer's annotation
    save on a transient header-propagation hiccup, which is a worse
    outcome than a less-precise audit entry. A missing forwarded username
    is already logged at request time by voila_runtime._voila_runtime_get.
    """
    user = resolve_audit_user()
    return trino.dbapi.connect(
        host=os.environ.get("TRINO_RW_HOST", "trino-rw.scout-extractor"),
        port=int(os.environ.get("TRINO_RW_PORT", "8080")),
        http_scheme="http",
        catalog=os.environ.get("TRINO_CATALOG", "delta"),
        schema=os.environ.get("TRINO_SCHEMA", "default"),
        user=user,
    )
