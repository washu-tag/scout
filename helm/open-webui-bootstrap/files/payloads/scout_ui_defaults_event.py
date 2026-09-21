"""
title: Scout UI Defaults
description: Forces Scout's per-user OWUI interface settings for every user.
"""

# OWUI defaults these ui.* settings per-user with no admin-global override, so
# this event function forces them on: user.created (new users; the only auth
# event OWUI emits for SSO) and function.enabled/updated on itself (deploy-time
# backfill of existing users — fired when the bootstrap Job re-seeds this
# function, no restart needed).
# Replaces the report-viewer signup webhook + SQL backfill (ADR 0029).

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("scout.ui_defaults")

# Per-user ui.* settings the report-viewer iframe requires, all forced true.
_IFRAME_FLAGS = {
    "iframeSandboxAllowSameOrigin": True,
    "iframeSandboxAllowForms": True,
}

_CHANGELOG_OFF = {"showChangelog": False}


async def _ensure_settings(user_id: str, forced: dict) -> bool:
    """Force `forced` for one user; return True iff a write happened.

    Rebuilds the whole `ui` sub-object because update_user_settings_by_id()
    shallow-merges only at the top level.
    """
    from open_webui.models.users import Users  # noqa: PLC0415 (lazy: OWUI-runtime only)

    user = await Users.get_user_by_id(user_id)
    if user is None:
        return False
    settings = user.settings.model_dump() if user.settings else {}
    ui = dict(settings.get("ui") or {})
    if all(ui.get(key) is value for key, value in forced.items()):
        return False
    ui.update(forced)
    await Users.update_user_settings_by_id(user_id, {"ui": ui})
    return True


class Event:
    class Valves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Master switch. When off, the function makes no changes.",
        )
        show_release_notes: bool = Field(
            default=False,
            description="Leave the What's New modal to each admin's own Settings toggle.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _forced_settings(self) -> dict:
        forced = dict(_IFRAME_FLAGS)
        if not self.valves.show_release_notes:
            forced.update(_CHANGELOG_OFF)
        return forced

    async def event(
        self,
        event: dict,
        __event_name__: Optional[str] = None,
        __id__: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if not self.valves.enabled:
            return
        if __event_name__ == "user.created":
            await self._on_user_created(event)
        elif __event_name__ in ("function.enabled", "function.updated"):
            # Fires when the bootstrap Job (re)seeds THIS function each deploy —
            # the deploy-time signal to backfill existing users. Filter to self so
            # a sibling function's update never triggers a sweep.
            if (event.get("subject") or {}).get("id") == __id__:
                await self._backfill_all()

    async def _on_user_created(self, event: dict) -> None:
        # New user's id is on actor (fall back to subject).
        actor = event.get("actor") or {}
        subject = event.get("subject") or {}
        user_id = actor.get("id") or subject.get("id")
        if not user_id:
            log.warning("user.created carried no user id: %s", event)
            return
        if await _ensure_settings(user_id, self._forced_settings()):
            log.info("ui defaults applied to new user %s", user_id)

    async def _backfill_all(self) -> None:
        # Idempotent, so no lock needed despite firing once per replica:
        # concurrent sweeps converge and only the first writes.
        from open_webui.models.users import Users  # noqa: PLC0415

        forced = self._forced_settings()
        result = await Users.get_users()
        users = result.get("users", []) if isinstance(result, dict) else (result or [])
        changed = 0
        for u in users:
            user_id = getattr(u, "id", None)
            if user_id is None and isinstance(u, dict):
                user_id = u.get("id")
            if user_id and await _ensure_settings(user_id, forced):
                changed += 1
        log.info("ui defaults backfill: %d/%d users updated", changed, len(users))
