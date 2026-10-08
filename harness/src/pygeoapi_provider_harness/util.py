"""JSON-safe conversion, exception description, path remapping and redaction."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import traceback
from collections.abc import Iterable
from typing import Any

MAX_TRACEBACK_CHARS = 20_000
MAX_CHAIN = 5


def json_default(value: Any) -> Any:
    """``json.dumps`` fallback for values providers commonly return."""
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return {'$bytes_base64': base64.b64encode(bytes(value)[:1024]).decode()}
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    return str(value)


def describe_exception(err: BaseException) -> dict[str, Any]:
    """Class, message, pygeoapi ``user_msg`` and the cause chain of an error."""
    chain: list[dict[str, Any]] = []
    current: BaseException | None = err
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < MAX_CHAIN:
        seen.add(id(current))
        chain.append(
            {
                'type': _qualname(current),
                'message': str(current),
                'user_msg': getattr(current, 'user_msg', None),
            }
        )
        current = current.__cause__ or current.__context__
    tb = ''.join(traceback.format_exception(err))
    return {
        'type': _qualname(err),
        'message': str(err),
        'user_msg': getattr(err, 'user_msg', None),
        'chain': chain,
        'traceback': tb[-MAX_TRACEBACK_CHARS:],
    }


def _qualname(err: BaseException) -> str:
    cls = type(err)
    module = cls.__module__
    return cls.__qualname__ if module == 'builtins' else f'{module}.{cls.__qualname__}'


def remap_paths(value: Any, path_map: Iterable[dict[str, str]]) -> Any:
    """Rewrite server-side paths to host paths (subprocess runner only).

    ``path_map`` items are ``{'server': '/data', 'host': '/abs/docker/data'}``.
    Only whole path prefixes are replaced (``/data`` matches ``/data/x`` but
    not ``/database``).
    """
    maps = sorted(path_map, key=lambda m: len(m['server']), reverse=True)

    def fix(s: str) -> str:
        for m in maps:
            server = m['server'].rstrip('/')
            if s == server or s.startswith(server + '/'):
                return m['host'].rstrip('/') + s[len(server) :]
        return s

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return fix(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(value)


def redact(value: Any, secrets: Iterable[str]) -> Any:
    """Replace every occurrence of a secret value in strings with ``***``."""
    needles = sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True)
    if not needles:
        return value

    def scrub(s: str) -> str:
        for n in needles:
            s = s.replace(n, '***')
        return s

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return scrub(v)
        if isinstance(v, dict):
            return {scrub(str(k)) if isinstance(k, str) else k: walk(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [walk(x) for x in v]
        return v

    return walk(value)


def parse_time(value: Any) -> dt.datetime | None:
    """A timezone-aware datetime from ISO 8601 text (naive means UTC), or None."""
    if value is None or value == '':
        return None
    if isinstance(value, dt.datetime):
        moment = value
    else:
        try:
            moment = dt.datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    return moment.replace(tzinfo=dt.UTC) if moment.tzinfo is None else moment
