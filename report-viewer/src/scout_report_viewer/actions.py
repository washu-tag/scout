"""Extensible per-search action buttons (issue #739 PoC).

Report-viewer's search-detail toolbar currently hardcodes every button in
the frontend. This module proves out a backend-declared contract instead:
a button is data (`ActionDescriptor`), filtered by the caller's role
server-side, and rendered generically by the SPA - the same shape ADR 0034
(#636) used for launchpad chips, adapted for actions that can be gated per
search rather than always-static links.

PoC scope: `_CATALOG` is a hardcoded list, not runtime discovery. Real
discovery (labelled ConfigMaps + a sidecar, mirroring ADR 0034) is
deliberately deferred until this contract shape is validated - see the
issue for the full design discussion.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, model_validator


class ActionDescriptor(BaseModel):
    """One toolbar button. `action_type` decides how the SPA handles a click:

    - `open-url`: the SPA opens `url` (real navigation, not `window.open()`,
      plus an always-shown copy-link fallback - see
      `frontend/src/openResult.ts`). Whether the popup actually succeeds is
      controlled by the destination's own Cross-Origin-Opener-Policy header
      (e.g. `popup-friendly-security-headers` in
      `ansible/roles/traefik/tasks/main.yaml`), not anything declared here.
    - `client`: the SPA looks up `client_handler` in a small local registry
      of page-specific logic (e.g. building a CSV from the currently
      loaded/filtered rows) it cannot receive from a backend descriptor.
      An unregistered handler costs just that action, not the toolbar
      (mirrors ADR 0034's per-chip graceful degradation).
    """

    id: str
    title: str
    icon: str = "app"
    tone: str = "indigo"
    weight: int = 100
    action_type: Literal["open-url", "client"]
    url: str | None = None
    required_role: str | None = None
    client_handler: str | None = None

    @model_validator(mode="after")
    def _check_action_type_fields(self) -> "ActionDescriptor":
        if self.action_type == "open-url":
            if not self.url or not _is_safe_action_url(self.url):
                raise ValueError(f"action {self.id!r}: open-url requires a safe http(s) url")
        elif self.action_type == "client" and not self.client_handler:
            raise ValueError(f"action {self.id!r}: client action requires client_handler")
        return self


def _is_safe_action_url(url: str) -> bool:
    """http(s) with a real host only. Mirrors ADR 0034's destination
    validation for launchpad chips (no `javascript:`/`data:` schemes) -
    catalog strings are untrusted by definition once this stops being a
    hardcoded list."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


_CATALOG: list[ActionDescriptor] = [
    ActionDescriptor(
        id="docs-link",
        title="Scout Docs",
        icon="book",
        tone="indigo",
        weight=100,
        action_type="open-url",
        url="https://washu-scout.readthedocs.io/en/latest/",
    ),
    ActionDescriptor(
        id="download-csv",
        title="Download CSV",
        icon="download",
        tone="slate",
        weight=10,
        action_type="client",
        client_handler="download-csv",
    ),
    # Demonstrates role-gating only - required_role="report-viewer-admin"
    # never matches on a real token today (report-viewer has no Keycloak
    # client of its own yet, see auth.py), so this is exercised in tests
    # rather than a live deploy until that's provisioned.
    ActionDescriptor(
        id="admin-diagnostics-poc",
        title="Admin Diagnostics (PoC)",
        icon="shield",
        tone="rose",
        weight=200,
        action_type="open-url",
        url="https://washu-scout.readthedocs.io/en/latest/",
        required_role="report-viewer-admin",
    ),
]


def list_actions(user_roles: frozenset[str]) -> list[ActionDescriptor]:
    """Role-filtered, weight-sorted actions visible to this caller.

    Server-side filtering only - visibility is UX, not the authorization
    boundary (ADR 0034's framing for launchpad chips, unchanged here): a
    real backend-calling action must still independently enforce the same
    role check at its own endpoint, since a hidden action's URL is not
    itself a secret.
    """
    visible = [d for d in _CATALOG if d.required_role is None or d.required_role in user_roles]
    return sorted(visible, key=lambda d: (d.weight, d.title, d.id))
