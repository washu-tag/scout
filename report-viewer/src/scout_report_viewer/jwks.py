"""JWKS fetch + cache for Keycloak JWT validation.

We cache the JWKS document in-process for `JWKS_CACHE_TTL_SECONDS` and
refresh on cache miss OR when validation fails with an unknown `kid`
(handles Keycloak key rotation without a service restart).

Separate module from `auth.py` so the cache is process-wide and easy
to mock in tests.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


JWKS_CACHE_TTL_SECONDS = 300  # 5 min - matches Keycloak's default rotation window


class JwksCache:
    """In-process JWKS cache with TTL + forced-refresh-on-miss.

    A single instance is created at module load and reused for the
    process lifetime. Tests can inject their own via `set_default()`.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._lock = threading.Lock()
        self._fetched_at: float = 0.0
        self._keys: dict[str, dict[str, Any]] = {}

    def get_key(self, kid: str) -> dict[str, Any] | None:
        """Return the JWK matching `kid`, refreshing the cache if absent.

        Returns None only when the second fetch (post-miss) also lacks
        the kid - i.e. the kid genuinely isn't ours.
        """
        with self._lock:
            self._maybe_refresh()
            key = self._keys.get(kid)
            if key is None:
                # Force a refresh; the kid might be from a rotation we
                # haven't seen yet. Reset the timestamp so the TTL gate
                # doesn't suppress it.
                self._fetched_at = 0.0
                self._maybe_refresh()
                key = self._keys.get(kid)
            return key

    def _maybe_refresh(self) -> None:
        if time.time() - self._fetched_at < JWKS_CACHE_TTL_SECONDS:
            return
        try:
            resp = httpx.get(self._url, timeout=10.0)
            resp.raise_for_status()
            doc = resp.json()
            self._keys = {k["kid"]: k for k in doc.get("keys", []) if "kid" in k}
            self._fetched_at = time.time()
            log.info("JWKS refreshed: %d keys", len(self._keys))
        except Exception:
            log.exception("JWKS refresh failed (kept existing cache)")


_default: JwksCache | None = None


def get_default(url: str) -> JwksCache:
    """Lazy-init the process-wide cache. URL is captured on first call."""
    global _default
    if _default is None:
        _default = JwksCache(url)
    return _default


def set_default(cache: JwksCache | None) -> None:
    """Override the cache (tests)."""
    global _default
    _default = cache
