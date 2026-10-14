"""Which host:port pairs a sandbox job may reach (PLAN.md §8.2).

They come from what the job will connect to, never from the job's own
request: the connection settings of SQL providers, and the URLs a spec
declares. ``${VAR}`` placeholders are filled from the job's test secrets, so a
connection string kept in a secret still yields its host.

A descriptor opts in with an ``egress`` rule: ``{kind: sql, default_port: 5432}``
or ``{kind: spec}``. Providers without one get no network at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

PLACEHOLDER = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}')
DEFAULT_PORTS = {'http': 80, 'https': 443}


@dataclass(frozen=True, order=True)
class HostPort:
    host: str
    port: int

    def __str__(self) -> str:
        return f'{self.host}:{self.port}'


def fill(value: Any, secrets: Mapping[str, str]) -> Any:
    """Replace ``${VAR}`` with test secrets (or the placeholder's default); leave others."""
    if isinstance(value, str):

        def sub(m: re.Match[str]) -> str:
            if m.group(1) in secrets:
                return secrets[m.group(1)]
            return m.group(2) if m.group(2) is not None else m.group(0)

        return PLACEHOLDER.sub(sub, value)
    if isinstance(value, Mapping):
        return {k: fill(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, secrets) for v in value]
    return value


def _hostport(host: Any, port: Any) -> set[HostPort]:
    if not host or '${' in str(host):
        return set()
    try:
        return {HostPort(str(host).strip().lower(), int(port))}
    except (TypeError, ValueError):
        return set()


def sql(data: Any, default_port: int) -> set[HostPort]:
    """The database host of a SQL provider's ``data``: a mapping or a connection URL."""
    if isinstance(data, Mapping):
        return _hostport(data.get('host'), data.get('port') or default_port)
    if isinstance(data, str) and '://' in data:
        parts = urlsplit(data)
        try:
            port = parts.port or default_port
        except ValueError:
            return set()
        return _hostport(parts.hostname, port)
    return set()


def urls(values: Iterable[Any]) -> set[HostPort]:
    found: set[HostPort] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        parts = urlsplit(value)
        if parts.scheme not in DEFAULT_PORTS:
            continue
        try:
            port = parts.port or DEFAULT_PORTS[parts.scheme]
        except ValueError:
            continue
        found |= _hostport(parts.hostname, port)
    return found


def sql_source(source: Mapping[str, Any]) -> set[HostPort]:
    """The database server of a SQL spec's ``source`` (none for SQLite files)."""
    if source.get('dialect') != 'postgresql':
        return set()
    if source.get('dsn') is not None:
        return sql(source['dsn'], 5432)
    return sql(source.get('connection') or {}, 5432)


def spec(doc: Mapping[str, Any]) -> set[HostPort]:
    """The hosts a spec declares: its source and item URLs, or its database server."""
    if doc.get('template') == 'sql-features':
        return sql_source(doc.get('source') or {})
    source = doc.get('source') or {}
    item = doc.get('item') or {}
    return urls([source.get('url'), item.get('url')])


def for_provider(
    pdef: Mapping[str, Any],
    rule: Mapping[str, Any] | None,
    secrets: Mapping[str, str],
    spec_doc: Mapping[str, Any] | None = None,
) -> set[HostPort]:
    if not rule:
        return set()
    kind = rule.get('kind')
    if kind == 'sql':
        return sql(fill(pdef.get('data'), secrets), int(rule.get('default_port', 5432)))
    if kind == 'spec' and spec_doc is not None:
        return spec(fill(spec_doc, secrets))
    return set()
