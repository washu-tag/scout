"""The loader's nesting cap.

Nothing here may be able to crash the interpreter, which is the whole point of
the cap: it is checked before composition recurses, so a hostile depth is
refused without any stack -- C or Python -- being walked down to it.
"""

from __future__ import annotations

import time

import pytest
import yaml

from scout_keycloak_fragment_reconciler.yamlio import MAX_DEPTH, safe_load


def nested(depth: int) -> str:
    """A document whose deepest node sits at `depth`, root mapping included."""
    return "a: " + "[" * (depth - 1) + "]" * (depth - 1)


def depth_of(value) -> int:
    if isinstance(value, dict):
        return 1 + max((depth_of(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((depth_of(v) for v in value), default=0)
    return 1


class TestDepthCap:
    def test_a_document_at_the_cap_loads(self):
        assert depth_of(safe_load(nested(MAX_DEPTH))) == MAX_DEPTH

    def test_one_level_past_the_cap_is_a_yaml_error(self):
        with pytest.raises(yaml.YAMLError, match=f"{MAX_DEPTH}-level limit"):
            safe_load(nested(MAX_DEPTH + 1))

    def test_a_hostile_document_is_refused_promptly(self):
        """~1 MB of open brackets: inside the ConfigMap limit, and the shape
        that overflows libyaml's C stack."""
        started = time.monotonic()
        with pytest.raises(yaml.YAMLError):
            safe_load(nested(500_000))
        assert time.monotonic() - started < 5

    def test_breadth_is_not_depth(self):
        """The cap bounds nesting, not size; a long flat list is fine."""
        wide = "a: [" + ",".join(str(i) for i in range(10_000)) + "]"
        assert len(safe_load(wide)["a"]) == 10_000
