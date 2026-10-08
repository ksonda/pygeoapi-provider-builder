"""Suggestions from sampled JSON, against each shape the mock API serves."""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from provider_builder.suggest import suggest
from pygeoapi_spec_providers.spec import validate

REPO = Path(__file__).resolve().parents[2]


@cache
def mock_client() -> Any:
    spec = importlib.util.spec_from_file_location(
        'mock_api_s', REPO / 'docker/mock-api/mock_api.py'
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app.test_client()


def sampled(path: str) -> dict[str, Any]:
    response = mock_client().get(path)
    return suggest(response.get_json(), dict(response.headers), f'http://mockapi:8000{path}')


def test_offset_paging_with_a_total() -> None:
    s = sampled('/v2/wells?offset=0&limit=25')
    assert s['records'] == '$.data.items'
    assert s['record_count'] == 25
    assert s['item_id']['from'] == 'well_id'
    assert s['geometry'] == {'type': 'geojson', 'field': 'location'}
    assert s['time_field'] == 'last_measurement_date'
    assert s['title_field'] == 'well_name'
    pagination = s['pagination']
    assert (pagination['style'], pagination['offset_param'], pagination['limit_param']) == (
        'offset',
        'offset',
        'limit',
    )
    assert pagination['total'] == '$.meta.total'
    epoch = next(p for p in s['properties'] if p['name'] == 'measured_epoch')
    assert epoch == {'name': 'measured_epoch', 'type': 'datetime', 'format': 'epoch_s'}


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        (
            '/linked/wells',
            {'style': 'next_link', 'next_link': {'next': "$.links[?@.rel=='next'].href"}},
        ),
        ('/header-linked/wells', {'style': 'next_link', 'next_link': {'next': 'header:Link'}}),
        (
            '/cursor/wells',
            {
                'style': 'cursor',
                'cursor': {'cursor_param': 'cursor', 'cursor_path': '$.next_cursor'},
            },
        ),
        ('/paged/wells?page=1&per_page=25', {'style': 'page'}),
    ],
)
def test_paging_hints(path: str, expected: dict[str, Any]) -> None:
    pagination = sampled(path)['pagination']
    for key, value in expected.items():
        assert pagination[key] == value, pagination


def test_nested_esri_style_records() -> None:
    s = sampled('/esri/wells?token=test-key')
    assert s['records'] == '$.features'
    assert s['item_id']['from'] == '$.attributes.OBJECTID'
    assert s['geometry'] == {'type': 'point', 'x': '$.geometry.x', 'y': '$.geometry.y'}
    names = {p['name']: p for p in s['properties']}
    assert names['attributes_name']['from'] == '$.attributes.NAME'


def test_a_top_level_array() -> None:
    s = sampled('/all/wells')
    assert s['records'] == '$'
    assert s['record_count'] == 120


def test_no_records() -> None:
    assert suggest({'status': 'ok'})['records'] is None


@pytest.mark.parametrize(
    'path',
    [
        '/v2/wells?offset=0&limit=25',
        '/linked/wells',
        '/cursor/wells',
        '/esri/wells?token=test-key',
        '/all/wells',
    ],
)
def test_suggestions_make_a_valid_spec(path: str) -> None:
    s = sampled(path)
    pagination = {k: v for k, v in s['pagination'].items() if k != 'hints'}
    spec = {
        'spec_version': 1,
        'template': 'http-features',
        'id': 'x',
        'title': 'X',
        'source': {'url': f'http://mockapi:8000{path.split("?")[0]}'},
        'records': s['records'],
        'item_id': {'from': s['item_id']['from']},
        'geometry': s['geometry'],
        'properties': s['properties'],
        'pagination': pagination,
    }
    for key in ('time_field', 'title_field'):
        if s[key]:
            spec[key] = s[key]
    assert validate(spec) == []
