"""Search ID generator.

Format: `s_<6-char base62>`. 6 chars of base62 = 56 bits, plenty for
the TTL + per-user scope; collisions would only matter within a single
user's active window.
"""

import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def new_search_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"s_{suffix}"
