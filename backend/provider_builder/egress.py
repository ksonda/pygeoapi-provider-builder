"""Which host:port pairs a sandbox job may reach (PLAN.md §8.2).

They come from what the job will connect to, never from the job's own
request: the connection settings of SQL providers, and the URLs a spec
declares. ``${VAR}`` placeholders are filled from the job's test secrets, so a
connection string kept in a secret still yields its host.

A descriptor opts in with an ``egress`` rule: ``{kind: sql, default_port: 5432}``,
``{kind: spec}``, ``{kind: url, keys: [data]}`` (the hosts of the http(s) URLs in
those config keys, such as a WMS or a tile server; GDAL prefixes like ``WFS:`` are
dropped first), or ``{kind: hosts, hosts: [api.example.gov:443]}`` (code-built
providers declare what they reach; ``sql_keys`` names config keys holding a database
connection, such as ``data``). Providers without one get no network at all.
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
    template = doc.get('template')
    if template == 'sql-features':
        return sql_source(doc.get('source') or {})
    if template == 'timeseries-edr':
        found: set[HostPort] = set()
        for block in ('locations', 'observations'):
            part = doc.get(block) or {}
            if part.get('from') == 'http':
                http = part.get('http') or {}
                if block == 'locations':
                    found |= spec({'template': 'http-features', **http})
                else:
                    found |= urls([http.get('url')])
            elif part.get('from') == 'sql':
                found |= sql_source((part.get('sql') or {}).get('source') or {})
        return found
    source = doc.get('source') or {}
    item = doc.get('item') or {}
    return urls([source.get('url'), item.get('url')])


#: GDAL's prefixes before a URL: a driver name (WFS:, ESRIJSON:) or a virtual file system.
GDAL_PREFIX = re.compile(
    r'^(?:/vsicurl(?:_streaming)?/|[A-Za-z][A-Za-z0-9_]*:(?=https?://))', re.IGNORECASE
)


def strip_gdal_prefix(value: Any) -> Any:
    """``WFS:https://…`` or ``/vsicurl/https://…`` as the plain URL GDAL connects to."""
    if not isinstance(value, str):
        return value
    while m := GDAL_PREFIX.match(value):
        value = value[m.end() :]
    return value


def _dotted(pdef: Mapping[str, Any], key: str) -> Any:
    node: Any = pdef
    for part in key.split('.'):
        node = node.get(part) if isinstance(node, Mapping) else None
    return node


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
    if kind == 'url':  # the service a URL in the config names (ESRI, MVT-proxy, WMSFacade, OGR)
        values = [fill(_dotted(pdef, str(key)), secrets) for key in rule.get('keys') or ['data']]
        return urls(strip_gdal_prefix(v) for v in values)
    if kind == 'hosts':  # a code-built provider's declared host:port pairs
        found: set[HostPort] = set()
        for entry in rule.get('hosts') or []:
            host, _, port = str(entry).rpartition(':')
            found |= _hostport(host, port)
        for key in rule.get('sql_keys') or []:  # config values that hold a database connection
            found |= sql(
                fill(_dotted(pdef, str(key)), secrets), int(rule.get('default_port', 5432))
            )
        return found
    return set()
