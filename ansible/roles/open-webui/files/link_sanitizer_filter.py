"""
title: Link Sanitizer Filter
description: Removes external URLs from LLM responses to prevent data exfiltration.
"""

import re
from typing import Optional, Callable, Any, Awaitable
from urllib.parse import urlparse

try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object

    def Field(default=None, description=None):
        return default


class Filter:
    """Removes external URLs from LLM responses to prevent data exfiltration."""

    class Valves(BaseModel):
        internal_domains: str = Field(
            default="",
            description="Comma-separated internal domains to allow (subdomains included)",
        )
        replacement_text: str = Field(
            default="(external link removed for security)",
            description="Text shown in place of removed links",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.md_link_pattern = re.compile(
            r"\[([^\]]*)\]\((https?://[^)]+)\)", re.IGNORECASE
        )
        # Match raw URLs not in quotes (markdown links already processed)
        # Stop at common delimiters: space, parens, brackets, angle brackets, quotes
        self.raw_url_pattern = re.compile(
            r'(?<!")(https?://[^\s()\[\]<>"]+)', re.IGNORECASE
        )
        # For stream processing: buffer for partial URLs
        self._stream_buffer = ""
        # Characters that end a URL
        self._url_end_chars = set(' \t\n\r()[]<>"\'')

    def get_internal_domains(self) -> set:
        if not self.valves.internal_domains:
            return set()
        return {
            d.strip().lower()
            for d in self.valves.internal_domains.split(",")
            if d.strip()
        }

    def is_external(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            # Use hostname to strip port and userinfo (e.g., user:pass@host:port)
            domain = (parsed.hostname or "").lower()

            internal = self.get_internal_domains()
            if not internal:
                return True

            for internal_domain in internal:
                if domain == internal_domain or domain.endswith("." + internal_domain):
                    return False
            return True
        except Exception:
            return True

    def sanitize_md_link(self, match: re.Match) -> str:
        text = match.group(1)
        url = match.group(2)
        if self.is_external(url):
            if text:
                return f"{text} {self.valves.replacement_text}"
            return self.valves.replacement_text
        return match.group(0)

    def sanitize_raw_url(self, match: re.Match) -> str:
        url = match.group(1)
        if self.is_external(url):
            return self.valves.replacement_text
        return match.group(0)

    def sanitize_content(self, content: str) -> str:
        content = self.md_link_pattern.sub(self.sanitize_md_link, content)
        content = self.raw_url_pattern.sub(self.sanitize_raw_url, content)
        return content

    def _process_stream_buffer(self, final: bool = False) -> str:
        """
        Process the stream buffer, returning sanitized content that's safe to emit.
        Keeps partial URLs in buffer for next chunk.

        Args:
            final: If True, flush everything (end of stream)
        """
        if not self._stream_buffer:
            return ""

        # Look for URL start patterns
        http_pos = self._stream_buffer.lower().find("http://")
        https_pos = self._stream_buffer.lower().find("https://")

        # Find earliest URL start (-1 means not found)
        url_start = -1
        if http_pos >= 0 and https_pos >= 0:
            url_start = min(http_pos, https_pos)
        elif http_pos >= 0:
            url_start = http_pos
        elif https_pos >= 0:
            url_start = https_pos

        if url_start == -1:
            # No URL in buffer
            if final:
                # Flush everything
                result = self._stream_buffer
                self._stream_buffer = ""
                return result
            # Keep last few chars in case "http" is split across chunks
            if len(self._stream_buffer) > 7:  # len("https://") - 1
                result = self._stream_buffer[:-7]
                self._stream_buffer = self._stream_buffer[-7:]
                return result
            return ""

        # Found a URL start - emit everything before it
        result = self._stream_buffer[:url_start]
        self._stream_buffer = self._stream_buffer[url_start:]

        # Now find the end of the URL
        url_end = -1
        for i, char in enumerate(self._stream_buffer):
            if i == 0:
                continue  # Skip the 'h' of http
            if char in self._url_end_chars:
                url_end = i
                break

        if url_end == -1:
            # URL might continue in next chunk
            if final:
                # End of stream - process what we have
                url = self._stream_buffer
                self._stream_buffer = ""
                if self.is_external(url):
                    return result + self.valves.replacement_text
                return result + url
            # Keep buffering
            return result

        # We have a complete URL
        url = self._stream_buffer[:url_end]
        self._stream_buffer = self._stream_buffer[url_end:]

        if self.is_external(url):
            result += self.valves.replacement_text
        else:
            result += url

        # Recursively process rest of buffer
        return result + self._process_stream_buffer(final)

    def stream(self, event: dict) -> dict:
        """
        Process streaming chunks in real-time, sanitizing URLs as they arrive.
        """
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            if "content" in delta and delta["content"]:
                # Add chunk to buffer
                self._stream_buffer += delta["content"]
                # Process and emit safe content
                delta["content"] = self._process_stream_buffer(final=False)

            # Check for end of stream
            if choice.get("finish_reason"):
                # Flush remaining buffer
                remaining = self._process_stream_buffer(final=True)
                if remaining:
                    delta["content"] = delta.get("content", "") + remaining

        return event

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Callable[[Any], Awaitable[None]] = None,
    ) -> dict:
        if not body.get("messages"):
            return body

        for msg in body["messages"]:
            if msg.get("role") == "assistant" and msg.get("content"):
                original = msg["content"]
                sanitized = self.sanitize_content(original)
                if sanitized != original and __event_emitter__:
                    await __event_emitter__(
                        {"type": "replace", "data": {"content": sanitized}}
                    )
                msg["content"] = sanitized

        return body
