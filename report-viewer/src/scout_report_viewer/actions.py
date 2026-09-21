"""Extensible per-search action buttons (issue #739 PoC).

Report-viewer's search-detail toolbar used to hardcode every button in
the frontend. This module proves out a backend-declared contract instead:
a button is data (`ActionDescriptor`), filtered by the caller's Keycloak
group membership server-side, and rendered generically by the SPA - the
same shape ADR 0034
(#636) used for launchpad chips, adapted for actions that can be gated per
search rather than always-static links.

Discovery: `helm/report-viewer`'s chart renders a `catalog.yaml` into a
ConfigMap and mounts it at `settings.action_catalog_path` - the same
"core chips ride a chart-rendered ConfigMap mounted directly into the
pod" delivery ADR 0034 uses for launchpad's *own* tiles (as opposed to
the cross-namespace sidecar-watch mechanism it uses for third-party
contributions, which is a further increment this doesn't attempt yet:
this ConfigMap is owned entirely by report-viewer's own chart, read once
at process start - a ConfigMap edit needs a pod restart to take effect,
no live re-read/TTL snapshot yet).

A site admin can add a genuinely new button - not just toggle a built-in
one - via `values.yaml`'s `actions.custom` list, with no report-viewer
code change or image rebuild: just a values change + `helm upgrade`.
Both `open-url` and `backend-call` are authorable this way (see
`ActionDescriptor` below and `helm/report-viewer/templates/actions-
configmap.yaml`) - a `client` action needs a handler already registered
in report-viewer's own frontend, so it structurally can't be added
through values data alone.

Graded degradation, mirroring ADR 0034: an unparseable file falls back
to `_DEFAULT_CATALOG` entirely (bad document costs its only document);
one invalid entry within an otherwise-valid file is skipped, logged, and
the rest of the catalog still loads (bad chip costs the chip).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from jose import jwt
from pydantic import BaseModel, ValidationError, model_validator

from .config import settings

log = logging.getLogger(__name__)


class ActionDescriptor(BaseModel):
    """One toolbar button. `action_type` decides how the SPA handles a click:

    - `open-url`: the SPA opens `url` (real navigation, not `window.open()`,
      plus an always-shown copy-link fallback - see
      `frontend/src/openResult.ts`). Whether the popup actually succeeds is
      controlled by the destination's own Cross-Origin-Opener-Policy header
      (e.g. `security-headers-sameorigin-popups` in
      `ansible/roles/traefik/tasks/main.yaml`), not anything declared here.
    - `client`: the SPA looks up `client_handler` in a small local registry
      of page-specific logic (e.g. building a CSV from the currently
      loaded/filtered rows) it cannot receive from a backend descriptor.
      An unregistered handler costs just that action, not the toolbar
      (mirrors ADR 0034's per-chip graceful degradation).
    - `backend-call`: the SPA POSTs to report-viewer's generic
      `/api/searches/{id}/actions/{action_id}/invoke`, which forwards the
      search's context to `endpoint_url` (a genuinely separate,
      independently deployed service - #595's "Apps" tier) and expects
      back `{"url": "..."}`, then hands that off to the same `open-url`
      handling. report-viewer never needs to know anything about what a
      given plugin actually does - only that it returns a safe http(s)
      URL. If the action has an invoke token, it's forwarded as
      `X-Report-Viewer-Action-Token` - see `load_invoke_token()` below;
      it is never a field on this model, so it structurally cannot
      round-trip into an API response.
    """

    id: str
    title: str
    weight: int = 100
    action_type: Literal["open-url", "client", "backend-call"]
    url: str | None = None
    required_group: str | None = None
    client_handler: str | None = None
    endpoint_url: str | None = None

    @model_validator(mode="after")
    def _check_action_type_fields(self) -> "ActionDescriptor":
        if self.action_type == "open-url":
            if not self.url or not _is_safe_action_url(self.url):
                raise ValueError(
                    f"action {self.id!r}: open-url requires a safe http(s) url"
                )
        elif self.action_type == "client" and not self.client_handler:
            raise ValueError(
                f"action {self.id!r}: client action requires client_handler"
            )
        elif self.action_type == "backend-call":
            if not self.endpoint_url or not _is_safe_action_url(self.endpoint_url):
                raise ValueError(
                    f"action {self.id!r}: backend-call requires a safe http(s) endpoint_url"
                )
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


# Built-in floor when no ConfigMap is mounted (local dev without the
# chart, or the mount breaking) - ADR 0034's "never an empty page"
# principle. Matches today's actual toolbar exactly: Explain Search and
# Download CSV are what already ship on main, just re-expressed through
# this contract. New demo/example actions belong in the Helm chart's
# rendered catalog or in tests, not baked into this fallback.
_DEFAULT_CATALOG: list[ActionDescriptor] = [
    ActionDescriptor(
        id="explain-search",
        title="Explain Search",
        weight=5,
        action_type="client",
        client_handler="explain-search",
    ),
    ActionDescriptor(
        id="download-csv",
        title="Download CSV",
        weight=10,
        action_type="client",
        client_handler="download-csv",
    ),
]


def _load_catalog_from_file(path: str) -> list[ActionDescriptor] | None:
    """Parse `path` into a validated action list, or None if it's absent
    or unparseable (caller falls back to `_DEFAULT_CATALOG`)."""
    file = Path(path)
    if not file.is_file():
        return None
    try:
        raw = yaml.safe_load(file.read_text())
    except yaml.YAMLError:
        log.exception("action catalog %s: invalid YAML, using built-in defaults", path)
        return None
    # An empty file (e.g. every chart-toggled action disabled) parses to
    # None, which is a legitimate, intentionally-empty catalog - distinct
    # from a genuinely malformed shape (a dict/string instead of a list),
    # which still falls back to _DEFAULT_CATALOG.
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        log.error(
            "action catalog %s: expected a YAML list, using built-in defaults", path
        )
        return None

    catalog: list[ActionDescriptor] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw):
        try:
            descriptor = ActionDescriptor(**entry)
        except (TypeError, ValidationError) as exc:
            log.warning("action catalog %s: skipping entry %d (%s)", path, i, exc)
            continue
        # Matches ADR 0034's chip rule: duplicate ids reject the later
        # entry, so e.g. a misconfigured actions.custom id colliding with
        # a built-in (or another custom entry) doesn't silently produce
        # two same-keyed React list items.
        if descriptor.id in seen_ids:
            log.warning(
                "action catalog %s: skipping entry %d, duplicate id %r",
                path,
                i,
                descriptor.id,
            )
            continue
        seen_ids.add(descriptor.id)
        catalog.append(descriptor)
    return catalog


_loaded_catalog = _load_catalog_from_file(settings.action_catalog_path)
# `or` would treat a validly-empty list (see above) the same as a missing
# file, silently reintroducing the defaults a chart-level "disable
# everything" was meant to remove - check for None explicitly instead.
_CATALOG: list[ActionDescriptor] = (
    _loaded_catalog if _loaded_catalog is not None else _DEFAULT_CATALOG
)


def _read_action_secret(filename: str) -> str | None:
    """Read `settings.action_tokens_path/<filename>`, or None if missing.

    Read on every invoke rather than cached, so a rotation (`helm
    upgrade` + Secret update) takes effect without a pod restart.
    """
    file = Path(settings.action_tokens_path) / filename
    if not file.is_file():
        return None
    return file.read_text().strip() or None


def load_invoke_token(action_id: str) -> str | None:
    """The invoke token for `action_id`, or None if it has none.

    Read from a Secret-backed volume (`actions-secret.yaml`), not the
    action catalog itself, which lives in a ConfigMap with no
    access-control distinction from other config. This proves only that
    the caller knows the shared secret, not who the end user is or what
    they're authorized for - see `mint_user_assertion` for that.
    """
    return _read_action_secret(action_id)


def mint_user_assertion(
    action_id: str, sub: str, groups: frozenset[str], search_id: str
) -> str | None:
    """Short-lived signed assertion of who this invoke is for, so a
    backend-call target can independently verify identity and group
    membership instead of trusting the bearer invoke token alone.

    Signed with a SEPARATE per-action key (`<action_id>.assertion-key`),
    never the invoke token itself: the invoke token is transmitted on
    every call and can leak via logs/traces/support bundles, but a
    signing key never travels over the wire - only its signature output
    does, which can't be reversed to recover it. Reusing the invoke token
    as the signing key would let anyone who obtained it forge whatever
    claims they wanted, defeating the point.

    None if this action has no assertion key configured - opt-in, so an
    action that never sets one just doesn't get this header, same as
    load_invoke_token. A 60s expiry keeps this a one-shot proof of "this
    exact invocation is legitimate right now," not a general credential.
    """
    key = _read_action_secret(f"{action_id}.assertion-key")
    if not key:
        return None
    now = int(time.time())
    claims = {
        "sub": sub,
        "groups": sorted(groups),
        "search_id": search_id,
        "action_id": action_id,
        "iat": now,
        "exp": now + 60,
    }
    return jwt.encode(claims, key, algorithm="HS256")


def list_actions(user_groups: frozenset[str]) -> list[ActionDescriptor]:
    """Group-filtered, weight-sorted actions visible to this caller.

    Server-side filtering only - visibility is UX, not the authorization
    boundary (ADR 0034's framing for launchpad chips, unchanged here): a
    real backend-calling action must still independently enforce the same
    group check at its own endpoint, since a hidden action's URL is not
    itself a secret.
    """
    visible = [
        d
        for d in _CATALOG
        if d.required_group is None or d.required_group in user_groups
    ]
    return sorted(visible, key=lambda d: (d.weight, d.title, d.id))
