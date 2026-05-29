"""
title: Tool Result Body→Attribute Migrator (Diagnostic)
description: Rewrites OWUI 0.9.5 tool_calls body-content back to result="..." attribute as a workaround for a frontend rendering regression.
"""

# Background
# ----------
# Upstream commit 45e49d33e (open-webui, Apr 2026, shipped in 0.9.5) moved
# tool-call results from a `result="..."` attribute on the opening `<details>`
# tag into the body between `<details ...>` and `</details>`. The frontend was
# supposed to read it via a new `resultContent` prop on `ToolCallDisplay`, but
# in 0.9.5 the Output section in the expanded panel renders empty even though
# the body is in storage. Results still reach the LLM (the conversation works),
# only the chat-UI display is broken.
#
# `ToolCallDisplay.svelte` still has the legacy fallback:
#     $: result = resultContent || decode(attributes?.result ?? '');
# So if we move the body back onto a `result="..."` attribute, the OLD path
# renders correctly. This filter does exactly that on outlet — diagnostic only.
# Remove (or set `enable_active: false`) once the upstream display bug is
# fixed.

import re
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field


# Match a single <details ...>\n<summary>...</summary>\n{body}\n</details>
# block. DOTALL lets `.+?` cross newlines. The lazy quantifier on body stops
# at the FIRST `</details>`, which is correct because middleware HTML-escapes
# body content (any literal `</details>` becomes `&lt;/details&gt;`).
_DETAILS_BLOCK = re.compile(
    r"<details(?P<attrs>\s+[^>]*?)>\n"
    r"<summary>(?P<summary>[^<]*)</summary>\n"
    r"(?P<body>.+?)\n"
    r"</details>",
    re.DOTALL,
)


def _attr_value(attrs: str, name: str) -> Optional[str]:
    m = re.search(rf'\b{re.escape(name)}="([^"]*)"', attrs)
    return m.group(1) if m else None


def rewrite_tool_calls_to_attribute(content: str) -> str:
    """Move body content of tool_calls details blocks into a result="..." attribute."""

    def repl(m: re.Match) -> str:
        attrs = m.group("attrs")
        summary = m.group("summary")
        body = m.group("body").strip()

        # Only touch tool_calls; leave reasoning / code_interpreter alone.
        if _attr_value(attrs, "type") != "tool_calls":
            return m.group(0)

        # Idempotent: if a result= attribute is already present, leave it.
        if _attr_value(attrs, "result") is not None:
            return m.group(0)

        # Body is already HTML-escaped by middleware.serialize_output, so it
        # contains no literal `"` (they're &quot;) and is safe inside an
        # attribute value.
        new_attrs = attrs.rstrip() + f' result="{body}"'
        return f"<details{new_attrs}>\n<summary>{summary}</summary>\n</details>"

    return _DETAILS_BLOCK.sub(repl, content)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=100,
            description="Run after other content filters. Higher = later.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> dict:
        messages = body.get("messages") or []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            original = msg.get("content") or ""
            # Cheap pre-check — skip messages that have no tool-call details.
            if '<details type="tool_calls"' not in original:
                continue
            rewritten = rewrite_tool_calls_to_attribute(original)
            if rewritten == original:
                continue
            msg["content"] = rewritten
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "replace", "data": {"content": rewritten}}
                )
        return body
