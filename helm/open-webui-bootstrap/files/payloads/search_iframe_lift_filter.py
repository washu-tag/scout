"""
title: Search iframe lift
description: Outlet filter — appends an HTML artifact code block (renders in OWUI's side artifact panel with allow-scripts sandbox) for the last Scout search_id mentioned. Replaces the raw markdown <iframe> approach, which OWUI rewrites to sandbox="" and blocks scripts inside.
"""

import re
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field


_SEARCH_ID_RE = re.compile(r"\b(s_[A-Za-z0-9]{6})\b")
_MARKER_BEGIN = "<!-- BEGIN scout-search-artifact -->"
_MARKER_END = "<!-- END scout-search-artifact -->"
_INJECTED_BLOCK_RE = re.compile(
    re.escape(_MARKER_BEGIN) + r".*?" + re.escape(_MARKER_END) + r"\n?",
    re.DOTALL,
)


def lift_iframe(content: str, base_url: str) -> str:
    """Strip any prior injected artifact block, then append a fresh
    `html` code block for the LAST search_id mentioned. OWUI auto-detects
    `html`/`svg` code blocks as artifacts and shows them in a side panel
    with `sandbox="allow-scripts allow-downloads"` (which DOES permit
    Tabulator to load via CDN). Returns the content unchanged if no
    search_id is present and there's nothing to strip."""
    stripped = _INJECTED_BLOCK_RE.sub("", content)
    matches = _SEARCH_ID_RE.findall(stripped)
    if not matches:
        return stripped
    search_id = matches[-1]
    base = base_url.rstrip("/")
    src = f"{base}/spa/searches/{search_id}"
    # We can't put scripts directly in here that fetch /rows (OWUI's
    # artifact sandbox has no allow-same-origin by default — that's a
    # per-user toggle), so for now the artifact srcdoc just hosts an
    # inner <iframe> pointing at the viewer. The inner iframe inherits
    # the parent's sandbox flags (allow-scripts is there), and the
    # viewer's same-origin fetch happens inside the inner iframe's own
    # browsing context.
    # Two-part block:
    #   1. A `[View search ↗](url)` markdown link — always works as a
    #      backstop; opens the standalone viewer in a new tab.
    #   2. An `html` code block — OWUI renders this in its side artifact
    #      panel (`sandbox="allow-scripts allow-downloads"`). If the
    #      nested iframe + cross-origin fetch survive the sandbox, the
    #      user gets the viewer in-app; if not, the link covers it.
    block = (
        f"\n\n{_MARKER_BEGIN}\n"
        f"[View search {search_id} ↗]({src})\n\n"
        f"```html\n"
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f'<head><meta charset="utf-8"><title>Search {search_id}</title>\n'
        f"<style>html,body{{margin:0;padding:0;height:100%;}}"
        f"iframe{{display:block;width:100%;height:100vh;border:0;}}</style>\n"
        f"</head><body>\n"
        f'<iframe src="{src}" referrerpolicy="strict-origin-when-cross-origin"></iframe>\n'
        f"</body></html>\n"
        f"```\n"
        f"{_MARKER_END}"
    )
    return stripped.rstrip() + block


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=200,
            description="Run after other content filters. Higher = later.",
        )
        base_url: str = Field(
            default="https://report-viewer.dev02.tag.rcif.io",
            description="Public origin where the report-viewer-service viewer is reachable.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> dict:
        print("[search_iframe_lift] outlet called", flush=True)
        messages = body.get("messages") or []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            original = msg.get("content") or ""
            # Cheap pre-check — skip messages with no search id.
            if "s_" not in original:
                continue
            rewritten = lift_iframe(original, self.valves.base_url)
            if rewritten == original:
                continue
            msg["content"] = rewritten
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "replace", "data": {"content": rewritten}}
                )
        return body
