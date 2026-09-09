"""YAML through libyaml where the wheel provides it.

Every fragment is parsed on every reconcile, and PyYAML's pure-Python loader is
~9x slower than the C one for that shape of document. The manylinux wheels
bundle libyaml, so the fallback is only for a PyYAML built without it.
"""

from typing import Any

import yaml

try:
    from yaml import CSafeDumper as _Dumper
    from yaml import CSafeLoader as _Loader
except ImportError:  # pragma: no cover - PyYAML built without libyaml
    from yaml import SafeDumper as _Dumper
    from yaml import SafeLoader as _Loader

YAMLError = yaml.YAMLError

__all__ = ["YAMLError", "safe_dump", "safe_load", "safe_load_all"]


def safe_load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


def safe_load_all(text: str) -> list[Any]:
    """Every document in a `---`-separated stream, e.g. a rendered chart."""
    return list(yaml.load_all(text, Loader=_Loader))


def safe_dump(data: Any, **kwargs: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, **kwargs)
