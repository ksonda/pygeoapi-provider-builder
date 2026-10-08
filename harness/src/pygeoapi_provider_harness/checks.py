"""Contract checks for pygeoapi providers (PLAN.md §7.7).

Each check is a pure function over a loaded provider (L1), pygeoapi's API
called in-process (L2) or a gunicorn server in this process's container
(L3). Results form a checklist: ``pass``, ``fail``, ``warn``, ``skip`` or
``unsupported``, each with a plain explanation, a fix hint and details.

The contract is pygeoapi 0.24.0's (docs/pygeoapi-notes.md §4-§5): fields as
dicts with a ``type``, the API's 13 query keywords, ``features`` even for
hits, ``ProviderItemNotFoundError`` for unknown ids, ``user_msg`` on errors.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from .util import describe_exception

Status = str  # pass | fail | warn | skip | unsupported
CAPABILITIES = (
    'bbox',
    'datetime',
    'properties',
    'sortby',
    'select_properties',
    'skip_geometry',
    'q',
)
UNKNOWN_ID = '__provider-builder-no-such-id__'
NO_MATCH = 'zqxjkvwy'  # a search term no dataset contains

#: The smallest server config pygeoapi accepts at runtime and for OpenAPI (notes §1, §6.2).
SERVER: dict[str, Any] = {
    'server': {
        'bind': {'host': '127.0.0.1', 'port': 5000},
        'url': 'http://127.0.0.1:5000',
        'mimetype': 'application/json; charset=UTF-8',
        'encoding': 'utf-8',
        'languages': ['en-US'],
        'limits': {'default_items': 10, 'max_items': 50},
        'map': {'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'attribution': 'OSM'},
    },
    'logging': {'level': 'ERROR'},
    'metadata': {
        'identification': {
            'title': 'Provider contract check',
            'description': 'pygeoapi Provider Builder harness',
            'keywords': ['test'],
            'terms_of_service': 'https://creativecommons.org/licenses/by/4.0/',
            'url': 'https://example.org',
        },
        'license': {'name': 'CC-BY 4.0', 'url': 'https://creativecommons.org/licenses/by/4.0/'},
        'provider': {'name': 'Provider Builder', 'url': 'https://example.org'},
        'contact': {'name': 'Harness', 'email': 'harness@example.org'},
    },
}


@dataclass
class Check:
    id: str
    level: str
    title: str
    status: Status
    message: str
    hint: str | None = None
    details: Any = None
    ms: float = 0.0


class Skip(Exception):
    """Raised by a check that can't run (no suitable data, missing tool)."""


@dataclass
class Checklist:
    results: list[Check] = field(default_factory=list)
    on_result: Callable[[Check], None] | None = None

    def add(self, check: Check) -> Check:
        self.results.append(check)
        if self.on_result:
            self.on_result(check)
        return check

    def run(
        self, id: str, level: str, title: str, fn: Callable[[], Check | tuple[Any, ...]]
    ) -> Check:
        t0 = time.perf_counter()
        try:
            out = fn()
            check = out if isinstance(out, Check) else Check(id, level, title, *out)
        except Skip as why:
            check = Check(id, level, title, 'skip', str(why))
        except Exception as err:  # noqa: BLE001 - a crashing check is a failed check
            check = failed(id, level, title, err)
        check.ms = round((time.perf_counter() - t0) * 1000, 1)
        return self.add(check)

    def summary(self) -> dict[str, int]:
        counts = dict.fromkeys(('pass', 'fail', 'warn', 'skip', 'unsupported'), 0)
        for check in self.results:
            counts[check.status] += 1
        return counts

    def ok(self) -> bool:
        return not any(c.status == 'fail' for c in self.results)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.results]


def failed(id: str, level: str, title: str, err: BaseException) -> Check:
    from pygeoapi.provider.base import ProviderGenericError

    info = describe_exception(err)
    name = type(err).__name__
    if isinstance(err, ProviderGenericError):
        if getattr(err, 'user_msg', None):
            message = f'It raised {name}: {err.user_msg}'
            hint = None
        else:
            message = f'It raised {name} without a user_msg, so clients only see a generic error.'
            hint = 'Raise it with user_msg="…" (notes §4).'
    else:
        message = f'It raised {name}: {err}'
        hint = 'Raise a pygeoapi Provider*Error with user_msg; other exceptions become HTTP 500.'
    return Check(id, level, title, 'fail', message, hint, info)


def api_query_kwargs(**overrides: Any) -> dict[str, Any]:
    """The 13 keywords pygeoapi's items API passes to ``query()``.

    ``pygeoapi/api/itemtypes.py:557``; docs/pygeoapi-notes.md §5.1.
    """
    kwargs: dict[str, Any] = {
        'offset': 0,
        'limit': 10,
        'resulttype': 'results',
        'bbox': [],
        'datetime_': None,
        'properties': [],
        'sortby': [],
        'select_properties': [],
        'skip_geometry': False,
        'q': None,
        'language': None,
        'filterq': None,
        'crs_transform_spec': None,
    }
    kwargs.update(overrides)
    return kwargs


def ids(collection: Mapping[str, Any]) -> list[str]:
    return [str(f.get('id')) for f in collection.get('features', [])]


# --- L1: features and records -------------------------------------------------------------


class FeatureContract:
    """L1 checks for ``feature`` and ``record`` providers."""

    def __init__(
        self, pdef: dict[str, Any], capabilities: Mapping[str, bool] | None, out: Checklist
    ):
        self.pdef = pdef
        self.capabilities = capabilities
        self.out = out
        self.provider: Any = None
        self.fields: dict[str, Any] = {}
        self.sample: list[dict[str, Any]] = []
        self.matched: int | None = None

    def run(self) -> None:
        self.out.run('L1.load', 'L1', 'pygeoapi loads the provider', self.load)
        if self.provider is None:
            return
        self.out.run('L1.fields', 'L1', 'Fields are dicts with a type', self.check_fields)
        self.out.run('L1.config_fields', 'L1', 'Configured fields exist', self.config_fields)
        self.out.run('L1.query', 'L1', 'query() returns a FeatureCollection', self.query_shape)
        if not self.sample:
            return
        self.out.run('L1.limit', 'L1', 'limit is honoured', self.limit)
        self.out.run('L1.offset', 'L1', 'Pages are disjoint and stable', self.offset)
        self.out.run('L1.hits', 'L1', 'resulttype=hits counts and keeps features', self.hits)
        for capability in CAPABILITIES:
            self.out.run(
                f'L1.{capability}',
                'L1',
                TITLES[capability],
                functools.partial(self.capability, capability),
            )
        self.out.run('L1.get', 'L1', 'get() returns the same item', self.get)
        self.out.run(
            'L1.get_unknown',
            'L1',
            'An unknown id raises ProviderItemNotFoundError',
            self.get_unknown,
        )
        if self.pdef.get('editable'):
            self.out.add(
                Check(
                    'L1.transactions',
                    'L1',
                    'Transactions',
                    'skip',
                    'Transaction checks arrive with code-built providers (Phase 4).',
                )
            )

    # each check returns (status, message[, hint[, details]])

    def load(self) -> tuple[Any, ...]:
        from pygeoapi.plugin import load_plugin
        from pygeoapi.provider.base import BaseProvider

        first = load_plugin('provider', dict(self.pdef))
        if not isinstance(first, BaseProvider):
            return (
                'fail',
                f'{type(first).__name__} is not a pygeoapi BaseProvider.',
                'Subclass pygeoapi.provider.base.BaseProvider.',
            )
        second = load_plugin('provider', dict(self.pdef))
        self.provider = first
        if dict(first.fields or {}) != dict(second.fields or {}):
            return (
                'fail',
                'Loading the provider twice gave different fields.',
                'pygeoapi creates a provider per request; the constructor must be deterministic.',
            )
        cls = f'{type(first).__module__}.{type(first).__qualname__}'
        return ('pass', f'Loaded {cls}.')

    def check_fields(self) -> tuple[Any, ...]:
        fields = self.provider.fields
        try:
            got = self.provider.get_fields()
        except NotImplementedError:
            got = None
        if not fields:
            return (
                'fail',
                'p.fields is empty, so queryables are empty, property filters are '
                'ignored and sortby or properties= return 400 (notes §5.1).',
                'Populate self._fields in the constructor.',
            )
        bad = [k for k, v in fields.items() if not isinstance(v, Mapping) or 'type' not in v]
        if bad:
            return (
                'fail',
                f'These fields are not dicts with a type: {", ".join(map(str, bad[:5]))}. '
                "pygeoapi's /queryables, /schema and OpenAPI crash on them (notes §5.1).",
                "Use {'name': {'type': 'string'}}, not {'name': 'string'}.",
                {k: fields[k] for k in bad[:5]},
            )
        if got is not None and dict(got) != dict(fields):
            return (
                'warn',
                'get_fields() and p.fields differ.',
                'Return self._fields from get_fields().',
            )
        self.fields = dict(fields)
        return ('pass', f'{len(fields)} field(s): {", ".join(list(fields)[:8])}')

    def config_fields(self) -> tuple[Any, ...]:
        problems = []
        for key in ('title_field', 'time_field', 'uri_field'):
            name = self.pdef.get(key)
            if name and name not in self.fields:
                problems.append(f'{key} {name!r} is not a field')
        title = self.pdef.get('title_field')
        if title in self.fields and self.fields[title].get('type') != 'string':
            problems.append(f'title_field {title!r} is not a string field')
        for name in self.pdef.get('properties') or []:
            if name not in self.fields:
                problems.append(f'properties lists {name!r}, which is not a field')
        if problems:
            return ('fail', '; '.join(problems) + '.')
        return ('pass', 'title_field, time_field, uri_field and properties all exist.')

    def query_shape(self) -> tuple[Any, ...]:
        result = self.provider.query(**api_query_kwargs(limit=10))
        problems = []
        if not isinstance(result, Mapping):
            return ('fail', f'query() returned {type(result).__name__}, not a dict.')
        if result.get('type') != 'FeatureCollection':
            problems.append("type isn't FeatureCollection")
        features = result.get('features')
        if not isinstance(features, list):
            return ('fail', 'query() returned no features list.')
        returned, matched = result.get('numberReturned'), result.get('numberMatched')
        if returned is not None and returned != len(features):
            problems.append(f'numberReturned is {returned} but there are {len(features)} features')
        if isinstance(matched, int) and matched < len(features):
            problems.append(f'numberMatched ({matched}) is below the number returned')
        for f in features[:10]:
            if f.get('type') != 'Feature' or not isinstance(f.get('properties'), Mapping):
                problems.append('a feature lacks type: Feature or a properties object')
                break
            if f.get('id') in (None, ''):
                problems.append('a feature has no id, so /items/{id} can never find it')
                break
        if problems:
            return ('fail', '; '.join(problems) + '.', None, {'first': features[:1]})
        self.sample = features
        self.matched = matched if isinstance(matched, int) else None
        if not features:
            return ('warn', 'query() works but returned no features, so most checks are skipped.')
        count = f' of {matched}' if self.matched is not None else ''
        return ('pass', f'{len(features)} feature(s){count}.')

    def limit(self) -> tuple[Any, ...]:
        got = self.provider.query(**api_query_kwargs(limit=2))['features']
        if len(got) > 2:
            return ('fail', f'limit=2 returned {len(got)} features.')
        return ('pass', 'limit=2 returned at most 2 features.')

    def offset(self) -> tuple[Any, ...]:
        first = ids(self.provider.query(**api_query_kwargs(limit=3, offset=0)))
        second = ids(self.provider.query(**api_query_kwargs(limit=3, offset=3)))
        both = ids(self.provider.query(**api_query_kwargs(limit=6, offset=0)))
        if len(both) < 4:
            raise Skip('Fewer than 4 items, so paging can’t be checked.')
        if set(first) & set(second):
            return (
                'fail',
                'offset=3 repeats items from offset=0.',
                None,
                {'first': first, 'second': second},
            )
        if both != first + second:
            return (
                'fail',
                'Two pages of 3 differ from one page of 6: the order is unstable.',
                'Sort by a unique key so offset paging is stable.',
                {'pages': first + second, 'one': both},
            )
        return ('pass', 'offset pages are disjoint and stable.')

    def hits(self) -> tuple[Any, ...]:
        result = self.provider.query(**api_query_kwargs(resulttype='hits'))
        if 'features' not in result:
            return (
                'fail',
                'A hits response has no features key; pygeoapi reads it and fails '
                'with a KeyError (notes §5.1).',
                'Return features: [] with resulttype=hits.',
            )
        matched = result.get('numberMatched')
        if matched is None:
            return ('warn', 'resulttype=hits returned no numberMatched.')
        if self.matched is not None and matched != self.matched:
            return ('fail', f'hits says {matched} but a normal query says {self.matched}.')
        return ('pass', f'numberMatched is {matched}.')

    # --- capabilities

    def capability(self, name: str) -> Check:
        title = TITLES[name]
        declared = None if self.capabilities is None else self.capabilities.get(name)
        try:
            works, detail = getattr(self, f'try_{name}')()
        except Skip:
            raise
        except Exception as err:  # noqa: BLE001
            works, detail = False, f'it raised {type(err).__name__}: {err}'
        if works and declared is False:
            return Check(
                f'L1.{name}',
                'L1',
                title,
                'warn',
                f"Works ({detail}) but the descriptor doesn't declare it.",
                'Set it to true under capabilities.',
            )
        if works:
            return Check(f'L1.{name}', 'L1', title, 'pass', detail)
        if declared:
            return Check(
                f'L1.{name}',
                'L1',
                title,
                'fail',
                f'Declared, but {detail}.',
                'Fix it, or set it to false under capabilities.',
            )
        return Check(f'L1.{name}', 'L1', title, 'unsupported', f'Not supported: {detail}.')

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.provider.query(**api_query_kwargs(**kwargs))['features'])

    def try_bbox(self) -> tuple[bool, str]:
        from shapely.geometry import box, shape

        target = next((f for f in self.sample if f.get('geometry')), None)
        if target is None:
            raise Skip('No item in the sample has a geometry.')
        minx, miny, maxx, maxy = shape(target['geometry']).bounds
        pad = max(maxx - minx, maxy - miny, 1e-4) * 0.01
        bounds = [minx - pad, miny - pad, maxx + pad, maxy + pad]
        got = self.query(bbox=bounds, limit=1000)
        if str(target['id']) not in ids({'features': got}):
            return False, f'a bbox around item {target["id"]} did not return it'
        area = box(*bounds)
        outside = [
            f['id'] for f in got if f.get('geometry') and not shape(f['geometry']).intersects(area)
        ]
        if outside:
            return False, f'{len(outside)} item(s) outside the bbox came back'
        return (
            True,
            f'a bbox around item {target["id"]} returned it and '
            f'{len(got) - 1} other(s), all inside',
        )

    def try_datetime(self) -> tuple[bool, str]:
        from pygeoapi_provider_harness.util import parse_time

        time_field = self.pdef.get('time_field') or getattr(self.provider, 'time_field', None)
        if not time_field:
            raise Skip('No time_field is configured.')
        target = next((f for f in self.sample if f['properties'].get(time_field)), None)
        if target is None:
            raise Skip(f'No item in the sample has a {time_field}.')
        value = str(target['properties'][time_field])
        got = self.query(datetime_=f'{value}/{value}', limit=1000)
        if str(target['id']) not in ids({'features': got}):
            return False, f'datetime={value}/{value} did not return item {target["id"]}'
        moment = parse_time(value)
        off = [f['id'] for f in got if parse_time(f['properties'].get(time_field)) != moment]
        if off:
            return False, f'{len(off)} item(s) outside the interval came back'
        return True, f'datetime={value}/{value} returned {len(got)} item(s), all at that time'

    def pick(self, types: tuple[type, ...]) -> tuple[str, Any] | None:
        for feature in self.sample:
            for name, value in feature['properties'].items():
                if (
                    name in self.fields
                    and isinstance(value, types)
                    and not isinstance(value, bool)
                    and value not in ('', None)
                ):
                    return name, value
        return None

    def try_properties(self) -> tuple[bool, str]:
        found = self.pick((str,)) or self.pick((int, float))
        if found is None:
            raise Skip('No property in the sample has a value to filter on.')
        name, value = found
        got = self.query(properties=[(name, str(value))], limit=1000)
        if not got:
            return False, f'{name}={value!r} returned nothing'
        off = [f['id'] for f in got if str(f['properties'].get(name)) != str(value)]
        if off:
            return False, f'{name}={value!r} returned {len(off)} item(s) with another value'
        return True, f'{name}={value!r} returned {len(got)} matching item(s)'

    def try_sortby(self) -> tuple[bool, str]:
        candidates = [
            n for n, f in self.fields.items() if f.get('type') in ('number', 'integer', 'string')
        ]
        name = next(
            (n for n in candidates if len({str(x['properties'].get(n)) for x in self.sample}) > 1),
            None,
        )
        if name is None:
            raise Skip('No field has two different values in the sample.')
        for order, label in (('-', 'descending'), ('+', 'ascending')):
            got = self.query(sortby=[{'property': name, 'order': order}], limit=20)
            values = [
                f['properties'].get(name) for f in got if f['properties'].get(name) is not None
            ]
            expected = sorted(values, reverse=order == '-')
            if values != expected:
                return False, f'sortby {order}{name} is not {label}'
        return True, f'sortby on {name} sorts both ways'

    def try_select_properties(self) -> tuple[bool, str]:
        name = next(iter(self.fields), None)
        if name is None:
            raise Skip('No fields.')
        got = self.query(select_properties=[name], limit=5)
        allowed = {name, self.pdef.get('id_field')}
        extra = sorted({k for f in got for k in f['properties']} - allowed)
        if extra:
            return False, f'select_properties=[{name}] still returned {", ".join(extra[:5])}'
        return True, f'select_properties=[{name}] returned only {name}'

    def try_skip_geometry(self) -> tuple[bool, str]:
        got = self.query(skip_geometry=True, limit=5)
        if any(f.get('geometry') for f in got):
            return False, 'skip_geometry=True still returned geometries'
        return True, 'skip_geometry=True returned no geometries'

    def try_q(self) -> tuple[bool, str]:
        word = None
        for feature in self.sample:
            for value in feature['properties'].values():
                if isinstance(value, str):
                    word = next((w for w in re.findall(r'[A-Za-z]{4,}', value)), None)
                    if word:
                        break
            if word:
                break
        if word is None:
            raise Skip('No text property in the sample to search for.')
        got = self.query(q=word, limit=50)
        if not got:
            return False, f'q={word} returned nothing'
        nothing = self.query(q=NO_MATCH, limit=50)
        if nothing:
            return False, f'q is ignored: q={NO_MATCH} still returned {len(nothing)} item(s)'
        return (
            True,
            f'q={word} returned {len(got)} item(s), and a term matching nothing returned none',
        )

    # --- items

    def get(self) -> tuple[Any, ...]:
        target = self.sample[0]
        item = self.provider.get(str(target['id']))  # the API passes the URL segment, a string
        if item is None:
            return ('fail', f'get({target["id"]!r}) returned None.')
        if str(item.get('id')) != str(target['id']):
            return ('fail', f'get({target["id"]!r}) returned item {item.get("id")!r}.')
        return ('pass', f'get({target["id"]!r}) round-trips.')

    def get_unknown(self) -> tuple[Any, ...]:
        from pygeoapi.provider.base import ProviderItemNotFoundError

        try:
            item = self.provider.get(UNKNOWN_ID)
        except ProviderItemNotFoundError:
            return ('pass', 'An unknown id raises ProviderItemNotFoundError (HTTP 404).')
        if item is None:
            return (
                'fail',
                'get() returned None for an unknown id, which pygeoapi answers with '
                'HTTP 400 instead of 404 (notes §5.1).',
                'Raise ProviderItemNotFoundError.',
            )
        return ('fail', f'get() returned an item for an id that cannot exist: {item.get("id")!r}.')


TITLES = {
    'bbox': 'bbox filter',
    'datetime': 'datetime filter',
    'properties': 'Property filters',
    'sortby': 'sortby',
    'select_properties': 'select_properties',
    'skip_geometry': 'skip_geometry',
    'q': 'q (free-text search)',
}


# --- L1: EDR ------------------------------------------------------------------------------


def edr_checks(pdef: dict[str, Any], out: Checklist) -> None:
    holder: dict[str, Any] = {}

    def load() -> tuple[Any, ...]:
        from pygeoapi.plugin import load_plugin

        holder['p'] = load_plugin('provider', dict(pdef))
        return ('pass', f'Loaded {type(holder["p"]).__qualname__}.')

    def fields() -> tuple[Any, ...]:
        p = holder['p']
        missing = [
            k
            for k, v in (p.get_fields() or {}).items()
            if not isinstance(v, Mapping) or 'x-ogc-unit' not in v
        ]
        if missing:
            return (
                'fail',
                f'These fields have no x-ogc-unit: {", ".join(map(str, missing[:5]))}. '
                'get_parameters() raises without it, so /collections/{id} returns 500 '
                '(notes §5.2).',
                "Add 'x-ogc-unit' to every field.",
            )
        return ('pass', 'Every field has an x-ogc-unit.')

    def query_types() -> tuple[Any, ...]:
        types = list(holder['p'].get_query_types() or [])
        if not types:
            return (
                'fail',
                'No query types are registered.',
                'Define the query methods in the class body (ADR-0009).',
            )
        if 'items' in types:
            return ('fail', "query types include 'items', which EDR doesn't define.")
        return ('pass', f'Query types: {", ".join(types)}.')

    out.run('L1.load', 'L1', 'pygeoapi loads the provider', load)
    if 'p' not in holder:
        return
    out.run('L1.edr_fields', 'L1', 'EDR fields have units', fields)
    out.run('L1.edr_query_types', 'L1', 'EDR query types', query_types)
    out.add(
        Check(
            'L1.edr_coveragejson',
            'L1',
            'CoverageJSON responses',
            'skip',
            'CoverageJSON validation arrives with the time-series template (Phase 4).',
        )
    )


# --- L2: pygeoapi's API in-process --------------------------------------------------------


def one_collection_config(
    rid: str, resource: Mapping[str, Any], url: str | None = None
) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(json.dumps(SERVER))
    if url:
        config['server']['url'] = url
    config['resources'] = {rid: dict(resource)}
    return config


def api_checks(rid: str, resource: Mapping[str, Any], item_id: Any, out: Checklist) -> None:
    from pygeoapi.api import API, APIRequest, describe_collections, get_collection_schema
    from pygeoapi.api.itemtypes import (
        get_collection_item,
        get_collection_items,
        get_collection_queryables,
    )
    from pygeoapi.openapi import get_oas
    from werkzeug.test import EnvironBuilder

    config = one_collection_config(rid, resource)
    state: dict[str, Any] = {}

    def openapi() -> tuple[Any, ...]:
        oas = get_oas(config, fail_on_invalid_collection=True)
        state['oas'] = oas
        path = f'/collections/{rid}/queryables'
        if path not in oas.get('paths', {}):
            return ('fail', f'The OpenAPI document has no {path}.')
        return ('pass', f'get_oas() with the fail flag lists {path}.')

    out.run('L2.openapi', 'L2', 'OpenAPI generation', openapi)
    if 'oas' not in state:
        return
    api = API(config, state['oas'])

    def call(
        fn: Callable[..., Any], path: str, *args: Any, query: str = 'f=json'
    ) -> tuple[int, Any]:
        request = EnvironBuilder(path=path, query_string=query).get_request()
        _headers, status, content = fn(api, APIRequest.from_flask(request, api.locales), *args)
        return status, content

    def endpoint(
        fn: Callable[..., Any], path: str, *args: Any, query: str = 'f=json'
    ) -> Callable[[], tuple[Any, ...]]:
        def check() -> tuple[Any, ...]:
            status, content = call(fn, path, *args, query=query)
            if status != 200:
                return (
                    'fail',
                    f'GET {path}?{query} returned HTTP {status}.',
                    None,
                    str(content)[:2000],
                )
            return ('pass', f'GET {path}?{query} returned 200.')

        return check

    base = f'/collections/{rid}'
    out.run(
        'L2.collection', 'L2', 'Collection metadata', endpoint(describe_collections, base, rid)
    )
    out.run(
        'L2.items',
        'L2',
        'Items',
        endpoint(get_collection_items, f'{base}/items', rid, query='f=json&limit=2'),
    )
    if item_id is not None:
        out.run(
            'L2.item',
            'L2',
            'One item',
            endpoint(get_collection_item, f'{base}/items/{item_id}', rid, str(item_id)),
        )
    out.run(
        'L2.queryables',
        'L2',
        'Queryables',
        endpoint(get_collection_queryables, f'{base}/queryables', rid),
    )
    out.run('L2.schema', 'L2', 'Schema', endpoint(get_collection_schema, f'{base}/schema', rid))


# --- L3: a live server in this container --------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


@contextmanager
def live_server(config: dict[str, Any], port: int) -> Iterator[str]:
    import yaml
    from pygeoapi.openapi import get_oas

    workdir = tempfile.mkdtemp(prefix='harness-l3-')
    config_path = os.path.join(workdir, 'pygeoapi.config.yml')
    openapi_path = os.path.join(workdir, 'openapi.yml')
    with open(config_path, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(config, fh)
    with open(openapi_path, 'w', encoding='utf-8') as fh:
        json.dump(get_oas(config, fail_on_invalid_collection=True), fh)
    env = {**os.environ, 'PYGEOAPI_CONFIG': config_path, 'PYGEOAPI_OPENAPI': openapi_path}
    proc = subprocess.Popen(  # noqa: S603 - our own interpreter and arguments
        [
            sys.executable,
            '-m',
            'gunicorn',
            '--bind',
            f'127.0.0.1:{port}',
            '--workers',
            '1',
            '--worker-tmp-dir',
            workdir,
            '--timeout',
            '60',
            'pygeoapi.flask_app:APP',
        ],
        cwd=workdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                err = proc.stderr.read().decode(errors='replace')[-2000:] if proc.stderr else ''
                raise RuntimeError(f'gunicorn exited ({proc.returncode}): {err}')
            try:
                with urllib.request.urlopen(base + '/?f=json', timeout=2):  # noqa: S310 - loopback
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if time.monotonic() > deadline:
                    raise RuntimeError('gunicorn did not answer within 30 s') from None
                time.sleep(0.2)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def live_checks(rid: str, resource: Mapping[str, Any], item_id: Any, out: Checklist) -> None:
    import importlib.util

    if importlib.util.find_spec('gunicorn') is None:
        out.add(
            Check(
                'L3.server',
                'L3',
                'A live pygeoapi server',
                'skip',
                "gunicorn isn't installed here; L3 runs in the sandbox image.",
            )
        )
        return
    port = free_port()
    config = one_collection_config(rid, resource, url=f'http://127.0.0.1:{port}')
    paths = [f'/collections/{rid}', f'/collections/{rid}/items']
    if item_id is not None:
        paths.append(f'/collections/{rid}/items/{item_id}')
    try:
        with live_server(config, port) as base:
            out.add(
                Check('L3.server', 'L3', 'A live pygeoapi server', 'pass', 'gunicorn started.')
            )
            for path in paths:
                for fmt, kind in (('json', 'json'), ('html', 'html'), ('jsonld', 'json')):
                    out.run(
                        f'L3.{fmt}:{path}',
                        'L3',
                        f'{path}?f={fmt}',
                        functools.partial(fetch, base, path, fmt, kind),
                    )
    except RuntimeError as err:
        out.add(Check('L3.server', 'L3', 'A live pygeoapi server', 'fail', str(err)))


def fetch(base: str, path: str, fmt: str, kind: str) -> tuple[Any, ...]:
    url = f'{base}{path}?f={fmt}'
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - loopback
            body, ctype = r.read(), r.headers.get('content-type', '')
    except urllib.error.HTTPError as err:
        return ('fail', f'HTTP {err.code}.', None, err.read().decode(errors='replace')[:2000])
    if kind == 'json':
        json.loads(body)
    elif b'<html' not in body[:2000].lower():
        return ('fail', f'Not an HTML page ({ctype}).')
    return ('pass', f'200 {ctype.split(";")[0]}.')


# --- entry point ---------------------------------------------------------------------------


def run_contract(
    rid: str,
    resource: Mapping[str, Any],
    provider_index: int = 0,
    capabilities: Mapping[str, bool] | None = None,
    levels: tuple[str, ...] = ('L1', 'L2', 'L3'),
    on_result: Callable[[Check], None] | None = None,
) -> Checklist:
    out = Checklist(on_result=on_result)
    pdef = dict(resource['providers'][provider_index])
    kind = pdef.get('type')
    item_id = None
    if 'L1' in levels:
        if kind in ('feature', 'record'):
            contract = FeatureContract(pdef, capabilities, out)
            contract.run()
            item_id = contract.sample[0]['id'] if contract.sample else None
        elif kind == 'edr':
            edr_checks(pdef, out)
        else:
            out.add(
                Check(
                    'L1.type',
                    'L1',
                    f'{kind} providers',
                    'skip',
                    f'Contract checks for {kind} providers arrive in Phase 4.',
                )
            )
    if not out.ok():
        for level in ('L2', 'L3'):
            if level in levels:
                out.add(
                    Check(
                        f'{level}.blocked', level, f'{level} checks', 'skip', 'Skipped: L1 failed.'
                    )
                )
        return out
    if 'L2' in levels and kind in ('feature', 'record'):
        api_checks(rid, resource, item_id, out)
    if 'L3' in levels and kind in ('feature', 'record') and out.ok():
        live_checks(rid, resource, item_id, out)
    return out
