"""Search ID generator.

Format: `s_<16-char base62>`. log2(62^16) ~= 95 bits, well past any
brute-force window.
"""

import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def new_search_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    return f"s_{suffix}"
