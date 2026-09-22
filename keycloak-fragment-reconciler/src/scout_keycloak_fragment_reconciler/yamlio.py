"""YAML through PyYAML's pure-Python loader, with nesting bounded.

A fragment is untrusted input, and libyaml composes nested collections on the C
stack: deep enough, it overflows and kills the process, which no `except` can
contain. The pure loader recurses on the interpreter stack instead, and
`MAX_DEPTH` refuses the document before either stack is walked down to it.
Measured on a fully-populated fragment that costs ~400us against libyaml's
~40us -- some 12ms for a 30-fragment pass, against a resync of minutes.
"""

from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.nodes import Node

# A fully-populated fragment nests 6 deep, counting the leaf scalar.
MAX_DEPTH = 16

__all__ = ["MAX_DEPTH", "safe_load"]


class _DepthLimitedLoader(yaml.SafeLoader):
    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node:
        if self._depth >= MAX_DEPTH:
            raise ComposerError(
                None,
                None,
                f"nesting exceeds the {MAX_DEPTH}-level limit",
                self.peek_event().start_mark,
            )
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def safe_load(text: str) -> Any:
    return yaml.load(text, Loader=_DepthLimitedLoader)
