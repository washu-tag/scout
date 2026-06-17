"""OWUI new-user webhook receiver.

Open WebUI fires its admin notification webhook on new signups
(https://docs.openwebui.com/features/administration/webhooks/). Without
this receiver every new user lands in `pending` and an admin has to
flip them by hand before they can use Scout. We consume the webhook
and call OWUI's user-update API ourselves, using the existing scout-
deploy admin credentials that already live in the cluster.

Wiring (deploy time):

  * datasets-service mounts BOOTSTRAP_PASSWORD from the
    `open-webui-secrets` Secret as `DATASETS_OWUI_ADMIN_PASSWORD`.
  * OWUI's `WEBHOOK_URL` admin setting is pointed at
    `http://datasets-service.<ns>:8000/webhooks/owui-new-user`.
  * NetworkPolicy on datasets-service permits ingress from the OWUI
    pod (currently no NetworkPolicy — open by default).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from .. import metrics
from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/owui-new-user", status_code=status.HTTP_204_NO_CONTENT)
async def owui_new_user(request: Request) -> None:
    """Receive OWUI's admin notification on new signups and auto-enable
    the user. No-op on missing config; 503s loudly so the OWUI side
    surfaces the failure in its own logs."""
    if not settings.owui_admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OWUI admin credentials not configured",
        )

    # Optional shared-secret check. Empty secret = skip the check
    # (acceptable when ingress is already constrained by NetworkPolicy).
    if settings.owui_webhook_secret:
        got = request.headers.get("X-Scout-Webhook-Secret", "")
        if got != settings.owui_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="webhook secret mismatch",
            )

    try:
        body = await request.json()
    except Exception:
        log.exception("could not parse webhook body")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid JSON",
        )

    # OWUI sends `user` as a JSON-encoded STRING (model_dump_json),
    # NOT a dict — see open_webui/utils/oauth.py: `'user':
    # user.model_dump_json(exclude_none=True)`. Older versions sent a
    # dict directly. Handle both shapes.
    action = body.get("action") or body.get("event") or ""
    raw_user = body.get("user")
    if isinstance(raw_user, str):
        try:
            import json as _json

            user_obj = _json.loads(raw_user)
        except Exception:
            user_obj = {}
    elif isinstance(raw_user, dict):
        user_obj = raw_user
    else:
        user_obj = {}
    new_user_id = (user_obj or {}).get("id") or body.get("user_id")
    new_user_email = (user_obj or {}).get("email") or body.get("email")
    if not (new_user_id or new_user_email):
        log.info("webhook fired but no user id/email", extra={"action": action})
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="other", result="skipped").inc()
        return None
    if action and "signup" not in action.lower() and "new" not in action.lower():
        # Unknown event — log and ignore. We only auto-enable on signup.
        log.info("ignoring non-signup webhook", extra={"action": action})
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="other", result="skipped").inc()
        return None

    try:
        await _enable_user(new_user_id=new_user_id, new_user_email=new_user_email)
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="signup", result="enabled").inc()
    except Exception:
        log.exception(
            "auto-enable failed",
            extra={"new_user_id": new_user_id, "new_user_email": new_user_email},
        )
        metrics.OWUI_WEBHOOK_EVENTS.labels(action="signup", result="error").inc()
        # Don't raise — OWUI's webhook treats 5xx as failure and will
        # retry; we want this to be best-effort.
    return None


async def _enable_user(*, new_user_id: str | None, new_user_email: str | None) -> None:
    """Sign in as admin, find the new user, set role=user."""
    base = settings.owui_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as c:
        # Sign in as the bootstrap admin.
        signin = await c.post(
            f"{base}/api/v1/auths/signin",
            json={
                "email": settings.owui_admin_email,
                "password": settings.owui_admin_password,
            },
        )
        if signin.status_code != 200:
            raise RuntimeError(
                f"OWUI signin failed: {signin.status_code} {signin.text}"
            )
        admin_token = signin.json().get("token")
        if not admin_token:
            raise RuntimeError("OWUI signin returned no token")
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Resolve user id from email if needed.
        target_id = new_user_id
        if not target_id and new_user_email:
            users = await c.get(f"{base}/api/v1/users/", headers=headers)
            if users.status_code != 200:
                raise RuntimeError(
                    f"OWUI users list failed: {users.status_code} {users.text}"
                )
            for u in users.json() or []:
                if isinstance(u, dict) and u.get("email") == new_user_email:
                    target_id = u.get("id")
                    break
            if not target_id:
                log.info(
                    "new user not found in OWUI users list",
                    extra={"email": new_user_email},
                )
                return

        # Flip role to "user". OWUI's user-update endpoint is
        # `POST /api/v1/users/{id}/update` and accepts a
        # UserUpdateForm — any combination of role/name/email/password.
        # Sending only `role` is fine; the handler updates only the
        # fields present in form_data.
        payload: dict[str, Any] = {"role": "user"}
        upd = await c.post(
            f"{base}/api/v1/users/{target_id}/update",
            headers=headers,
            json=payload,
        )
        if upd.status_code not in (200, 204):
            raise RuntimeError(f"OWUI role update failed: {upd.status_code} {upd.text}")
        log.info(
            "OWUI user auto-enabled",
            extra={"user_id": target_id, "email": new_user_email},
        )
