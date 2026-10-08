"""``http-features``: an HTTP JSON API as an OGC API - Features collection.

Config::

    providers:
      - type: feature
        name: pygeoapi_spec_providers.http.HTTPFeatureProvider
        data: /specs/state-wells/1.2.0/spec.yaml    # or pkg:<module>/<resource> (ADR-0005)

The query pipeline is PLAN.md §7.3: plan pushdown, fetch pages, extract
records, map them to features, apply what the source couldn't, slice, and
always return ``features``, even for hits (notes §5.1).
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pygeoapi.crs import crs_transform, get_crs
from pygeoapi.provider.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderQueryError,
    ProviderTypeError,
)

from . import formatter
from .client import SourceClient, SourceError, status_error
from .jsonpaths import PathError, first
from .local import LocalFilters, parse_interval, shape_output, sort_features, split_terms
from .mapping import Mapper, check_error, extract_records, to_iso
from .spec import BBOX_TOKENS, Spec, SpecError, load_spec

USER_MESSAGES = {
    'timeout': 'The data source timed out.',
    'connect': "The data source couldn't be reached.",
    'egress': "The data source isn't allowed by this server's egress rules.",
    'too_large': 'The data source sent a response larger than allowed.',
    'not_json': "The data source didn't return JSON.",
    'api_error': 'The data source reported an error.',
}


def user_message(err: SourceError) -> str:
    if err.kind == 'status':
        if err.status in (401, 403):
            return 'The data source rejected the credentials.'
        if err.status == 429:
            return 'The data source is limiting requests; try again later.'
        return f'The data source returned HTTP {err.status}.'
    return USER_MESSAGES.get(err.kind, 'The data source failed.')


@contextmanager
def provider_errors() -> Iterator[None]:
    """Turn engine failures into pygeoapi errors with a ``user_msg`` (notes §4)."""
    try:
        yield
    except SourceError as err:
        cls = ProviderQueryError
        if err.kind in ('egress', 'connect', 'timeout') or err.status in (401, 403):
            cls = ProviderConnectionError
        raise cls(str(err), user_msg=user_message(err)) from err
    except (PathError, formatter.FormatError) as err:
        raise ProviderQueryError(str(err), user_msg='The provider spec has an error.') from err


def format_moment(moment: datetime, fmt: str) -> str:
    moment = moment.astimezone(UTC)
    if fmt == 'iso8601':
        return to_iso(moment)
    if fmt == 'date':
        return moment.date().isoformat()
    if fmt == 'epoch_s':
        return str(int(moment.timestamp()))
    if fmt == 'epoch_ms':
        return str(int(moment.timestamp() * 1000))
    return moment.strftime(fmt)


@dataclass
class Page:
    records: list[Mapping[str, Any]]
    doc: Any
    url: httpx.URL
    headers: httpx.Headers


@dataclass
class Plan:
    params: dict[str, str]
    local: LocalFilters


def link_header_next(value: str | None) -> str | None:
    """The ``rel="next"`` target of an RFC 8288 Link header."""
    if not value:
        return None
    for part in value.split(','):
        url, _, params = part.partition(';')
        url = url.strip()
        rels = [
            p.split('=', 1)[1].strip().strip('"')
            for p in params.split(';')
            if '=' in p and p.split('=', 1)[0].strip().lower() == 'rel'
        ]
        if url.startswith('<') and url.endswith('>') and any('next' in r.split() for r in rels):
            return url[1:-1]
    return None


class PageStream:
    """Pages from the source, starting at record ``skip``.

    Offset and page pagination skip on the server; the other styles drop the
    first ``skip`` records as they arrive. ``truncated`` is set when the
    ``max_pages`` limit stopped the stream early.
    """

    def __init__(self, engine: Engine, params: dict[str, str], *, skip: int, page_size: int):
        self.engine = engine
        self.params = params
        self.skip = skip
        self.page_size = page_size
        self.truncated = False

    def __iter__(self) -> Iterator[Page]:
        e, params = self.engine, self.params
        style = e.pagination['style']
        url = e.spec.source['url']
        max_pages = int(e.limits['max_pages'])
        drop = self.skip
        if style == 'none':
            page = e.fetch(url, params)
            yield self._dropped(page, drop)
            return
        if style == 'offset':
            offset = self.skip
            for _ in range(max_pages):
                page = e.fetch(
                    url,
                    {
                        **params,
                        e.pagination['offset_param']: str(offset),
                        e.pagination['limit_param']: str(self.page_size),
                    },
                )
                yield page
                got, total = len(page.records), e.total(page.doc)
                if (
                    got == 0
                    or got < self.page_size
                    or (total is not None and offset + got >= total)
                ):
                    return
                offset += got
            self.truncated = True
            return
        if style == 'page':
            cfg = e.pagination['page']
            number = int(cfg['page_start'])
            size_param = cfg.get('size_param')
            if size_param:
                number += self.skip // self.page_size
                drop = self.skip % self.page_size
            for _ in range(max_pages):
                query = {**params, cfg['page_param']: str(number)}
                if size_param:
                    query[size_param] = str(self.page_size)
                page = e.fetch(url, query)
                got = len(page.records)
                page, drop = self._dropped(page, drop), max(0, drop - got)
                yield page
                if got == 0 or (size_param and got < self.page_size):
                    return
                number += 1
            self.truncated = True
            return
        if style == 'next_link':
            nxt_ref = e.pagination['next_link']['next']
            target: httpx.URL | str = url
            query, static = params, True
            for _ in range(max_pages):
                page = e.fetch(target, query, static=static)
                got = len(page.records)
                yield self._dropped(page, drop)
                drop = max(0, drop - got)
                if nxt_ref == 'header:Link':
                    nxt = link_header_next(page.headers.get('link'))
                else:
                    found = first(nxt_ref, page.doc)
                    nxt = found if isinstance(found, str) and found else None
                if not nxt:
                    return
                target, query, static = page.url.join(nxt), {}, False
            self.truncated = True
            return
        if style == 'cursor':
            cfg = e.pagination['cursor']
            cursor: Any = None
            seen: set[str] = set()
            for _ in range(max_pages):
                query = dict(params)
                if cursor is not None:
                    query[cfg['cursor_param']] = str(cursor)
                page = e.fetch(url, query)
                got = len(page.records)
                yield self._dropped(page, drop)
                drop = max(0, drop - got)
                cursor = first(cfg['cursor_path'], page.doc)
                if cursor in (None, '', False) or str(cursor) in seen:
                    return
                seen.add(str(cursor))
            self.truncated = True
            return
        raise ProviderTypeError(user_msg=f'Unknown pagination style {style!r}')

    @staticmethod
    def _dropped(page: Page, drop: int) -> Page:
        if drop:
            page.records = page.records[drop:]
        return page


class Engine:
    """Fetching and mapping for one spec. One per process: pygeoapi creates a
    provider per request, so the HTTP client, rate limiter and cache live here."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.client = SourceClient.for_spec(spec)
        self.mapper = Mapper(spec)
        self.pagination: dict[str, Any] = spec.data['pagination']
        self.filters: dict[str, Any] = spec.data['filters']
        self.limits: dict[str, Any] = spec.data['limits']
        self.time_field: str | None = spec.data.get('time_field')

    def fetch(
        self, url: str | httpx.URL, params: Mapping[str, str], *, static: bool = True
    ) -> Page:
        response = self.client.get(url, params, static=static)
        if not 200 <= response.status < 300:
            raise status_error(response)
        doc = response.json()
        check_error(self.spec, doc)
        return Page(extract_records(self.spec, doc), doc, response.url, response.headers)

    def total(self, doc: Any) -> int | None:
        path = self.pagination.get('total')
        if not path:
            return None
        value = first(path, doc)
        try:
            return int(value) if value is not None and not isinstance(value, bool) else None
        except (TypeError, ValueError):
            return None

    def plan(
        self,
        bbox: Sequence[float] | None,
        datetime_: str | None,
        properties: Sequence[tuple[str, str]],
        sortby: Sequence[dict[str, str]],
        q: str | None,
    ) -> Plan:
        f, params = self.filters, {}
        local = LocalFilters(time_field=self.time_field)
        if bbox:
            if 'bbox' in f:
                b = list(bbox)
                corners = (b[0], b[1], b[3], b[4]) if len(b) == 6 else tuple(b[:4])
                template = formatter.parse(f['bbox']['format'], BBOX_TOKENS)
                params[f['bbox']['param']] = formatter.render(
                    template, dict(zip(BBOX_TOKENS, corners, strict=True))
                )
            else:
                local.bbox = bbox
        if datetime_ and self.time_field:
            start, end = parse_interval(datetime_)
            if 'datetime' in f:
                cfg = f['datetime']
                fmt = cfg.get('format', 'iso8601')
                if 'param' in cfg:
                    text = [format_moment(m, fmt) if m else '..' for m in (start, end)]
                    params[cfg['param']] = text[0] if start == end else '/'.join(text)
                else:
                    if start:
                        params[cfg['start_param']] = format_moment(start, fmt)
                    if end:
                        params[cfg['end_param']] = format_moment(end, fmt)
            else:
                local.interval = (start, end)
        for name, value in properties or []:
            upstream = f.get('properties', {}).get(name)
            if upstream:
                params[upstream] = str(value)
            else:
                local.properties.append((name, str(value)))
        if sortby:
            cfg = f.get('sortby')
            if cfg and len(sortby) == 1 and sortby[0]['property'] in cfg['properties']:
                name = sortby[0]['property']
                prop = self.spec.get_property(name) or {}
                ref = prop.get('from', name)
                upstream_name = name if ref.startswith('$') else ref
                order = 'desc' if sortby[0].get('order') == '-' else 'asc'
                order = cfg.get('orders', {}).get(order, order)
                template = formatter.parse(
                    cfg.get('format', '{property}:{order}'), ('property', 'order')
                )
                params[cfg['param']] = formatter.render(
                    template, {'property': upstream_name, 'order': order}
                )
            else:
                local.sortby = list(sortby)
        if q:
            if 'q' in f:
                params[f['q']['param']] = q
            else:
                local.terms = split_terms(q)
        return Plan(params, local)

    def query(
        self, *, offset: int, limit: int, hits: bool, count: bool, plan: Plan
    ) -> tuple[list[dict[str, Any]], int | None]:
        cap = int(self.pagination['max_page_size'])
        max_scan = int(self.limits['max_records_scanned'])
        has_total = bool(self.pagination.get('total'))
        if not plan.local.any():
            if hits and has_total:
                for page in PageStream(self, plan.params, skip=0, page_size=1):
                    return [], self.total(page.doc)
            if not hits:
                features: list[dict[str, Any]] = []
                matched, scanned = None, 0
                stream = PageStream(
                    self, plan.params, skip=offset, page_size=max(1, min(cap, limit))
                )
                for page in stream:
                    if matched is None and has_total and count:
                        matched = self.total(page.doc)
                    for record in page.records:
                        scanned += 1
                        feature = self.mapper.feature(record)
                        if feature is not None:
                            features.append(feature)
                            if len(features) >= limit:
                                break
                    if len(features) >= limit or scanned >= max_scan:
                        break
                return features[:limit], matched
        # Scan within the limits, apply what the source couldn't, then slice.
        matching: list[dict[str, Any]] = []
        scanned, complete = 0, True
        stream = PageStream(self, plan.params, skip=0, page_size=cap)
        for page in stream:
            for record in page.records:
                scanned += 1
                feature = self.mapper.feature(record)
                if feature is not None and plan.local.matches(feature):
                    matching.append(feature)
            if scanned >= max_scan:
                complete = False
                break
        complete = complete and not stream.truncated
        if plan.local.sortby:
            sort_features(matching, plan.local.sortby)
        matched = len(matching) if complete else None
        return ([] if hits else matching[offset : offset + limit]), matched

    def get(self, identifier: Any) -> dict[str, Any]:
        not_found = ProviderItemNotFoundError(user_msg='item not found')
        item = self.spec.data.get('item')
        if item:
            url = formatter.render_url(formatter.parse(item['url'], ('id',)), {'id': identifier})
            response = self.client.get(url)
            if response.status in (404, 410):
                raise not_found
            if not 200 <= response.status < 300:
                raise status_error(response)
            doc = response.json()
            check_error(self.spec, doc)
            record = first(item['record'], doc) if item.get('record') else doc
            feature = self.mapper.feature(record) if isinstance(record, Mapping) else None
            if feature is None:
                raise not_found
            return feature
        scanned = 0
        cap, max_scan = (
            int(self.pagination['max_page_size']),
            int(self.limits['max_records_scanned']),
        )
        for page in PageStream(self, {}, skip=0, page_size=cap):
            for record in page.records:
                scanned += 1
                feature = self.mapper.feature(record)
                if feature is not None and str(feature['id']) == str(identifier):
                    return feature
            if scanned >= max_scan:
                break
        raise not_found


@functools.lru_cache(maxsize=32)
def engine_for(spec: Spec) -> Engine:
    return Engine(spec)


class HTTPFeatureProvider(BaseProvider):  # type: ignore[misc]  # pygeoapi is untyped
    """A feature provider driven by an ``http-features`` spec (``data`` points at it)."""

    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        try:
            self.spec = load_spec(provider_def['data'])
        except SpecError as err:
            raise ProviderConnectionError(
                str(err), user_msg='The provider spec is invalid.'
            ) from err
        if self.spec.data['template'] != 'http-features':
            raise ProviderTypeError(
                user_msg=f'{self.spec.data["template"]} specs need another provider class'
            )
        self.engine = engine_for(self.spec)
        self._fields = (
            self.spec.fields()
        )  # pygeoapi reads p.fields, which returns _fields (notes §3.2)
        self.id_field = provider_def.get('id_field') or self.spec.id_property
        self.title_field = provider_def.get('title_field') or self.spec.data.get('title_field')
        self.time_field = provider_def.get('time_field') or self.spec.data.get('time_field')
        crs = self.spec.data['geometry'].get('crs')
        if crs and 'storage_crs' not in provider_def:
            self.storage_crs = get_crs(crs)

    def get_fields(self) -> dict[str, dict[str, str]]:
        return self._fields

    @crs_transform  # type: ignore[untyped-decorator]
    def query(
        self,
        offset: int = 0,
        limit: int = 10,
        resulttype: str = 'results',
        bbox: Sequence[float] | None = None,
        datetime_: str | None = None,
        properties: Sequence[tuple[str, str]] | None = None,
        sortby: Sequence[dict[str, str]] | None = None,
        select_properties: Sequence[str] | None = None,
        skip_geometry: bool = False,
        q: str | None = None,
        filterq: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if filterq is not None:
            raise ProviderInvalidQueryError(
                user_msg='This collection does not support CQL filters.'
            )
        with provider_errors():
            plan = self.engine.plan(bbox, datetime_, properties or [], sortby or [], q)
            features, matched = self.engine.query(
                offset=max(0, int(offset)),
                limit=max(0, int(limit)),
                hits=resulttype == 'hits',
                count=self.count,
                plan=plan,
            )
        features = [shape_output(f, select_properties or [], skip_geometry) for f in features]
        result: dict[str, Any] = {
            'type': 'FeatureCollection',
            'features': features,
            'numberReturned': len(features),
        }
        if matched is not None and (self.count or resulttype == 'hits'):
            result['numberMatched'] = matched
        return result

    @crs_transform  # type: ignore[untyped-decorator]
    def get(self, identifier: Any, **kwargs: Any) -> dict[str, Any]:
        with provider_errors():
            return self.engine.get(identifier)

    def __repr__(self) -> str:
        spec = getattr(self, 'spec', None)
        return f'<HTTPFeatureProvider> {spec.id if spec else "(no spec)"}'
