"""Eject: a template-built provider as standalone Python (PLAN.md §7.6, ADR-0021).

Eject turns one saved version of a spec into a code-built provider package in
the J5 code workspace. Its module subclasses pygeoapi's base classes directly
and does what the spec engine did with that spec, with **no dependency on
pygeoapi-spec-providers**:

- The spec's settings become constants, and the code paths the spec doesn't
  use (other pagination styles, filters it doesn't push down) are left out.
- Simple JSONPaths become plain indexing (``record.get('name')``,
  ``dig(doc, ['data', 'items'])``). The others keep python-jsonpath, in the
  strict mode the engine reads them in.
- HTTP uses requests. SQL uses SQLAlchemy and runs the query exactly as the SQL
  guard regenerated it (ADR-0007), pretty-printed.
- What changes from server to server goes in the pygeoapi config: ``data`` is
  the source (a URL, a database file or connection settings), and secrets are
  ``options`` that hold ``${VAR}``. A placeholder anywhere else is refused,
  because the code would have to bake it in.

The code comes from the templates in ``scaffold_templates/eject/``. Spec values
reach it only as Python literals (``repr``, proven by ``ast.literal_eval``) in
``Code``: the template environment refuses to output anything else, so a raw
spec value can't land in the code by mistake. Expressions are built here from
parsed, validated parts, and never with f-strings over spec text.

Eject is one-way. The spec stays as it was, so configs that use it keep
working, and the package doesn't go back into the wizard.
"""

from __future__ import annotations

import ast
import json
import keyword
import math
import re
import sys
import textwrap
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pygeoapi
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field
from pygeoapi_spec_providers import formatter, sqlguard
from pygeoapi_spec_providers.db import srid_of
from pygeoapi_spec_providers.spec import (
    BBOX_TOKENS,
    FIELD_TYPES,
    Problem,
    Spec,
    SpecError,
    normalize_source,
    parse,
)
from pygeoapi_spec_providers.timeseries import (
    DATETIME_TOKENS,
    SOURCE_KEYS,
    TOKENS,
    URL_TOKENS,
    parameter_json,
    parse_duration,
    sub_spec,
)

from . import egress
from .scaffold import CLASS, PACKAGE, SLUG, TEMPLATES, default_class, default_package, ruff
from .scaffold import environment as scaffold_environment
from .workspace import HIDDEN, capabilities_for

EJECT_TEMPLATES = TEMPLATES / 'eject'
GEOMETRY_COLUMNS = ('native', 'wkb', 'wkt', 'geojson', 'gpkg')
#: Where a spec's ``${VAR}`` placeholders may be: the values eject puts in the config.
CONFIG_POINTERS: dict[str, list[str]] = {
    'http-features': [r'/source/auth/(value|password|username)'],
    'sql-features': [r'/source/(path|dsn)', r'/source/connection/[^/]+'],
    'timeseries-edr': [
        r'/locations/http/source/auth/(value|password|username)',
        r'/locations/sql/source/(path|dsn)',
        r'/locations/sql/source/connection/[^/]+',
        r'/observations/http/auth/(value|password|username)',
        r'/observations/sql/source/(path|dsn)',
        r'/observations/sql/source/connection/[^/]+',
    ],
}
#: The options an HTTP source's auth becomes: (Python variable, spec key).
AUTH_OPTIONS: dict[str, list[tuple[str, str]]] = {
    'header': [('api_key', 'value')],
    'query': [('api_key', 'value')],
    'bearer': [('token', 'value')],
    'basic': [('username', 'username'), ('password', 'password')],
}
AUTH_TITLES = {
    'api_key': 'API key',
    'token': 'Bearer token',
    'username': 'User name',
    'password': 'Password',
}
STYLE_DOCS = {
    'none': 'the source returns everything in one response',
    'offset': 'offset pagination',
    'page': 'page-number pagination',
    'next_link': 'each page links to the next',
    'cursor': 'cursor pagination',
}


class EjectError(ValueError):
    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.problems = problems or []


class EjectRequest(BaseModel):
    version: str | None = Field(
        default=None, description='The saved spec version to eject (the newest by default)'
    )
    id: str = Field(description='The new code-built provider id (a slug)')
    title: str | None = None
    package: str | None = Field(default=None, description='The Python package name')
    class_name: str | None = None


# --- code: literals and checked text ---------------------------------------------------------


class Code(str):
    """Text for a generated file, built here: never a raw spec value."""

    __slots__ = ()


def _check_plain(value: Any) -> None:
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EjectError(f'{value!r} is not a finite number')
        return
    if isinstance(value, list | tuple):
        for item in value:
            _check_plain(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EjectError(f'{key!r} is a key that is not text')
            _check_plain(item)
        return
    raise EjectError(f'A {type(value).__name__} is not a plain value')


def literal(value: Any) -> Code:
    """A Python literal for a plain value, proven to read back as that value."""
    _check_plain(value)
    text = repr(value)
    if ast.literal_eval(text) != value:
        raise EjectError(f'{text[:60]} does not read back as itself')
    return Code(text)


def number(value: int) -> Code:
    """An integer literal, with ``_`` between thousands once it's large."""
    text = f'{value:_}' if abs(value) >= 10_000 else str(value)
    if not isinstance(value, int) or isinstance(value, bool) or ast.literal_eval(text) != value:
        raise EjectError(f'{value!r} is not an integer')
    return Code(text)


def frozen(values: Iterable[str]) -> Code:
    return Code(f'frozenset({literal(sorted(values))})')


def text_block(value: str) -> Code:
    """A long string, such as SQL, between triple quotes (with a newline at each end).

    Falls back to ``repr`` unless the triple-quoted form reads back exactly.
    """
    body = '\n' + value.strip('\n') + '\n'
    plain = all(c == '\n' or ' ' <= c != '\x7f' for c in body)
    if plain and '\\' not in body and '"""' not in body:
        candidate = '"""' + body + '"""'
        try:
            if ast.literal_eval(candidate) == body:
                return Code(candidate)
        except (SyntaxError, ValueError):
            pass
    return literal(body)


def ident(name: str) -> Code:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise EjectError(f'{name!r} is not a Python identifier')
    return Code(name)


def docstring(text: str) -> Code:
    """A docstring: control characters dropped, backslashes and triple quotes escaped."""
    clean = ''.join(c for c in text if c == '\n' or ' ' <= c != '\x7f')
    body = clean.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    candidate = '"""' + body + '"""'
    if ast.literal_eval(candidate) != clean:
        raise EjectError('A docstring did not read back as itself')
    return Code(candidate)


def markdown(value: Any) -> Code:
    """Text for one line of Markdown."""
    return Code(' '.join(str(value).split()))


def _finalize(value: Any) -> Any:
    if isinstance(value, Code):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise EjectError(f'A template printed {str(value)[:40]!r} without marking it as code')


def environment() -> Environment:
    env = Environment(  # noqa: S701 - renders Python, TOML and Markdown, never HTML
        loader=FileSystemLoader(EJECT_TEMPLATES),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        finalize=_finalize,
    )
    env.filters['md'] = markdown
    return env


# --- JSONPaths -------------------------------------------------------------------------------

SEGMENT = re.compile(
    r'\.(?P<dot>[A-Za-z_][A-Za-z0-9_]*)'
    r'|\[(?P<index>0|-?[1-9][0-9]*)\]'
    r"|\['(?P<single>[^'\\]*)'\]"
    r'|\["(?P<double>[^"\\]*)"\]'
)


def simple_path(path: str) -> tuple[list[str | int], bool] | None:
    """The keys of a JSONPath made of names and indexes, and whether it ends in ``[*]``.

    None for anything else: filters, slices, unions, ``..`` or a wildcard elsewhere.
    """
    if not path.startswith('$'):
        return None
    rest, each = path[1:], False
    for wildcard in ('[*]', '.*'):
        if rest.endswith(wildcard):
            rest, each = rest[: -len(wildcard)], True
            break
    keys: list[str | int] = []
    pos = 0
    while pos < len(rest):
        m = SEGMENT.match(rest, pos)
        if m is None:
            return None
        if m['dot'] is not None:
            keys.append(m['dot'])
        elif m['index'] is not None:
            keys.append(int(m['index']))
        else:
            keys.append(m['single'] if m['single'] is not None else m['double'])
        pos = m.end()
    return keys, each


class Paths:
    """Python for one module's JSONPaths. The ones that need python-jsonpath become
    compiled constants; the rest are plain indexing."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # path -> constant name

    def constants(self) -> list[tuple[Code, Code]]:
        return [(Code(name), literal(path)) for path, name in self.names.items()]

    def _constant(self, path: str, role: str) -> str:
        if path not in self.names:
            stem = re.sub(r'[^A-Za-z0-9]+', '_', role).strip('_').upper() or 'VALUE'
            stem = stem if stem[0].isalpha() else f'P_{stem}'
            name, n = f'{stem}_PATH', 2
            while name in self.names.values():
                name, n = f'{stem}_PATH_{n}', n + 1
            self.names[path] = name
        return self.names[path]

    def first(self, path: str, data: str, role: str) -> Code:
        """The first match of ``path`` in the variable ``data``, or None."""
        simple = simple_path(path)
        if simple is not None and not simple[1]:
            keys = simple[0]
            return Code(f'dig({data}, {literal(keys)})' if keys else data)
        return Code(f'jsonpaths.first({self._constant(path, role)}, {data})')

    def value(self, ref: str, role: str, record: str = 'record') -> Code:
        """A record's value: one of its keys, or a JSONPath relative to it (``$…``)."""
        if not ref.startswith('$'):
            return Code(f'{record}.get({literal(ref)})')
        simple = simple_path(ref)
        if simple is not None and not simple[1] and len(simple[0]) == 1:
            key = simple[0][0]
            if isinstance(key, str):
                return Code(f'{record}.get({literal(key)})')
        return self.first(ref, record, role)

    def records(self, path: str, data: str) -> Code:
        """The objects a records path points at."""
        simple = simple_path(path)
        if simple is None:
            return Code(f'jsonpaths.records({self._constant(path, "records")}, {data})')
        keys, each = simple
        inner = f'dig({data}, {literal(keys)})' if keys else data
        return Code(f'records_at({inner}, each=True)' if each else f'records_at({inner})')


def template_code(
    text: str,
    names: Mapping[str, str],
    datetimes: Iterable[str] = (),
    *,
    encode: bool = False,
    strings: Iterable[str] = (),
) -> Code:
    """Python that fills a placeholder template, as the engine's formatter renders it.

    ``names`` maps each placeholder to the variable that holds its value. Literal
    parts become ``repr``\\ s; values go through ``text()``, and ``quote()`` too in a
    URL. ``strings`` names placeholders whose values are already text.
    """
    template = formatter.parse(text, tuple(names), tuple(datetimes))
    plain = set(strings)
    parts: list[str] = []
    for part in template.parts:
        if isinstance(part, str):
            parts.append(literal(part))
            continue
        var = ident(names[part.name])
        if part.spec is not None:
            value = f'text({var}, {literal(part.spec)})'
        elif part.name in plain and not encode:
            value = str(var)
        else:
            value = f'text({var})'
        parts.append(f"quote({value}, safe='')" if encode else value)
    return Code(' + '.join(parts) if parts else "''")


def timedelta_code(delta: timedelta) -> Code:
    seconds = int(delta.total_seconds())
    if delta != timedelta(seconds=seconds):
        return Code(f'timedelta(seconds={delta.total_seconds()!r})')
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    parts = [
        f'{name}={amount}'
        for name, amount in (('days', days), ('hours', hours), ('minutes', minutes))
        if amount
    ]
    if secs or not parts:
        parts.append(f'seconds={secs}')
    return Code(f'timedelta({", ".join(parts)})')


def quote_name(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


# --- placeholders and the config -------------------------------------------------------------

PLACEHOLDER = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}')
PLACEHOLDER_NAME = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
YAML_PLAIN = re.compile(r'[A-Za-z0-9_./@+$:{}-]+')
#: Text that reads the same as a plain YAML scalar (checked with safe_load too).
YAML_SAFE_TEXT = re.compile(r'[A-Za-z0-9_/][A-Za-z0-9_./@+:,()-]*( [A-Za-z0-9_./@+,()-]+)*')
YAML_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*')


def placeholder_problems(doc: Mapping[str, Any], template: str) -> list[Problem]:
    """Placeholders eject would have to put in the code (only config values may hold one)."""
    allowed = [re.compile(p) for p in CONFIG_POINTERS.get(template, [])]
    problems: list[Problem] = []

    def walk(node: Any, pointer: str) -> None:
        if isinstance(node, str):
            if '${' in node and not any(a.fullmatch(pointer) for a in allowed):
                problems.append(
                    Problem(
                        pointer,
                        'Eject writes this value into the code, so it cannot be a placeholder.',
                        'Write the value into the spec, save a version, then eject it.',
                    )
                )
        elif isinstance(node, Mapping):
            for key, value in node.items():
                walk(value, f'{pointer}/{key}')
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f'{pointer}/{i}')

    walk(doc, '')
    return problems


def yaml_scalar(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int | float):
        _check_plain(value)
        return json.dumps(value)
    text = str(value)
    if '${' in text:
        # pygeoapi substitutes ${VAR} only in plain (unquoted) scalars.
        if not YAML_PLAIN.fullmatch(text) or ': ' in text or yaml.safe_load(text) != text:
            raise EjectError(f'{text!r} has a placeholder that YAML cannot hold unquoted')
        return text
    plain = YAML_SAFE_TEXT.fullmatch(text) and ': ' not in text and not text.endswith(':')
    if plain and yaml.safe_load(text) == text:
        return text
    return json.dumps(text, ensure_ascii=False)


def yaml_lines(value: Any, indent: int = 0) -> list[str]:
    """Block-style YAML for mappings and lists (lists of numbers in flow style)."""
    pad = ' ' * indent
    lines: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key) if YAML_KEY.fullmatch(str(key)) else json.dumps(str(key))
            if (
                isinstance(item, list)
                and item
                and all(isinstance(v, int | float) and not isinstance(v, bool) for v in item)
            ):
                lines.append(f'{pad}{name}: [{", ".join(yaml_scalar(v) for v in item)}]')
            elif isinstance(item, Mapping | list) and item:
                lines.append(f'{pad}{name}:')
                lines += yaml_lines(item, indent + 2)
            elif isinstance(item, Mapping | list):
                lines.append(f'{pad}{name}: {"{}" if isinstance(item, Mapping) else "[]"}')
            else:
                lines.append(f'{pad}{name}: {yaml_scalar(item)}')
    else:
        for item in value:
            if isinstance(item, Mapping) and item:
                sub = yaml_lines(item, indent + 2)
                lines.append(f'{pad}- {sub[0].lstrip()}')
                lines += sub[1:]
            else:
                lines.append(f'{pad}- {yaml_scalar(item)}')
    return lines


def to_yaml(value: Mapping[str, Any]) -> str:
    text = '\n'.join(yaml_lines(value)) + '\n'
    if yaml.safe_load(text) != json.loads(json.dumps(value)):
        raise EjectError('The example config did not read back as written')
    return text


def connection_data(source: Mapping[str, Any]) -> Any:
    """What a SQL source becomes in the config: a file path, a URL or connection settings."""
    if source['dialect'] == 'sqlite':
        return source['path']
    if source.get('dsn') is not None:
        return source['dsn']
    keys = ('host', 'port', 'dbname', 'user', 'password')
    connection = source.get('connection') or {}
    return {k: connection[k] for k in keys if connection.get(k) is not None}


def connection_fields(prefix: str, source: Mapping[str, Any], title: str) -> list[dict[str, Any]]:
    """The descriptor's config fields for a SQL source at ``prefix`` (``data``, say)."""
    if source['dialect'] == 'sqlite':
        return [
            {
                'key': prefix,
                'type': 'path',
                'title': f'{title}: SQLite file',
                'help': 'The database file, as the pygeoapi server sees it.',
                'required': True,
                'suggest': str(source['path']),
                'step': 'connect',
            }
        ]
    if source.get('dsn') is not None:
        return [
            {
                'key': prefix,
                'type': 'secret',
                'title': f'{title}: connection URL',
                'help': 'A postgresql:// URL, from an environment variable.',
                'required': True,
                'env_name': 'DB_DSN',
                'step': 'connect',
            }
        ]
    connection = source.get('connection') or {}
    out: list[dict[str, Any]] = []
    for key, kind, label in (
        ('host', 'string', 'Host'),
        ('port', 'integer', 'Port'),
        ('dbname', 'string', 'Database'),
        ('user', 'string', 'User'),
    ):
        value = connection.get(key)
        field: dict[str, Any] = {
            'key': f'{prefix}.{key}',
            'type': kind,
            'title': f'{title}: {label}',
            'required': key != 'port',
            'step': 'connect',
        }
        if value is not None and '${' not in str(value):
            field['suggest'] = value
        out.append(field)
    if connection.get('password') is not None:
        out.append(
            {
                'key': f'{prefix}.password',
                'type': 'secret',
                'title': f'{title}: Password',
                'env_name': 'DB_PASSWORD',
                'step': 'connect',
            }
        )
    return out


# --- HTTP sources ----------------------------------------------------------------------------


def auth_options(auth: Mapping[str, Any], prefix: str) -> list[dict[str, Any]]:
    """The ``options`` an HTTP source's auth needs: the variable, the option and its value."""
    out = []
    for var, key in AUTH_OPTIONS.get(str(auth.get('type')), []):
        out.append(
            {
                'var': ident(var),
                'option': prefix + var,
                'key': literal(prefix + var),
                'value': auth.get(key),
                'secret': key != 'username',
            }
        )
    return out


def client_args(
    source: Mapping[str, Any], options: list[dict[str, Any]], *, headers: str, query: str | None
) -> list[tuple[Code, Code]]:
    """The ``Client(...)`` keywords for an HTTP source (its constants are named in the module)."""
    auth = source.get('auth') or {}
    kind = auth.get('type')
    args: list[tuple[Code, Code]] = []
    items: list[str] = []
    if kind == 'header':
        items.append(f'{literal(str(auth["name"]))}: api_key')
    elif kind == 'bearer':
        items.append("'Authorization': 'Bearer ' + token")
    if items:
        merged = ([f'**{headers}'] if source.get('headers') else []) + items
        args.append((Code('headers'), Code('{' + ', '.join(merged) + '}')))
    elif source.get('headers'):
        args.append((Code('headers'), Code(headers)))
    if query:
        args.append((Code('query'), Code(query)))
    if kind == 'query':
        args.append((Code('auth_query'), Code('{' + literal(str(auth['name'])) + ': api_key}')))
    if kind == 'basic':
        args.append((Code('basic_auth'), Code('(username, password)')))
    for name, constant in (
        ('timeout_s', 'TIMEOUT_S'),
        ('retries', 'RETRIES'),
        ('backoff_s', 'BACKOFF_S'),
        ('retry_on', 'RETRY_ON'),
        ('max_bytes', 'MAX_RESPONSE_BYTES'),
    ):
        args.append((Code(name), Code(constant)))
    if float(source.get('cache_ttl_s') or 0):
        args.append((Code('cache_ttl_s'), Code('CACHE_TTL_S')))
    if source.get('rate_limit'):
        args.append((Code('rate_limit'), Code('limiter_for(host, *RATE_LIMIT)')))
    secrets = [str(o['var']) for o in options if o['secret']]
    if secrets:
        args.append((Code('secrets'), Code('[' + ', '.join(secrets) + ']')))
    return args


def http_source_constants(source: Mapping[str, Any]) -> dict[str, Any]:
    retries = source['retries']
    rate = source.get('rate_limit')
    cache = float(source.get('cache_ttl_s') or 0)
    headers = {str(k): str(v) for k, v in (source.get('headers') or {}).items()}
    return {
        'headers': literal(headers) if headers else None,
        'timeout_s': literal(float(source['timeout_s'])),
        'retries': literal(int(retries['max'])),
        'backoff_s': literal(float(retries['backoff_s'])),
        'retry_on': literal(tuple(int(s) for s in retries['on_status'])),
        'max_bytes': number(int(source['max_response_bytes'])),
        'cache_ttl_s': literal(cache) if cache else None,
        'rate_limit': literal((int(rate['requests']), float(rate['per_s']))) if rate else None,
    }


def url_host(url: str) -> str:
    host = urlsplit(url).hostname
    if not host:
        raise EjectError(f'{url!r} has no host')
    return host.lower()


def id_property(data: Mapping[str, Any]) -> str | None:
    ref = data['item_id']['from']
    for p in data['properties']:
        if p.get('from', p['name']) == ref:
            return str(p['name'])
    return None


def field_types(data: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {p['name']: dict(FIELD_TYPES[p['type']]) for p in data['properties']}


def http_context(
    data: Mapping[str, Any], *, class_name: str, prefix: str, module_doc: str, class_doc: str
) -> dict[str, Any]:
    """The http_features template's context for a features spec's data (defaults applied)."""
    paths = Paths()
    source = data['source']
    pagination = data['pagination']
    filters = data['filters']
    limits = data['limits']
    style = pagination['style']
    if '${' in str(source['url']) or '${' in str((data.get('item') or {}).get('url', '')):
        raise EjectError('Eject needs the source and item URLs written out, not placeholders.')
    options = auth_options(source.get('auth') or {}, prefix)
    query = {str(k): formatter.to_text(v) for k, v in source['query'].items()}
    hosts = {url_host(source['url'])}
    item = data.get('item')
    if item:
        hosts.add(url_host(formatter.render(formatter.parse(item['url'], ('id',)), {'id': 'x'})))
    c: dict[str, Any] = {
        'class_name': ident(class_name),
        'docstring': docstring(module_doc),
        'class_doc': docstring(class_doc),
        'source_url': literal(str(source['url'])),
        'hosts': frozen(hosts),
        'query': literal(query) if query else None,
        **http_source_constants(source),
        'client_args': client_args(
            source, options, headers='HEADERS', query='QUERY' if query else None
        ),
        'auth_options': options,
        'page_size': number(int(pagination['max_page_size'])),
        'max_pages': number(int(limits['max_pages'])),
        'max_scanned': number(int(limits['max_records_scanned'])),
        'style': style,
        'style_doc': Code(STYLE_DOCS[style]),
        'fields': literal(field_types(data)),
        'id_property': literal(id_property(data)),
        'title_field': literal(data.get('title_field')),
        'time_field': literal(data.get('time_field')),
        'time_field_name': bool(data.get('time_field')),
        'storage_crs': literal(data['geometry']['crs']) if data['geometry'].get('crs') else None,
    }
    # Pagination.
    if style == 'offset':
        c['offset_param'] = literal(pagination['offset_param'])
        c['limit_param'] = literal(pagination['limit_param'])
    elif style == 'page':
        cfg = pagination['page']
        c['page_param'] = literal(cfg['page_param'])
        c['page_start'] = literal(int(cfg['page_start']))
        c['size_param'] = literal(cfg['size_param']) if cfg.get('size_param') else None
    elif style == 'next_link':
        nxt = pagination['next_link']['next']
        c['next_header'] = nxt == 'header:Link'
        c['next_expr'] = None if c['next_header'] else paths.first(nxt, 'page.doc', 'next_link')
    elif style == 'cursor':
        cfg = pagination['cursor']
        c['cursor_param'] = literal(cfg['cursor_param'])
        c['cursor_expr'] = paths.first(cfg['cursor_path'], 'page.doc', 'cursor')
    total = pagination.get('total')
    c['total_expr'] = paths.first(total, 'doc', 'total') if total else None
    # Filters the source applies itself.
    bbox = filters.get('bbox')
    c['bbox_param'] = literal(bbox['param']) if bbox else None
    c['bbox_expr'] = template_code(bbox['format'], {t: t for t in BBOX_TOKENS}) if bbox else None
    dt = filters.get('datetime') if data.get('time_field') else None
    c['datetime'] = None
    if dt:
        fmt = literal(dt.get('format', 'iso8601'))
        if 'param' in dt:
            c['datetime'] = {'param': literal(dt['param']), 'format': fmt}
        else:
            c['datetime'] = {
                'param': None,
                'start_param': literal(dt['start_param']),
                'end_param': literal(dt['end_param']),
                'format': fmt,
            }
    pushed = {str(k): str(v) for k, v in (filters.get('properties') or {}).items()}
    c['property_params'] = literal(pushed) if pushed else None
    sort = filters.get('sortby')
    c['sort'] = None
    if sort:
        by_name = {p['name']: p for p in data['properties']}
        upstream = {}
        for name in sort['properties']:
            ref = (by_name.get(name) or {}).get('from', name)
            upstream[name] = name if ref.startswith('$') else ref
        c['sort'] = {
            'param': literal(sort['param']),
            'fields': literal(upstream),
            'orders': literal({str(k): str(v) for k, v in (sort.get('orders') or {}).items()}),
            'expr': template_code(
                sort.get('format', '{property}:{order}'),
                {'property': 'upstream', 'order': 'order'},
                strings=('property', 'order'),
            ),
        }
    c['q_param'] = literal(filters['q']['param']) if filters.get('q') else None
    # Items, errors and records.
    c['item'] = None
    if item:
        c['item'] = {
            'url': template_code(item['url'], {'id': 'identifier'}, encode=True),
            'record': (
                paths.first(item['record'], 'doc', 'item_record')
                if item.get('record')
                else Code('doc')
            ),
        }
    error_path = source.get('error_path')
    c['error_expr'] = paths.first(error_path, 'doc', 'error') if error_path else None
    c['records_expr'] = paths.records(data['records'], 'doc')
    # Mapping.
    c['id_expr'] = paths.value(data['item_id']['from'], 'id')
    geometry = data['geometry']
    kind = geometry['type']
    if kind == 'none':
        c['geometry_expr'] = Code('None')
    elif kind == 'point':
        x, y = paths.value(geometry['x'], 'x'), paths.value(geometry['y'], 'y')
        c['geometry_expr'] = Code(f'point({x}, {y})')
    else:
        raw = paths.value(geometry['field'], 'geometry')
        c['geometry_expr'] = Code(f'geometry_from({raw}, {literal(kind)})')
    exprs = []
    for p in data['properties']:
        args = [paths.value(p.get('from', p['name']), p['name']), literal(p['type'])]
        if p['type'] == 'datetime' and p.get('format', 'iso8601') != 'iso8601':
            args.append(literal(p['format']))
        exprs.append((literal(p['name']), Code(f'coerce({", ".join(args)})')))
    c['property_exprs'] = exprs
    c['path_constants'] = paths.constants()
    c['uses_jsonpath'] = bool(paths.names)
    return c


# --- SQL sources -----------------------------------------------------------------------------


def sql_context(
    data: Mapping[str, Any], *, class_name: str, module_doc: str, class_doc: str
) -> dict[str, Any]:
    """The sql_features template's context for a features spec's data (defaults applied)."""
    source = data['source']
    dialect = source['dialect']
    checked = sqlguard.check(data['query'], dialect, pretty=True)
    geometry = data['geometry']
    encoding = geometry['encoding']
    props = data['properties']
    id_column = data['item_id']['from']
    id_type = data['item_id'].get('type') or next(
        (p['type'] for p in props if p.get('from', p['name']) == id_column), None
    )
    geometry_columns: list[str] = []
    if encoding in GEOMETRY_COLUMNS:
        geometry_columns = [geometry['column']]
    elif encoding == 'xy':
        geometry_columns = [geometry['x'], geometry['y']]
    columns = list(
        dict.fromkeys([id_column, *geometry_columns, *(p.get('from', p['name']) for p in props)])
    )
    properties: dict[str, dict[str, Any]] = {}
    for p in props:
        entry: dict[str, Any] = {'column': p.get('from', p['name']), 'type': p['type']}
        if p.get('format'):
            entry['format'] = p['format']
        properties[p['name']] = entry
    pg = dialect == 'postgresql'
    c: dict[str, Any] = {
        'class_name': ident(class_name),
        'docstring': docstring(module_doc),
        'class_doc': docstring(class_doc),
        'dialect': dialect,
        'dialect_literal': literal(dialect),
        'driver': Code('psycopg2' if pg else 'sqlite3'),
        'like': Code('ILIKE' if pg else 'LIKE'),
        'query': text_block(checked.sql),
        'columns': literal(columns),
        'id_column': literal(id_column),
        'id_type': literal(id_type),
        'id_type_name': id_type,
        'properties': literal(properties),
        'text_columns': literal(
            [p.get('from', p['name']) for p in props if p['type'] == 'string']
        ),
        'text_columns_present': any(p['type'] == 'string' for p in props),
        'fields': literal(field_types(data)),
        'id_property': literal(id_property(data)),
        'title_field': literal(data.get('title_field')),
        'time_field': literal(data.get('time_field')),
        'time_field_name': bool(data.get('time_field')),
        'storage_crs': literal(geometry['crs']) if geometry.get('crs') else None,
        'max_rows': number(int(data['limits']['max_rows'])),
        'connect_timeout_s': literal(float(source.get('connect_timeout_s', 5))),
        'statement_timeout_s': literal(float(source.get('statement_timeout_s', 10))),
        'search_path': literal(tuple(source['search_path']))
        if source.get('search_path')
        else None,
        'select': literal('SELECT ' + ', '.join(f'src.{quote_name(col)}' for col in columns)),
        'id_order': literal(f'src.{quote_name(id_column)} ASC'),
        'id_eq': literal(f'src.{quote_name(id_column)} = '),
        'id_text_eq': literal(f'CAST(src.{quote_name(id_column)} AS TEXT) = '),
        'uses_jsonpath': False,
    }
    c['bbox'] = None
    if encoding == 'xy':
        cast = 'DOUBLE PRECISION' if pg else 'REAL'
        c['bbox'] = {
            'kind': 'xy',
            'x_between': literal(f'CAST(src.{quote_name(geometry["x"])} AS {cast}) BETWEEN '),
            'y_between': literal(f'CAST(src.{quote_name(geometry["y"])} AS {cast}) BETWEEN '),
        }
    elif encoding == 'native' and pg:
        column = quote_name(geometry['column'])
        c['bbox'] = {
            'kind': 'native',
            'intersects': literal(
                f'ST_Intersects(CAST(src.{column} AS geometry), ST_MakeEnvelope('
            ),
            'srid': literal(srid_of(geometry.get('crs'))),
        }
    c['time'] = None
    time_field = data.get('time_field')
    prop = next((p for p in props if p['name'] == time_field), None) if time_field else None
    if prop is not None and not prop.get('format') and pg:
        ref = f'src.{quote_name(prop.get("from", prop["name"]))}'
        c['time'] = {
            'ge': literal(f'{ref} >= '),
            'le': literal(f'{ref} <= '),
            'not_null': literal(f'{ref} IS NOT NULL'),
        }
    if encoding in GEOMETRY_COLUMNS:
        c['geometry_expr'] = Code(
            f'decode_geometry(record[{literal(geometry["column"])}], {literal(encoding)})'
        )
    elif encoding == 'xy':
        c['geometry_expr'] = Code(
            f'point(record[{literal(geometry["x"])}], record[{literal(geometry["y"])}])'
        )
    else:
        c['geometry_expr'] = Code('None')
    return c


# --- time series -----------------------------------------------------------------------------


def same_connection(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    keys = ('dialect', 'path', 'dsn', 'connection')
    return all(a.get(k) == b.get(k) for k in keys)


def timeseries_context(
    spec: Spec, *, class_name: str, pkg: str, module_doc: str, class_doc: str
) -> dict[str, Any]:
    data = spec.data
    obs = data['observations']
    paths = Paths()
    params = {p['id']: p for p in data['parameters']}
    coverage_params = {pid: parameter_json(p) for pid, p in params.items()}
    c: dict[str, Any] = {
        'class_name': ident(class_name),
        'docstring': docstring(module_doc),
        'class_doc': docstring(class_doc),
        'fields': literal(spec.fields()),
        'parameters': literal(coverage_params),
        'upstream': literal({pid: p.get('upstream', pid) for pid, p in params.items()}),
        'default_window': timedelta_code(parse_duration(obs['default_window'])),
        'max_window': timedelta_code(parse_duration(obs['max_window'])),
        'max_window_text': literal(obs['max_window']),
        'max_locations': number(int(data['limits']['max_locations'])),
        'max_observations': number(int(data['limits']['max_observations'])),
        'snap_km': literal(float(data['position']['snap_radius_km'])),
        'query_types': list(data['query_types']),
        'locations_plugin': literal(f'{pkg}.locations.Locations'),
        'obs_http': obs['from'] == 'http',
    }
    names = {'location_id': 'location_id', 'parameter': 'upstream', 'start': 'start', 'end': 'end'}
    if obs['from'] == 'http':
        cfg = obs['http']
        if '${' in str(cfg['url']):
            raise EjectError('Eject needs the observations URL written out, not a placeholder.')
        base = {k: v for k, v in cfg.items() if k in SOURCE_KEYS}
        base['url'] = formatter.render(
            formatter.parse(cfg['url'], URL_TOKENS), {'location_id': 'x', 'parameter': 'x'}
        )
        base['query'] = {}
        source = normalize_source(base)
        options = auth_options(source.get('auth') or {}, 'observations_')
        o: dict[str, Any] = {
            'hosts': frozen({url_host(source['url'])}),
            **http_source_constants(source),
            'auth_options': options,
            'client_args': client_args(
                source, options, headers='OBSERVATIONS_HEADERS', query=None
            ),
            'url': template_code(
                cfg['url'], {'location_id': 'location_id', 'parameter': 'upstream'}, encode=True
            ),
            'query': [
                (
                    literal(str(k)),
                    template_code(formatter.to_text(v), names, DATETIME_TOKENS),
                )
                for k, v in (cfg.get('query') or {}).items()
            ],
            'records_expr': paths.records(cfg['records'], 'doc'),
            'time_expr': paths.value(cfg['time']['from'], 'time'),
            'time_format': literal(cfg['time'].get('format', 'iso8601')),
            'value_expr': paths.value(cfg['value']['from'], 'value'),
            'parameter_expr': (
                paths.value(cfg['parameter']['from'], 'parameter')
                if cfg.get('parameter')
                else None
            ),
            'error_expr': (
                paths.first(cfg['error_path'], 'doc', 'error') if cfg.get('error_path') else None
            ),
        }
    else:
        cfg = obs['sql']
        source = cfg['source']
        dialect = source['dialect']
        checked = sqlguard.check(cfg['query'], dialect, tuple(sorted(TOKENS)), pretty=True)
        loc = data['locations']
        shared = loc['from'] == 'sql' and same_connection(loc['sql']['source'], source)
        bound = {
            'location_id': 'location_id',
            'parameter': 'upstream',
            'start': 'to_iso(start)' if dialect == 'sqlite' else 'start',
            'end': 'to_iso(end)' if dialect == 'sqlite' else 'end',
        }
        o = {
            'query': text_block(checked.sql),
            'dialect_literal': literal(dialect),
            'dialect': dialect,
            'params': [
                (literal(name), Code(bound[name]))
                for name in TOKENS
                if name in checked.placeholders
            ],
            'time_column': literal(cfg['time']['from']),
            'time_format': literal(cfg['time'].get('format', 'iso8601')),
            'value_column': literal(cfg['value']['from']),
            'parameter_column': (
                literal(cfg['parameter']['from']) if cfg.get('parameter') else None
            ),
            'connect_timeout_s': literal(float(source.get('connect_timeout_s', 5))),
            'statement_timeout_s': literal(float(source.get('statement_timeout_s', 10))),
            'search_path': (
                literal(tuple(source['search_path'])) if source.get('search_path') else None
            ),
            'data_expr': Code('self.data' if shared else "setting(self.options, 'observations')"),
            'shared': shared,
        }
    c['obs'] = o
    c['path_constants'] = paths.constants()
    c['uses_jsonpath'] = bool(paths.names)
    return c


# --- the package ------------------------------------------------------------------------------


def check_names(req: EjectRequest) -> tuple[str, str]:
    if not SLUG.match(req.id):
        raise EjectError(f'{req.id!r} is not a provider id: lower-case letters, digits, -')
    pkg = req.package or default_package(req.id)
    cls = req.class_name or default_class(req.id)
    if not PACKAGE.match(pkg) or keyword.iskeyword(pkg) or not pkg.isidentifier():
        raise EjectError(f'{pkg!r} is not a Python package name: lower-case letters, digits, _')
    if not CLASS.match(cls) or keyword.iskeyword(cls) or cls == 'Locations':
        raise EjectError(f'{cls!r} is not a class name to use: CamelCase letters and digits')
    if pkg in ('pygeoapi', 'pygeoapi_spec_providers') or pkg in sys.stdlib_module_names:
        raise EjectError(f'{pkg!r} would shadow another package; pick another name')
    return pkg, cls


def eject_files(
    spec_text: str, spec_version: str, req: EjectRequest, *, today: str | None = None
) -> dict[str, str]:
    """Every file of the ejected package, by path; the Python not yet tidied by ruff."""
    raw = yaml.safe_load(spec_text)
    if not isinstance(raw, dict):
        raise EjectError('A spec must be a YAML mapping.')
    template = str(raw.get('template', 'http-features'))
    if template not in CONFIG_POINTERS:
        raise EjectError(f'Eject does not handle {template} specs.')
    problems = placeholder_problems(raw, template)
    if problems:
        raise EjectError('Some placeholders would have to go into the code.', problems)
    try:
        spec = parse(raw, f'{raw.get("id")} {spec_version}')
    except SpecError as err:
        raise EjectError('The spec has problems.', err.problems) from err
    pkg, cls = check_names(req)
    today = today or datetime.now(UTC).strftime('%Y-%m-%d')
    data = spec.data
    title = (req.title or str(data['title'])).strip()
    plugin = f'{pkg}.provider.{cls}'
    provider_type = 'edr' if template == 'timeseries-edr' else 'feature'
    origin = (
        f'Ejected from the Provider Builder spec ``{spec.id}``, version {spec_version}, '
        f'on {today}.'
    )
    env = scaffold_environment()
    files: dict[str, str] = {}
    helpers = ['support.py']
    options: dict[str, Any] = {}
    fields: list[dict[str, Any]] = []
    requires = [pkg]
    dependencies = [f'pygeoapi=={pygeoapi.__version__}', 'shapely>=2.0']
    hosts = sorted(str(h) for h in egress.spec(raw))
    sql_keys: list[str] = []
    left_out: list[str] = []
    postgresql = False

    def add_http(
        source: Mapping[str, Any], prefix: str, label: str, opts: list[dict[str, Any]]
    ) -> None:
        if 'http_client.py' not in helpers:
            helpers.append('http_client.py')
            dependencies.append('requests>=2.31')
            requires.append('requests')
            left_out.append(
                'It resolved each host itself and refused link-local and cloud-metadata '
                'addresses. This code connects to the hosts it declares (`HOSTS`): run it where '
                'the network reaches only what it needs.'
            )
        for o in opts:
            options[o['option']] = o['value']
            fields.append(
                {
                    'key': f'options.{o["option"]}',
                    'type': 'secret' if o['secret'] else 'string',
                    'title': f'{label}: {AUTH_TITLES[str(o["var"])]}',
                    'required': True,
                    **({'env_name': o['option'].upper()} if o['secret'] else {}),
                    'step': 'connect',
                }
            )

    def add_sql(source: Mapping[str, Any], key: str, label: str) -> None:
        nonlocal postgresql
        if 'database.py' not in helpers:
            helpers.append('database.py')
            dependencies.append('sqlalchemy>=2.0,<2.1')
            requires.append('sqlalchemy')
            left_out.append(
                'It refused database hosts that resolve to link-local or cloud-metadata '
                'addresses. This code connects where the config says.'
            )
        if source['dialect'] == 'postgresql':
            postgresql = True
            if 'psycopg2' not in requires:
                requires.append('psycopg2')
            sql_keys.append(key)
        fields.extend(connection_fields(key, source, label))

    if template == 'http-features':
        source = data['source']
        opts = auth_options(source.get('auth') or {}, '')
        add_http(source, '', 'Source', opts)
        fields.insert(
            0,
            {
                'key': 'data',
                'type': 'url',
                'title': 'Source API',
                'help': 'The URL the provider lists features from.',
                'required': True,
                'suggest': str(source['url']),
                'step': 'connect',
            },
        )
        data_value: Any = source['url']
        context = http_context(
            data,
            class_name=cls,
            prefix='',
            module_doc='',
            class_doc=f'{title}: features from {url_host(source["url"])}.',
        )
        uses_jsonpath = context['uses_jsonpath']
        module_doc = provider_doc(
            title,
            'an HTTP JSON API as OGC API - Features.',
            origin,
            'Requests go only to HOSTS. Filters the source applies itself go in the request;\n'
            'the others apply to the features after reading, up to MAX_RECORDS_SCANNED\n'
            'records.',
        )
        main = ('http_features.py.j2', context)
        locations = None
    elif template == 'sql-features':
        source = data['source']
        add_sql(source, 'data', 'Database')
        data_value = connection_data(source)
        context = sql_context(
            data, class_name=cls, module_doc='', class_doc=f'{title}: features from a SQL query.'
        )
        uses_jsonpath = False
        module_doc = provider_doc(
            title,
            'a SQL query as OGC API - Features.',
            origin,
            'The query runs as a subquery, ``SELECT <columns> FROM (QUERY) AS src WHERE …\n'
            'ORDER BY … LIMIT …``, in a read-only transaction with a statement timeout, and\n'
            "filter values are bound parameters. Filters the database can't apply (a bbox on\n"
            'text geometries, datetimes stored as text) apply after reading, up to MAX_ROWS\n'
            'rows.',
        )
        main = ('sql_features.py.j2', context)
        locations = None
    else:
        block = data['locations']
        kind = block['from']
        loc_spec = sub_spec(spec, f'{kind}-features', block[kind])
        loc_doc = (
            textwrap.fill(
                f'The monitoring locations of {title}, as a features provider that '
                'provider.py reads.',
                88,
            )
            + '\n\n'
            + textwrap.fill(origin, 88)
            + '\n'
        )
        if kind == 'http':
            opts = auth_options(loc_spec.data['source'].get('auth') or {}, 'locations_')
            add_http(loc_spec.data['source'], 'locations_', 'Locations', opts)
            fields.insert(
                0,
                {
                    'key': 'data',
                    'type': 'url',
                    'title': 'Locations API',
                    'help': 'The URL the monitoring locations come from.',
                    'required': True,
                    'suggest': str(loc_spec.data['source']['url']),
                    'step': 'connect',
                },
            )
            data_value = loc_spec.data['source']['url']
            loc_context = http_context(
                loc_spec.data,
                class_name='Locations',
                prefix='locations_',
                module_doc=loc_doc,
                class_doc='The monitoring locations.',
            )
            locations = ('http_features.py.j2', loc_context)
        else:
            add_sql(loc_spec.data['source'], 'data', 'Locations database')
            data_value = connection_data(loc_spec.data['source'])
            loc_context = sql_context(
                loc_spec.data,
                class_name='Locations',
                module_doc=loc_doc,
                class_doc='The monitoring locations.',
            )
            locations = ('sql_features.py.j2', loc_context)
        context = timeseries_context(
            spec, class_name=cls, pkg=pkg, module_doc='', class_doc=f'{title}: time series.'
        )
        obs = data['observations']
        if obs['from'] == 'http':
            obs_opts = context['obs']['auth_options']
            add_http(obs['http'], 'observations_', 'Observations', obs_opts)
        elif not context['obs']['shared']:
            add_sql(obs['sql']['source'], 'options.observations', 'Observations database')
            options['observations'] = connection_data(obs['sql']['source'])
        uses_jsonpath = context['uses_jsonpath'] or loc_context['uses_jsonpath']
        source_doc = 'API' if obs['from'] == 'http' else 'query'
        module_doc = provider_doc(
            title,
            'time series as OGC API - EDR.',
            origin,
            f'Locations come from ``locations.py``, observations from the observations\n'
            f'{source_doc}, one location and parameter at a time. ``locations`` without an id\n'
            'is a GeoJSON FeatureCollection; everything else is a CoverageJSON\n'
            'CoverageCollection of PointSeries, one per location and parameter. A query\n'
            'without observations is HTTP 204 (ProviderNoDataError): a CoverageCollection\n'
            "can't be empty.",
        )
        main = ('timeseries_edr.py.j2', context)
    if uses_jsonpath:
        helpers.append('jsonpaths.py')
        dependencies.append('python-jsonpath>=2.2,<3')
        requires.append('jsonpath')
    # The example config: what the contract checks run against, and the README shows.
    provider: dict[str, Any] = {'type': provider_type, 'name': plugin, 'data': data_value}
    if options:
        provider['options'] = options
    resource = {
        req.id: {
            'type': 'collection',
            'title': title,
            'description': str(data.get('description') or title),
            'keywords': [provider_type],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
            'providers': [provider],
        }
    }
    snippet = to_yaml(resource)
    provider_yaml = to_yaml({'providers': [provider]})
    main[1]['docstring'] = docstring(module_doc + '\nConfig::\n\n' + indent(provider_yaml, 4))
    render = environment()

    def r(name: str, ctx: dict[str, Any]) -> str:
        return render.get_template(name).render(c=ctx)

    files[f'src/{pkg}/provider.py'] = r(*main)
    if locations is not None:
        files[f'src/{pkg}/locations.py'] = r(*locations)
    for helper in helpers:
        files[f'src/{pkg}/{helper}'] = (EJECT_TEMPLATES / helper).read_text('utf-8')
    env_names = sorted(set(PLACEHOLDER_NAME.findall(snippet)))
    capabilities: dict[str, Any]
    if provider_type == 'edr':
        capabilities = {'query_types': list(data['query_types'])}
    else:
        capabilities = dict(capabilities_for(raw))
    descriptor = ejected_descriptor(
        req.id,
        plugin=plugin,
        provider_type=provider_type,
        title=title,
        template=template,
        spec_id=spec.id,
        spec_version=spec_version,
        requires=requires,
        hosts=hosts,
        sql_keys=sql_keys,
        capabilities=capabilities,
        fields=fields,
        data=data,
    )
    pkg_ctx = {
        'title': markdown(title),
        'dist': Code(json.dumps(pkg.replace('_', '-'))),
        'description': Code(json.dumps(f'{title}: a pygeoapi {provider_type} provider')),
        'dependencies': [Code(json.dumps(d)) for d in dependencies],
        'postgresql': postgresql,
        'pkg': ident(pkg),
        'type': Code(provider_type),
        'template': Code(template),
        'spec_id': markdown(spec.id),
        'spec_version': markdown(spec_version),
        'date': Code(today),
        'pygeoapi_version': Code(pygeoapi.__version__),
        'provider_yaml': Code(provider_yaml.rstrip('\n')),
        'env_names': markdown(', '.join(f'`{n}`' for n in env_names)) if env_names else None,
        'env': literal(env_names),
        'capabilities': literal(capabilities),
        'init_doc': docstring(f'{title}: a pygeoapi {provider_type} provider (ejected).'),
        'files': [markdown(line) for line in package_lines(pkg, helpers, locations is not None)],
        'left_out': [markdown(line) for line in left_out or ['Nothing that changes results.']],
    }
    files.update(
        {
            'pyproject.toml': r('pyproject.toml.j2', pkg_ctx),
            'README.md': r('README.md.j2', pkg_ctx),
            'CHANGELOG.md': r('CHANGELOG.md.j2', pkg_ctx),
            f'src/{pkg}/__init__.py': r('init.py.j2', pkg_ctx),
            'tests/test_contract.py': r('test_contract.py.j2', pkg_ctx),
            '.gitignore': env.get_template('package/gitignore.j2').render(),
            'Dockerfile.test': env.get_template('package/Dockerfile.test.j2').render(
                pygeoapi_version=pygeoapi.__version__, dist=pkg.replace('_', '-')
            ),
            'examples/pygeoapi-config.snippet.yml': (
                '# Add under resources: in your pygeoapi config.\n' + snippet
            ),
            f'src/{pkg}/descriptor.yaml': (
                '# The Provider Builder catalog entry for this provider '
                '(catalog/schema/descriptor.schema.json).\n'
                '# Keep it current: Mode A builds its form from these fields.\n'
                + yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True)
            ),
            f'docs/spec-{spec_version}.yaml': (
                '# The Provider Builder spec this package was ejected from. Nothing reads it.\n'
                + spec_text
            ),
            'tests/fixtures/README.md': 'Test data for the provider (no network in tests).\n',
            '.provider-builder.json': json.dumps(
                {
                    'scaffold_version': 1,
                    'pygeoapi': pygeoapi.__version__,
                    'provider_type': provider_type,
                    'id': req.id,
                    'title': title,
                    'description': str(data.get('description') or ''),
                    'package': pkg,
                    'class_name': cls,
                    'style': 'sql' if 'database.py' in helpers else 'rest',
                    'ejected_from': {
                        'spec': spec.id,
                        'version': spec_version,
                        'template': template,
                        'date': today,
                    },
                },
                indent=2,
            )
            + '\n',
        }
    )
    return files


def indent(text: str, width: int) -> str:
    pad = ' ' * width
    return ''.join(pad + line if line.strip() else line for line in text.splitlines(True))


def provider_doc(title: str, summary: str, origin: str, how: str) -> str:
    """The provider module's docstring, before its config example (paragraphs wrapped)."""
    settings = (
        "The spec's settings are the constants below, and the code does what the spec "
        'engine did with them. Nothing reads the spec any more, so change anything.'
    )
    paragraphs = [f'{title}: {summary}', f'{origin} {settings}', ' '.join(how.split())]
    return '\n\n'.join(textwrap.fill(p, 88) for p in paragraphs) + '\n'


def package_lines(pkg: str, helpers: list[str], has_locations: bool) -> list[str]:
    lines = [f"`src/{pkg}/provider.py`: the provider, with the spec's settings as constants."]
    if has_locations:
        lines.append(
            f'`src/{pkg}/locations.py`: the monitoring locations, a features provider of its own.'
        )
    notes = {
        'support.py': 'values, times, geometries, the filters applied after reading, sorting.',
        'http_client.py': 'HTTP: auth, retries, redirects on declared hosts, a size cap.',
        'database.py': 'read-only connections with statement timeouts; geometry decoding.',
        'jsonpaths.py': 'JSONPaths that need more than plain keys and indexes.',
    }
    lines += [f'`src/{pkg}/{h}`: {notes[h]}' for h in helpers]
    lines.append(f'`src/{pkg}/descriptor.yaml`: how the Provider Builder catalog shows it.')
    return lines


def ejected_descriptor(
    pid: str,
    *,
    plugin: str,
    provider_type: str,
    title: str,
    template: str,
    spec_id: str,
    spec_version: str,
    requires: list[str],
    hosts: list[str],
    sql_keys: list[str],
    capabilities: Mapping[str, Any],
    fields: list[dict[str, Any]],
    data: Mapping[str, Any],
) -> dict[str, Any]:
    rule: dict[str, Any] = {'kind': 'hosts', 'hosts': hosts}
    if sql_keys:
        rule['sql_keys'] = list(dict.fromkeys(sql_keys))
    descriptor: dict[str, Any] = {
        'descriptor_version': 1,
        'id': f'created/{pid}',
        'plugin': plugin,
        'provider_type': provider_type,
        'origin': 'created',
        'title': title,
        'summary': f'Ejected from the {template} spec {spec_id} {spec_version}.',
        'ogc_api': ['edr' if provider_type == 'edr' else 'features'],
        'version': '0.1.0',
        'requires': list(dict.fromkeys(requires)),
        'probe': {'kind': provider_type},
        'egress': rule,
        'hide': list(HIDDEN),
        'notes': [
            'Install this package on the pygeoapi server (pip install). It does not need '
            'pygeoapi-spec-providers.'
        ],
    }
    if provider_type == 'edr':
        descriptor['query_types'] = list(capabilities['query_types'])
        descriptor['hide'] = list(HIDDEN) + ['title_field', 'time_field', 'properties', 'count']
    else:
        descriptor['capabilities'] = dict(capabilities)
    out = list(fields)
    if provider_type == 'feature':
        for key, label, kinds in (
            ('title_field', 'Title field', ['string']),
            ('time_field', 'Time field', None),
        ):
            if data.get(key):
                field: dict[str, Any] = {
                    'key': key,
                    'type': 'field',
                    'title': label,
                    'help': f"Leave it to use the provider's ({data[key]}).",
                    'default': data[key],
                    'choices_from': 'probe.fields',
                    'step': 'map',
                }
                if kinds:
                    field['field_types'] = kinds
                out.append(field)
    descriptor['fields'] = out
    return descriptor


# --- tidying and writing ---------------------------------------------------------------------


def tidy(files: dict[str, str], config: Path) -> dict[str, str]:
    """Drop unused imports, sort them and format, as the package's own ruff config says."""
    out = dict(files)
    for name, source in files.items():
        if name.endswith('.py'):
            fixed = ruff(
                ['check', '--fix-only', '--select', 'F401,I', '--exit-zero'], source, name, config
            )
            out[name] = ruff(['format'], fixed or source, name, config)
    return out


def write(files: dict[str, str], folder: Path) -> Path:
    """Tidy and write an ejected package to ``folder``, which must not exist."""
    if folder.exists():
        raise EjectError(f'{folder.name} already exists in the workspace')
    folder.mkdir(parents=True)
    (folder / 'pyproject.toml').write_text(files['pyproject.toml'], encoding='utf-8')
    for name, content in tidy(files, folder / 'pyproject.toml').items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return folder
