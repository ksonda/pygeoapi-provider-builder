"""Spec builders for the mock API (docker/mock-api/mock_api.py)."""

from __future__ import annotations

import copy
from typing import Any

from pygeoapi_spec_providers.http import HTTPFeatureProvider

PROPERTIES = [
    {'name': 'well_id', 'type': 'string'},
    {'name': 'well_name', 'type': 'string'},
    {'name': 'county', 'type': 'string', 'from': 'county_name'},
    {'name': 'depth_ft', 'type': 'number', 'from': 'depth'},
    {'name': 'status', 'type': 'string'},
    {'name': 'last_measured', 'type': 'datetime', 'from': 'lastMeasurementDate'},
]


def wells(base: str, path: str = '/v2/wells', **extra: Any) -> dict[str, Any]:
    """The offset-paginated wells endpoint, with no filters pushed down."""
    spec: dict[str, Any] = {
        'spec_version': 1,
        'template': 'http-features',
        'id': 'mock-wells',
        'title': 'Mock wells',
        'version': '1.0.0',
        'source': {'url': base + path, 'retries': {'max': 0}},
        'records': '$.data.items',
        'item_id': {'from': 'well_id'},
        'geometry': {'type': 'point', 'x': 'longitude', 'y': 'latitude'},
        'properties': copy.deepcopy(PROPERTIES),
        'time_field': 'last_measured',
        'title_field': 'well_name',
        'pagination': {
            'style': 'offset',
            'offset_param': 'offset',
            'limit_param': 'limit',
            'max_page_size': 100,
            'total': '$.meta.total',
        },
    }
    spec.update(extra)
    return spec


def provider(spec: dict[str, Any] | str, **config: Any) -> HTTPFeatureProvider:
    return HTTPFeatureProvider(
        {
            'name': 'pygeoapi_spec_providers.http.HTTPFeatureProvider',
            'type': 'feature',
            'data': spec,
            **config,
        }
    )


def ids(result: dict[str, Any]) -> list[Any]:
    return [f['id'] for f in result['features']]


def all_ids() -> list[str]:
    return [f'W{i:04d}' for i in range(1, 121)]
