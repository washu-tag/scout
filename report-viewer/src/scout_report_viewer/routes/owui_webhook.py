"""OWUI signup webhook receiver.

Writes iframeSandboxAllowSameOrigin and iframeSandboxAllowForms to true
on the new user's OWUI Postgres row so the chat iframe renders with
allow-same-origin from the first search. Direct DB write because OWUI
has no admin API for per-user UI settings.
"""

from __future__ import annotations

import json as _json
import logging

import psycopg
from fastapi import APIRouter, HTTPException, Request, status

from .. import metrics
from ..config import settings
from ..logging_setup import scrub_for_log

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/owui-new-user", status_code=status.HTTP_204_NO_CONTENT)
async def owui_new_user(request: Request) -> None:
    if not settings.owui_database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OWUI database URL not configured",
        )

    try:
        body = await request.json()
    except Exception:
        log.exception("could not parse webhook body")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON",
        )

    # OWUI 0.9.6 sends `user` as a JSON-encoded string; decode it.
    action = scrub_for_log(body.get("action") or body.get("event") or "")
    raw_user = body.get("user") or "{}"
    try:
        user_obj = _json.loads(raw_user) if isinstance(raw_user, str) else raw_user
    except Exception:
        user_obj = {}
    new_user_id = scrub_for_log(user_obj.get("id") or body.get("user_id"))
    new_user_email = scrub_for_log(user_obj.get("email") or body.get("email"))

    if not new_user_id:
        log.info("webhook fired with no user id", extra={"action": action})
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="other", result="skipped").inc()
        return None
    if "signup" not in action.lower() and "new" not in action.lower():
        log.info("ignoring non-signup webhook", extra={"action": action})
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="other", result="skipped").inc()
        return None

    try:
        await _apply_iframe_defaults(new_user_id)
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="signup", result="enabled").inc()
        log.info(
            "iframe-defaults applied",
            extra={"user_id": new_user_id, "email": new_user_email},
        )
    except Exception:
        log.exception(
            "iframe-defaults apply failed",
            extra={"user_id": new_user_id, "email": new_user_email},
        )
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="signup", result="error").inc()
    return None


async def _apply_iframe_defaults(user_id: str) -> None:
    """Set iframeSandbox UI flags on the new OWUI user.

    SELECT-then-UPDATE in Python (not jsonb_set) because OWUI seeds
    settings to JSON `null`, which breaks COALESCE-based jsonb_set
    with "cannot set path in scalar"."""
    async with await psycopg.AsyncConnection.connect(
        settings.owui_database_url, autocommit=True
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute('SELECT settings FROM "user" WHERE id = %s', (user_id,))
            row = await cur.fetchone()
            if row is None:
                log.warning("user not in OWUI DB yet", extra={"user_id": user_id})
                return
            current = row[0] if isinstance(row[0], dict) else {}
            ui = current.get("ui") if isinstance(current.get("ui"), dict) else {}
            ui["iframeSandboxAllowSameOrigin"] = True
            ui["iframeSandboxAllowForms"] = True
            current["ui"] = ui
            await cur.execute(
                'UPDATE "user" SET settings = %s::json WHERE id = %s',
                (_json.dumps(current), user_id),
            )
