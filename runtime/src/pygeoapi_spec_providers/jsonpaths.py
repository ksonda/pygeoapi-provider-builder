"""RFC 9535 JSONPath in strict mode (ADR-0006).

Strict mode rejects the library's non-standard extensions, and no JSONPath
ever evaluates code: a Python expression in a filter is a syntax error.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from typing import Any

import jsonpath

ENV = jsonpath.JSONPathEnvironment(strict=True)


class PathError(ValueError):
    """A JSONPath that doesn't parse."""


@functools.lru_cache(maxsize=1024)
def compile_path(path: str) -> Any:
    try:
        return ENV.compile(path)
    except jsonpath.JSONPathError as err:
        detail = str(err).splitlines()[0]
        raise PathError(f'{path!r} is not a valid JSONPath: {detail}') from err


def findall(path: str, data: Any) -> list[Any]:
    return list(compile_path(path).findall(data))


def first(path: str, data: Any, default: Any = None) -> Any:
    for match in compile_path(path).finditer(data):
        return match.obj
    return default


def value_of(record: Any, ref: str) -> Any:
    """A key of the record, or a JSONPath relative to the record (``$...``)."""
    if ref.startswith('$'):
        return first(ref, record)
    if isinstance(record, Mapping):
        return record.get(ref)
    return None
