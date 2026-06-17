"""Dataset ID generator.

Format: `ds_<6-char base62>`. The prefix matches Flavin's design doc and
the LLM-facing handle name (`dataset_id`). 6 chars of base62 = 56 bits,
plenty for the 30-day TTL + per-user scope; collisions would only matter
within a single user's active window.
"""

import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def new_dataset_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"ds_{suffix}"
