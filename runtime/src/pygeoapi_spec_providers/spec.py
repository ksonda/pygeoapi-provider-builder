"""Load and validate provider specs (PLAN.md §7.1-7.2; ADR-0005, ADR-0016).

A spec is data. It is loaded with pygeoapi's own ``yaml_load``, so ``${VAR}``
behaves exactly as it does in the pygeoapi config, then checked against
``schemas/spec-v1.json`` and the semantic rules below. ``check_text`` runs on
the raw text, before environment variables are applied, and catches
placeholders that ``yaml_load`` would silently leave alone, and secrets
written into the spec itself.
"""

from __future__ import annotations

import copy
import functools
import importlib.resources
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

from . import formatter, jsonpaths

SCHEMA: dict[str, Any] = json.loads(
    importlib.resources.files(__package__).joinpath('schemas/spec-v1.json').read_text('utf-8')
)
VALIDATOR = Draft202012Validator(SCHEMA)

#: Spec property types, as pygeoapi field definitions (notes §5.1: dicts with a type).
FIELD_TYPES: dict[str, dict[str, str]] = {
    'string': {'type': 'string'},
    'integer': {'type': 'integer'},
    'number': {'type': 'number'},
    'boolean': {'type': 'boolean'},
    'datetime': {'type': 'string', 'format': 'date-time'},
}
DATETIME_FORMATS = frozenset({'iso8601', 'date', 'epoch_s', 'epoch_ms'})
SECRET_NAME = re.compile(r'(?i)auth|cookie|token|secret|passw|api[-_]?key|signature')
SECRET_PLACEHOLDER = re.compile(r'^\$\{[A-Za-z_][A-Za-z0-9_]*\}$')
BBOX_TOKENS = ('minx', 'miny', 'maxx', 'maxy')

DEFAULTS: dict[str, Any] = {
    'source': {
        'query': {},
        'headers': {},
        'timeout_s': 20,
        'retries': {'max': 2, 'backoff_s': 1, 'on_status': [429, 502, 503, 504]},
        'cache_ttl_s': 0,
        'max_response_bytes': 20_000_000,
    },
    'pagination': {'style': 'none', 'max_page_size': 100},
    'filters': {},
    'limits': {'max_records_scanned': 10_000, 'max_pages': 50},
}


@dataclass(frozen=True)
class Problem:
    pointer: str
    message: str
    hint: str | None = None


class SpecError(ValueError):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        shown = '; '.join(f'{p.pointer or "/"}: {p.message}' for p in problems[:5])
        more = f' (and {len(problems) - 5} more)' if len(problems) > 5 else ''
        super().__init__(f'Invalid spec: {shown}{more}')


@dataclass(frozen=True, eq=False)
class Spec:
    """A validated spec, with defaults filled in. Hashable by identity."""

    data: dict[str, Any]
    origin: str

    @property
    def id(self) -> str:
        return str(self.data['id'])

    @property
    def title(self) -> str:
        return str(self.data['title'])

    @property
    def source(self) -> dict[str, Any]:
        return dict(self.data['source'])

    @property
    def properties(self) -> list[dict[str, Any]]:
        return list(self.data['properties'])

    def fields(self) -> dict[str, dict[str, str]]:
        return {p['name']: dict(FIELD_TYPES[p['type']]) for p in self.data['properties']}

    def get_property(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.data['properties'] if p['name'] == name), None)

    @property
    def id_property(self) -> str | None:
        """The property that holds the item id, if one does."""
        ref = self.data['item_id']['from']
        for p in self.data['properties']:
            if p.get('from', p['name']) == ref:
                return str(p['name'])
        return None

    def hosts(self) -> set[str]:
        return declared_hosts(self.data)


def declared_hosts(doc: Mapping[str, Any]) -> set[str]:
    hosts = set()
    for url in (doc.get('source', {}).get('url'), doc.get('item', {}).get('url')):
        if isinstance(url, str):
            host = urlsplit(url).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def merge_defaults(doc: Mapping[str, Any]) -> dict[str, Any]:
    def merge(base: Any, over: Any) -> Any:
        if isinstance(base, dict) and isinstance(over, Mapping):
            out = copy.deepcopy(base)
            for k, v in over.items():
                out[k] = merge(out.get(k), v) if k in out else copy.deepcopy(v)
            return out
        return copy.deepcopy(over)

    data: dict[str, Any] = merge(DEFAULTS, doc)
    page = data['pagination'].get('page')
    if page is not None:
        page.setdefault('page_start', 1)
    return data


# --- validation ---------------------------------------------------------------------------


def _pointer(parts: Iterable[Any]) -> str:
    return ''.join(f'/{str(p).replace("~", "~0").replace("/", "~1")}' for p in parts)


def schema_problems(doc: Any) -> list[Problem]:
    problems = []
    for err in VALIDATOR.iter_errors(doc):
        leaves = list(err.context) or [err]
        for leaf in leaves:
            problems.append(Problem(_pointer(leaf.absolute_path), leaf.message))
    # A template branch that fails leaves its keys "unevaluated"; report the branch's errors only.
    if any(not p.message.startswith('Unevaluated properties') for p in problems):
        problems = [p for p in problems if not p.message.startswith('Unevaluated properties')]
    seen: set[tuple[str, str]] = set()
    unique = []
    for p in sorted(problems, key=lambda p: (p.pointer, p.message)):
        if (p.pointer, p.message) not in seen:
            seen.add((p.pointer, p.message))
            unique.append(p)
    return unique


def semantic_problems(doc: Mapping[str, Any]) -> list[Problem]:
    """Rules the JSON Schema can't express. Assumes the schema passed."""
    problems: list[Problem] = []

    def path(pointer: str, value: str) -> None:
        try:
            jsonpaths.compile_path(value)
        except jsonpaths.PathError as err:
            problems.append(Problem(pointer, str(err)))

    def ref(pointer: str, value: str) -> None:
        if value.startswith('$'):
            path(pointer, value)

    def template(
        pointer: str, text: str, allowed: Iterable[str], datetimes: Iterable[str] = ()
    ) -> None:
        try:
            formatter.parse(text, allowed, datetimes)
        except formatter.FormatError as err:
            problems.append(Problem(pointer, str(err)))

    source = doc['source']
    url = urlsplit(source['url'])
    if url.username or url.password:
        problems.append(
            Problem(
                '/source/url',
                'The URL contains credentials.',
                'Put them under source.auth, as environment variables.',
            )
        )
    if url.query:
        problems.append(
            Problem('/source/url', 'Put query parameters under source.query, not in the URL.')
        )
    for name in source.get('query', {}):
        if SECRET_NAME.search(name):
            problems.append(
                Problem(
                    f'/source/query/{name}',
                    f'{name!r} looks like a credential.',
                    'Use source.auth with type: query instead.',
                )
            )
    for name in source.get('headers', {}):
        if SECRET_NAME.search(name):
            problems.append(
                Problem(
                    f'/source/headers/{name}',
                    f'{name!r} looks like a credential.',
                    'Use source.auth with type: header or bearer instead.',
                )
            )
    if 'error_path' in source:
        path('/source/error_path', source['error_path'])

    path('/records', doc['records'])
    ref('/item_id/from', doc['item_id']['from'])
    geometry = doc['geometry']
    for key in ('x', 'y', 'field'):
        if key in geometry:
            ref(f'/geometry/{key}', geometry[key])

    names: dict[str, str] = {}
    for i, prop in enumerate(doc['properties']):
        if prop['name'] in names:
            problems.append(
                Problem(f'/properties/{i}/name', f'Duplicate property {prop["name"]!r}.')
            )
        names[prop['name']] = prop['type']
        if 'from' in prop:
            ref(f'/properties/{i}/from', prop['from'])
        fmt = prop.get('format')
        if fmt is not None:
            if prop['type'] != 'datetime':
                problems.append(
                    Problem(f'/properties/{i}/format', 'Only datetime properties take a format.')
                )
            elif fmt not in DATETIME_FORMATS:
                try:
                    formatter.check_strftime(fmt)
                except formatter.FormatError as err:
                    problems.append(
                        Problem(
                            f'/properties/{i}/format',
                            str(err),
                            'Use iso8601, date, epoch_s, epoch_ms or a strftime pattern.',
                        )
                    )

    for key, kind in (('time_field', 'datetime'), ('title_field', 'string')):
        name = doc.get(key)
        if name is None:
            continue
        if name not in names:
            problems.append(Problem(f'/{key}', f'{name!r} is not one of the properties.'))
        elif names[name] != kind:
            problems.append(
                Problem(f'/{key}', f'{key} must be a {kind} property; {name!r} is {names[name]}.')
            )

    pagination = doc.get('pagination', {})
    if 'total' in pagination:
        path('/pagination/total', pagination['total'])
    if 'cursor' in pagination:
        path('/pagination/cursor/cursor_path', pagination['cursor']['cursor_path'])
    if 'next_link' in pagination:
        nxt = pagination['next_link']['next']
        if nxt != 'header:Link':
            path('/pagination/next_link/next', nxt)

    filters = doc.get('filters', {})
    if 'bbox' in filters:
        template('/filters/bbox/format', filters['bbox']['format'], BBOX_TOKENS)
    if 'datetime' in filters:
        dt = filters['datetime']
        if 'param' not in dt and not ('start_param' in dt and 'end_param' in dt):
            problems.append(
                Problem('/filters/datetime', 'Give param, or start_param and end_param.')
            )
        fmt = dt.get('format')
        if fmt and fmt not in DATETIME_FORMATS:
            try:
                formatter.check_strftime(fmt)
            except formatter.FormatError as err:
                problems.append(Problem('/filters/datetime/format', str(err)))
    for prop_name in filters.get('properties', {}):
        if prop_name not in names:
            problems.append(
                Problem(
                    f'/filters/properties/{prop_name}',
                    f'{prop_name!r} is not one of the properties.',
                )
            )
    if 'sortby' in filters:
        sortby = filters['sortby']
        if 'format' in sortby:
            template('/filters/sortby/format', sortby['format'], ('property', 'order'))
        for i, prop_name in enumerate(sortby['properties']):
            if prop_name not in names:
                problems.append(
                    Problem(
                        f'/filters/sortby/properties/{i}',
                        f'{prop_name!r} is not one of the properties.',
                    )
                )

    item = doc.get('item')
    if item:
        template('/item/url', item['url'], ('id',))
        if '{id}' not in item['url']:
            problems.append(Problem('/item/url', 'The item URL needs an {id} placeholder.'))
        if 'record' in item:
            path('/item/record', item['record'])
    return problems


SOURCE_VALIDATOR = Draft202012Validator({'$ref': '#/$defs/source', '$defs': SCHEMA['$defs']})


def validate_source(source: Any) -> list[Problem]:
    """Checks for a ``source`` block on its own (J4 samples a URL before there's a spec)."""
    problems = [
        Problem('/source' + _pointer(e.absolute_path), e.message)
        for e in SOURCE_VALIDATOR.iter_errors(source)
    ]
    if problems:
        return problems
    doc = {
        'source': source,
        'records': '$',
        'item_id': {'from': 'id'},
        'geometry': {'type': 'none'},
        'properties': [{'name': 'id', 'type': 'string'}],
    }
    return [p for p in semantic_problems(doc) if p.pointer.startswith('/source')]


def normalize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """A ``source`` block with defaults applied."""
    return dict(merge_defaults({'source': source})['source'])


def validate(doc: Any) -> list[Problem]:
    problems = schema_problems(doc)
    if problems:
        return problems
    return semantic_problems(doc)


def check_text(text: str) -> list[Problem]:
    """Checks on the raw spec text, before ``${VAR}`` placeholders are applied."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as err:
        hint = None
        if '${' in text:
            hint = 'Placeholders such as ${VAR} only work in block style, not inside {…} or […].'
        return [Problem('', f"The YAML doesn't parse: {str(err).splitlines()[0]}", hint)]
    problems: list[Problem] = []

    def walk(node: yaml.Node | None, pointer: str, flow: bool) -> None:
        if isinstance(node, yaml.ScalarNode):
            if '${' not in node.value:
                return
            if node.style in ('"', "'", '|', '>'):
                problems.append(
                    Problem(
                        pointer,
                        'This placeholder is quoted, so pygeoapi leaves it as text.',
                        'Remove the quotes: pygeoapi substitutes only plain values.',
                    )
                )
            elif flow:
                problems.append(
                    Problem(
                        pointer,
                        'This placeholder is inside {…} or […].',
                        'Use block style (one key per line) for values with ${VAR}.',
                    )
                )
        elif isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                walk(value, f'{pointer}/{key.value}', flow or bool(node.flow_style))
        elif isinstance(node, yaml.SequenceNode):
            for i, value in enumerate(node.value):
                walk(value, f'{pointer}/{i}', flow or bool(node.flow_style))

    walk(root, '', False)
    raw = yaml.safe_load(text)
    auth = raw.get('source', {}).get('auth') if isinstance(raw, dict) else None
    if isinstance(auth, dict):
        for key in ('value', 'password'):
            value = auth.get(key)
            if value is not None and not (
                isinstance(value, str) and SECRET_PLACEHOLDER.match(value)
            ):
                problems.append(
                    Problem(
                        f'/source/auth/{key}',
                        'Secrets must come from an environment variable.',
                        f'Write {key}: ${{MY_SECRET}} and set MY_SECRET on the server.',
                    )
                )
    return problems


# --- loading ------------------------------------------------------------------------------


def parse(doc: Any, origin: str) -> Spec:
    if not isinstance(doc, dict):
        raise SpecError([Problem('', 'A spec must be a YAML mapping.')])
    problems = validate(doc)
    if problems:
        raise SpecError(problems)
    return Spec(merge_defaults(doc), origin)


def _yaml_load(stream: io.TextIOBase, origin: str) -> Any:
    from pygeoapi.util import yaml_load

    try:
        return yaml_load(stream)
    except OSError as err:  # a missing ${VAR} raises EnvironmentError (notes §2)
        raise SpecError([Problem('', f'{err} (needed by {origin})')]) from err
    except yaml.YAMLError as err:
        raise SpecError(
            [Problem('', f"The YAML doesn't parse: {str(err).splitlines()[0]}")]
        ) from err


@functools.lru_cache(maxsize=64)
def _load_inline(text: str) -> Spec:
    return parse(json.loads(text), 'inline')


@functools.lru_cache(maxsize=64)
def _load_file(path: str, mtime_ns: int, size: int) -> Spec:
    with open(path, encoding='utf-8') as fh:
        return parse(_yaml_load(fh, path), path)


@functools.lru_cache(maxsize=64)
def _load_package(ref: str) -> Spec:
    module, _, resource = ref[len('pkg:') :].partition('/')
    if not module or not resource:
        raise SpecError([Problem('', f'{ref!r} must look like pkg:<module>/<resource>.')])
    try:
        text = importlib.resources.files(module).joinpath(resource).read_text('utf-8')
    except (ModuleNotFoundError, FileNotFoundError) as err:
        raise SpecError([Problem('', f"Can't read {ref}: {err}")]) from err
    return parse(_yaml_load(io.StringIO(text), ref), ref)


def load_spec(ref: str | Mapping[str, Any]) -> Spec:
    """Load a spec from a path, a ``pkg:<module>/<resource>`` reference or an inline mapping.

    Specs are cached per process (files by path, mtime and size; inline mappings
    by content), so pygeoapi creating a provider per request stays cheap.
    """
    if isinstance(ref, Mapping):
        return _load_inline(json.dumps(ref, sort_keys=True, default=str))
    if ref.startswith('pkg:'):
        return _load_package(ref)
    path = Path(ref)
    try:
        stat = path.stat()
    except OSError as err:
        raise SpecError([Problem('', f"Can't read the spec file {ref}: {err.strerror}")]) from err
    return _load_file(str(path), stat.st_mtime_ns, stat.st_size)
