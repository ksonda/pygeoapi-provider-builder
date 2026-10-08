"""Form-state builders shared by the unit, contract and integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
# A pristine copy of the dev stack's config: demos edit the real one (README, Quickstart).
BASE_CONFIG = REPO / 'backend' / 'tests' / 'fixtures' / 'base.config.yml'


def csv_form(**overrides: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        'resource_id': 'obs',
        'resource': {
            'title': 'Observations',
            'description': 'Weather station observations',
            'keywords': ['weather', 'observations'],
            'extents': {
                'spatial': {'bbox': [-122, 43, -75, 49]},
                'temporal': {'begin': '2000-10-30T18:24:39Z', 'end': None},
            },
        },
        'providers': [
            {
                'descriptor': 'CSV',
                'values': {
                    'data': '/data/obs.csv',
                    'id_field': 'id',
                    'geometry.x_field': 'long',
                    'geometry.y_field': 'lat',
                    'time_field': 'datetime',
                },
            }
        ],
    }
    form.update(overrides)
    return form


def postgres_form() -> dict[str, Any]:
    return {
        'resource_id': 'waterways',
        'resource': {
            'title': 'Waterways of Burundi',
            'description': 'HOT OSM waterways',
            'keywords': ['water'],
            'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
        },
        'providers': [
            {
                'descriptor': 'PostgreSQL',
                'variant': 'fields',
                'values': {
                    'data.host': 'postgis',
                    'data.dbname': 'test',
                    'data.user': 'postgres',
                    'data.search_path': ['osm', 'public'],
                    'table': 'hotosm_bdi_waterways',
                    'id_field': 'osm_id',
                    'geom_field': 'foo_geom',
                },
                'secrets': {'data.password': {'env': 'PYGEOAPI_WATERWAYS_DB_PASSWORD'}},
            }
        ],
    }


def gpkg_form() -> dict:
    return {
        'resource_id': 'poi-portugal',
        'resource': {
            'title': 'Points of interest in Portugal',
            'description': 'OSM POIs',
            'keywords': ['poi'],
            'extents': {'spatial': {'bbox': [-9.5, 36.9, -6.2, 42.2]}},
        },
        'providers': [
            {
                'descriptor': 'SQLiteGPKG',
                'values': {
                    'data': '/data/poi_portugal.gpkg',
                    'table': 'poi_portugal',
                    'id_field': 'osm_id',
                    'title_field': 'name',
                },
            }
        ],
    }


def geojson_form() -> dict:
    return {
        'resource_id': 'lakes2',
        'resource': {
            'title': {'en': 'Large lakes', 'fr': 'Grands lacs'},
            'description': 'Natural Earth lakes',
            'keywords': ['lakes', 'yes', '2020'],
            'links': [
                {
                    'type': 'text/html',
                    'rel': 'canonical',
                    'title': 'Natural Earth',
                    'href': 'https://www.naturalearthdata.com/',
                }
            ],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
        },
        'providers': [
            {
                'descriptor': 'GeoJSON',
                'values': {
                    'data': '/data/ne_110m_lakes.geojson',
                    'id_field': 'id',
                    'properties': ['name', 'scalerank'],
                    'count': True,
                },
            }
        ],
    }


def postgres_dsn_form() -> dict:
    return {
        'resource_id': 'drains',
        'resource': {
            'title': 'Drains',
            'description': 'A materialized view',
            'keywords': ['water'],
            'extents': {'spatial': {'bbox': [29.0, -4.5, 30.9, -2.3]}},
        },
        'providers': [
            {
                'descriptor': 'PostgreSQL',
                'variant': 'dsn',
                'values': {
                    'options.search_path': ['osm'],
                    'table': 'hotosm_bdi_drains',
                    'id_field': 'osm_id',
                    'geom_field': 'foo_geom',
                    'options.connect_timeout': '10',
                },
                'secrets': {'data': {'env': 'PYGEOAPI_DRAINS_DB_DSN'}},
            }
        ],
    }


CASES = {
    'csv': csv_form,
    'geojson': geojson_form,
    'sqlitegpkg': gpkg_form,
    'postgresql-fields': postgres_form,
    'postgresql-dsn': postgres_dsn_form,
}
