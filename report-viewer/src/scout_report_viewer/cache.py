"""Query-result cache in Valkey (#806).

Off unless `valkey_url` is set and `cache_ttl_seconds` is positive. Any Valkey
or decode failure reads as a miss, so the cache can slow nothing down beyond
its timeouts and never turns into a request error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import zlib
from typing import Any

import redis.asyncio as redis

from . import metrics
from .config import settings

log = logging.getLogger(__name__)

# Bump the version when the stored encoding changes; old entries expire unread.
_PREFIX = "rv:v1:"

_client: redis.Redis | None = None


def _get_client() -> redis.Redis | None:
    global _client
    if not settings.valkey_url or settings.cache_ttl_seconds <= 0:
        return None
    if _client is None:
        _client = redis.from_url(
            settings.valkey_url,
            password=settings.valkey_password or None,
            socket_timeout=settings.valkey_timeout_seconds,
            socket_connect_timeout=settings.valkey_timeout_seconds,
        )
    return _client


def key(op: str, user: str, *parts: Any) -> str:
    raw = json.dumps([user, *parts], separators=(",", ":"), default=str)
    return f"{_PREFIX}{op}:{hashlib.sha256(raw.encode()).hexdigest()}"


def pack_rows(columns: list[str], rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[r.get(c) for c in columns] for r in rows]


def unpack_rows(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, r)) for r in rows]


def _encode(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":")).encode(), 1)


def _decode(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob))


async def get(op: str, cache_key: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    try:
        blob = await client.get(cache_key)
        value = None if blob is None else await asyncio.to_thread(_decode, blob)
    except Exception:
        log.warning("cache read failed; running uncached", exc_info=True)
        metrics.CACHE_LOOKUPS.labels(op=op, result="error").inc()
        return None
    metrics.CACHE_LOOKUPS.labels(op=op, result="miss" if value is None else "hit").inc()
    return value


async def put(cache_key: str, value: Any) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        blob = await asyncio.to_thread(_encode, value)
        await client.set(cache_key, blob, ex=settings.cache_ttl_seconds)
    except Exception:
        log.warning("cache write failed", exc_info=True)
