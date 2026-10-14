"""Specs for the ``timeseries-edr`` tests: the mock API's CO-OPS look-alike."""

from __future__ import annotations

import copy
from typing import Any

from pygeoapi_spec_providers.timeseries import TimeSeriesEDRProvider

#: The API's 17 keyword arguments (docs/pygeoapi-notes.md §5.2).
EDR_KWARGS: dict[str, Any] = {
    'query_type': None,
    'instance': None,
    'format_': 'json',
    'datetime_': None,
    'select_properties': [],
    'wkt': None,
    'z': None,
    'bbox': None,
    'within': None,
    'within_units': None,
    'corridor_width': None,
    'width_units': None,
    'corridor_height': None,
    'height_units': None,
    'limit': 10,
    'location_id': None,
    'crs_transform_spec': None,
}

PARAMETERS = [
    {
        'id': 'water_level',
        'label': 'Water level',
        'unit': {'symbol': 'm', 'label': 'metre'},
        'unit_uri': 'http://qudt.org/vocab/unit/M',
    },
    {
        'id': 'air_temperature',
        'label': 'Air temperature',
        'unit': {'symbol': 'Cel', 'label': 'degree Celsius'},
    },
]


def coops(base: str, **extra: Any) -> dict[str, Any]:
    """The CO-OPS stations and datagetter, as NOAA's real API shapes them."""
    spec: dict[str, Any] = {
        'spec_version': 1,
        'template': 'timeseries-edr',
        'id': 'coops-water-levels',
        'title': 'CO-OPS water levels',
        'version': '1.0.0',
        'locations': {
            'from': 'http',
            'http': {
                'source': {
                    'url': f'{base}/coops/mdapi/prod/webapi/stations.json',
                    'query': {'type': 'waterlevels'},
                    'retries': {'max': 0},
                },
                'records': '$.stations',
                'item_id': {'from': 'id'},
                'geometry': {'type': 'point', 'x': 'lng', 'y': 'lat'},
                'properties': [
                    {'name': 'station_id', 'type': 'string', 'from': 'id'},
                    {'name': 'name', 'type': 'string'},
                    {'name': 'state', 'type': 'string'},
                ],
                'title_field': 'name',
            },
        },
        'parameters': copy.deepcopy(PARAMETERS),
        'observations': {
            'from': 'http',
            'http': {
                'url': f'{base}/coops/api/prod/datagetter',
                'query': {
                    'product': '{parameter}',
                    'station': '{location_id}',
                    'begin_date': '{start:%Y%m%d %H:%M}',
                    'end_date': '{end:%Y%m%d %H:%M}',
                    'datum': 'MLLW',
                    'units': 'metric',
                    'time_zone': 'gmt',
                    'format': 'json',
                },
                'retries': {'max': 0},
                'records': '$.data',
                'error_path': '$.error',
                'time': {'from': 't', 'format': '%Y-%m-%d %H:%M'},
                'value': {'from': 'v'},
            },
            'default_window': 'P1D',
            'max_window': 'P31D',
        },
        'position': {'snap_radius_km': 5},
    }
    spec.update(extra)
    return spec


def provider(spec: dict[str, Any] | str, **config: Any) -> TimeSeriesEDRProvider:
    return TimeSeriesEDRProvider(
        {
            'name': 'pygeoapi_spec_providers.timeseries.TimeSeriesEDRProvider',
            'type': 'edr',
            'data': spec,
            **config,
        }
    )


def call(p: TimeSeriesEDRProvider, query_type: str, **kwargs: Any) -> dict[str, Any]:
    """Like pygeoapi's EDR API: ``query()`` with every keyword."""
    return dict(p.query(**{**EDR_KWARGS, 'query_type': query_type, **kwargs}))
