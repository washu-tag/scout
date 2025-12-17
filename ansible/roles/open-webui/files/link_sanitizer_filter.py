"""
title: Link Sanitizer Filter
description: Removes external URLs from LLM responses to prevent data exfiltration.
"""

import re
import threading
import time
from typing import Optional, Callable, Any, Awaitable
from urllib.parse import urlparse

from pydantic import BaseModel, Field


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

    # Time-to-live for stream buffers (seconds). Buffers older than this are
    # cleaned up to prevent memory leaks from abandoned streams.
    STREAM_BUFFER_TTL = 600  # 10 minutes

    def __init__(self):
        self.valves = self.Valves()
        # Match markdown links with http://, https://, or www.
        self.md_link_pattern = re.compile(
            r"\[([^\]]*)\]\(((?:https?://|www\.)[^)]+)\)", re.IGNORECASE
        )
        # Match raw URLs (markdown links already processed)
        # Matches http://, https://, or www. URLs
        # Handles IPv6 URLs with brackets: http://[::1]/path
        # Stop at common delimiters: space, parens, brackets, angle brackets, quotes
        self.raw_url_pattern = re.compile(
            r"((?:https?://|www\.)(?:\[[^\]]+\]|[^\s()\[\]<>\"'])+)", re.IGNORECASE
        )
        # For stream processing: per-stream buffers keyed by stream ID
        # Values are (buffer_content, last_updated_timestamp) tuples
        # Lock ensures thread-safety when the same Filter instance handles
        # multiple concurrent streams (Open WebUI shares filter instances)
        self._stream_buffers = {}
        self._buffer_lock = threading.Lock()
        # Characters that end a URL
        self._url_end_chars = set(" \t\n\r()[]<>\"'")

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
            # Add scheme if missing (for www. URLs) so urlparse works correctly
            parse_url = url
            if url.lower().startswith("www."):
                parse_url = "http://" + url

            parsed = urlparse(parse_url)
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

    def _cleanup_stale_buffers(self) -> None:
        """Remove stream buffers that haven't been updated within the TTL."""
        now = time.time()
        with self._buffer_lock:
            stale_ids = [
                stream_id
                for stream_id, (_, timestamp) in self._stream_buffers.items()
                if now - timestamp > self.STREAM_BUFFER_TTL
            ]
            for stream_id in stale_ids:
                del self._stream_buffers[stream_id]

    def _get_buffer(self, stream_id: str) -> str:
        """Get the buffer content for a stream, or empty string if not found."""
        with self._buffer_lock:
            entry = self._stream_buffers.get(stream_id)
            return entry[0] if entry else ""

    def _set_buffer(self, stream_id: str, content: str) -> None:
        """Set the buffer content for a stream with current timestamp."""
        with self._buffer_lock:
            self._stream_buffers[stream_id] = (content, time.time())

    def _delete_buffer(self, stream_id: str) -> None:
        """Delete the buffer for a stream if it exists."""
        with self._buffer_lock:
            self._stream_buffers.pop(stream_id, None)

    def _process_stream_buffer(self, stream_id: str, final: bool = False) -> str:
        """
        Process the stream buffer, returning sanitized content that's safe to emit.
        Keeps partial URLs in buffer for next chunk.

        Args:
            stream_id: Unique identifier for this stream (from event["id"])
            final: If True, flush everything (end of stream)
        """
        buffer = self._get_buffer(stream_id)
        if not buffer:
            return ""

        # Look for URL start patterns (http://, https://, www.)
        buffer_lower = buffer.lower()
        http_pos = buffer_lower.find("http://")
        https_pos = buffer_lower.find("https://")
        www_pos = buffer_lower.find("www.")

        # Find earliest URL start (-1 means not found)
        positions = [p for p in [http_pos, https_pos, www_pos] if p >= 0]
        url_start = min(positions) if positions else -1

        if url_start == -1:
            # No URL in buffer
            if final:
                # Flush everything
                result = buffer
                self._delete_buffer(stream_id)
                return result
            # Keep last few chars in case "http" or "www" is split across chunks
            if len(buffer) > 7:  # len("https://") - 1
                result = buffer[:-7]
                self._set_buffer(stream_id, buffer[-7:])
                return result
            return ""

        # Found a URL start - emit everything before it
        result = buffer[:url_start]
        buffer = buffer[url_start:]

        # Now find the end of the URL
        url_end = -1
        for i, char in enumerate(buffer):
            if i == 0:
                continue  # Skip the first char ('h' of http or 'w' of www)
            if char in self._url_end_chars:
                url_end = i
                break

        if url_end == -1:
            # URL might continue in next chunk
            if final:
                # End of stream - process what we have
                url = buffer
                self._delete_buffer(stream_id)
                if self.is_external(url):
                    return result + self.valves.replacement_text
                return result + url
            # Keep buffering
            self._set_buffer(stream_id, buffer)
            return result

        # We have a complete URL
        url = buffer[:url_end]
        self._set_buffer(stream_id, buffer[url_end:])

        if self.is_external(url):
            result += self.valves.replacement_text
        else:
            result += url

        # Recursively process rest of buffer
        return result + self._process_stream_buffer(stream_id, final)

    def stream(self, event: dict, __metadata__: dict = None) -> dict:
        """
        Process streaming chunks in real-time, sanitizing URLs as they arrive.

        Uses chat_id from __metadata__ to maintain separate buffers per conversation,
        ensuring thread-safety when multiple concurrent streams are processed.
        """
        # Require chat_id for stream buffering to ensure proper isolation.
        # Without it, we can't safely buffer across chunks. Skip stream processing
        # and let the outlet handle sanitization instead.
        if not __metadata__ or not __metadata__.get("chat_id"):
            return event

        # Opportunistically clean up abandoned stream buffers to prevent memory leaks
        self._cleanup_stale_buffers()

        stream_id = __metadata__["chat_id"]

        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            if "content" in delta and delta["content"]:
                # Add chunk to buffer for this stream
                current_buffer = self._get_buffer(stream_id)
                self._set_buffer(stream_id, current_buffer + delta["content"])
                # Process and emit safe content
                delta["content"] = self._process_stream_buffer(stream_id, final=False)

            # Check for end of stream
            if choice.get("finish_reason"):
                # Flush remaining buffer and clean up
                remaining = self._process_stream_buffer(stream_id, final=True)
                if remaining:
                    delta["content"] = delta.get("content", "") + remaining
                # Ensure cleanup even if buffer was already empty
                self._delete_buffer(stream_id)

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
