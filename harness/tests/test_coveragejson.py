"""The vendored CoverageJSON schema and the structural checks around it."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pygeoapi_provider_harness import coveragejson

REPO = Path(__file__).resolve().parents[2]
VENDORED = REPO / 'vendor' / 'CoverageJSON' / 'standard' / 'schema' / 'build' / '2020-12'

POINT_SERIES = {
    'type': 'CoverageCollection',
    'domainType': 'PointSeries',
    'parameters': {
        'water_level': {
            'type': 'Parameter',
            'observedProperty': {'label': {'en': 'Water level'}},
            'unit': {'symbol': 'm'},
        }
    },
    'coverages': [
        {
            'type': 'Coverage',
            'domain': {
                'type': 'Domain',
                'domainType': 'PointSeries',
                'axes': {
                    'x': {'values': [-122.4659]},
                    'y': {'values': [37.8063]},
                    't': {'values': ['2026-10-01T00:00:00Z', '2026-10-01T00:06:00Z']},
                },
                'referencing': [
                    {
                        'coordinates': ['x', 'y'],
                        'system': {
                            'type': 'GeographicCRS',
                            'id': 'http://www.opengis.net/def/crs/OGC/1.3/CRS84',
                        },
                    },
                    {
                        'coordinates': ['t'],
                        'system': {'type': 'TemporalRS', 'calendar': 'Gregorian'},
                    },
                ],
            },
            'ranges': {
                'water_level': {
                    'type': 'NdArray',
                    'dataType': 'float',
                    'axisNames': ['t'],
                    'shape': [2],
                    'values': [0.29, 0.272],
                }
            },
        }
    ],
}


def test_the_copy_matches_the_pinned_source() -> None:
    if not VENDORED.is_dir():
        pytest.skip('vendor/CoverageJSON is missing (make vendor)')
    ours = Path(coveragejson.__file__).with_name('schemas') / 'coveragejson.json'
    assert ours.read_bytes() == (VENDORED / 'coveragejson.json').read_bytes()


def test_a_point_series_collection_is_valid() -> None:
    assert coveragejson.problems(POINT_SERIES) == []
    assert coveragejson.range_keys(POINT_SERIES) == {'water_level'}


def test_schema_and_structure_problems_are_reported() -> None:
    empty = copy.deepcopy(POINT_SERIES)
    empty['coverages'][0]['ranges']['water_level']['values'] = []
    assert any('values' in p for p in coveragejson.problems(empty))

    short = copy.deepcopy(POINT_SERIES)
    short['coverages'][0]['ranges']['water_level']['shape'] = [3]
    found = coveragejson.problems(short)
    assert any('axis t has 2 values, shape says 3' in p for p in found)

    orphan = copy.deepcopy(POINT_SERIES)
    orphan['coverages'][0]['ranges']['salinity'] = orphan['coverages'][0]['ranges'].pop(
        'water_level'
    )
    assert any('no parameter describes this range' in p for p in coveragejson.problems(orphan))

    no_label = copy.deepcopy(POINT_SERIES)
    del no_label['parameters']['water_level']['observedProperty']['label']
    assert coveragejson.problems(no_label)
