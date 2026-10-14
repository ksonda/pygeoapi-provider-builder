"""JSONPath, for the paths in this provider that aren't plain keys and indexes.

RFC 9535 in python-jsonpath's strict mode, as the Provider Builder's spec
engine read them: no non-standard extensions, and no path ever runs code.
Simpler paths are plain indexing in the provider itself (``support.dig``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonpath import JSONPathEnvironment

ENV = JSONPathEnvironment(strict=True)


def compile_path(path: str) -> Any:
    return ENV.compile(path)


def findall(path: Any, data: Any) -> list[Any]:
    """Every match of a compiled path."""
    return list(path.findall(data))


def first(path: Any, data: Any, default: Any = None) -> Any:
    """The first match of a compiled path, or ``default``."""
    for match in path.finditer(data):
        return match.obj
    return default


def records(path: Any, data: Any) -> list[Any]:
    """The objects a records path points at: one array, or each match."""
    matches = findall(path, data)
    if len(matches) == 1 and isinstance(matches[0], list):
        matches = matches[0]
    return [m for m in matches if isinstance(m, Mapping)]
