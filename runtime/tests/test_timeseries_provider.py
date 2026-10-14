"""``timeseries-edr`` against the mock API's CO-OPS look-alike, and on SQLite."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pygeoapi.provider.base import (
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderNoDataError,
)
from pygeoapi_provider_harness import coveragejson
from pygeoapi_spec_providers.spec import validate
from pygeoapi_spec_providers.timeseries import parse_duration
from shapely import wkt
from tsfixtures import call, coops, provider

DAY = '2026-10-01T00:00:00Z/2026-10-02T00:00:00Z'


def test_the_spec_validates_and_fields_carry_units(mock_api: str) -> None:
    assert validate(coops(mock_api)) == []
    p = provider(coops(mock_api))
    assert p.get_fields()['water_level'] == {
        'type': 'number',
        'title': 'Water level',
        'x-ogc-unit': 'm',
    }
    assert p.get_query_types() == ['locations', 'position', 'area', 'cube']
    parameters = p.get_parameters()  # what /collections/{id} calls (notes §5.2)
    assert parameters['water_level']['unit']['symbol']['value'] == 'm'


def test_locations_lists_the_stations(mock_api: str) -> None:
    p = provider(coops(mock_api))
    fc = call(p, 'locations')
    assert fc['type'] == 'FeatureCollection'
    assert [f['id'] for f in fc['features']][:2] == ['9414290', '8518750']
    west = call(p, 'locations', bbox=[-125, 30, -115, 40])
    assert [f['properties']['name'] for f in west['features']] == ['San Francisco']


def test_one_location_is_valid_coveragejson(mock_api: str) -> None:
    p = provider(coops(mock_api))
    cc = call(p, 'locations', location_id='9414290', datetime_=DAY)
    assert coveragejson.problems(cc) == []
    assert cc['type'] == 'CoverageCollection'
    assert set(cc['parameters']) == {'water_level', 'air_temperature'}
    water = next(c for c in cc['coverages'] if 'water_level' in c['ranges'])
    times = water['domain']['axes']['t']['values']
    assert times[0] == '2026-10-01T00:00:00Z' and len(times) == 241  # every 6 minutes
    assert water['ranges']['water_level']['shape'] == [241]
    assert water['domain']['axes']['x']['values'] == [-122.4659]
    unit = cc['parameters']['water_level']['unit']
    assert unit == {'symbol': 'm', 'label': {'en': 'metre'}, 'id': 'http://qudt.org/vocab/unit/M'}


def test_parameter_names_narrow_the_response(mock_api: str) -> None:
    p = provider(coops(mock_api))
    cc = call(
        p, 'locations', location_id='8518750', datetime_=DAY, select_properties=['air_temperature']
    )
    assert coveragejson.range_keys(cc) == {'air_temperature'}
    assert list(cc['parameters']) == ['air_temperature']


def test_a_station_without_a_product_is_left_out(mock_api: str) -> None:
    p = provider(coops(mock_api))
    cc = call(p, 'locations', location_id='1612340', datetime_=DAY)  # Honolulu: no air temperature
    assert coveragejson.range_keys(cc) == {'water_level'}
    with pytest.raises(ProviderNoDataError):
        call(
            p,
            'locations',
            location_id='1612340',
            datetime_=DAY,
            select_properties=['air_temperature'],
        )


def test_position_snaps_to_the_nearest_station(mock_api: str) -> None:
    p = provider(coops(mock_api))
    cc = call(p, 'position', wkt=wkt.loads('POINT(-122.47 37.80)'), datetime_=DAY)
    assert coveragejson.problems(cc) == []
    assert {c['id'].split('-')[0] for c in cc['coverages']} == {'9414290'}
    with pytest.raises(ProviderNoDataError):
        call(p, 'position', wkt=wkt.loads('POINT(-100 40)'), datetime_=DAY)


def test_area_and_cube_select_stations_inside(mock_api: str) -> None:
    p = provider(coops(mock_api))
    east = wkt.loads('POLYGON((-80 35, -65 35, -65 45, -80 45, -80 35))')
    cc = call(p, 'area', wkt=east, datetime_=DAY, select_properties=['water_level'])
    assert coveragejson.problems(cc) == []
    assert {c['id'] for c in cc['coverages']} == {'8518750-water_level', '8443970-water_level'}
    cube = call(p, 'cube', bbox=[-125, 40, -115, 50], datetime_=DAY)
    assert {c['id'].split('-')[0] for c in cube['coverages']} == {'9447130'}  # Seattle
    one = call(p, 'cube', bbox=[-180, -90, 180, 90], datetime_=DAY, limit=1)
    assert {c['id'].split('-')[0] for c in one['coverages']} == {'9414290'}


def test_windows(mock_api: str) -> None:
    p = provider(coops(mock_api))
    engine = p.engine
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    assert engine.window(None, now) == (now - timedelta(days=1), now)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    assert engine.window('2026-10-01T00:00:00Z/..', now) == (start, start + timedelta(days=1))
    with pytest.raises(ProviderInvalidQueryError) as err:
        call(
            p,
            'locations',
            location_id='9414290',
            datetime_='2026-01-01T00:00:00Z/2026-03-01T00:00:00Z',
        )
    assert 'P31D' in err.value.user_msg
    assert parse_duration('PT6H') == timedelta(hours=6)
    with pytest.raises(ValueError):
        parse_duration('P1M')


def test_unknown_location_and_query_types(mock_api: str) -> None:
    p = provider(coops(mock_api, query_types=['locations']))
    assert p.get_query_types() == ['locations']
    with pytest.raises(ProviderItemNotFoundError):
        call(p, 'locations', location_id='0000000', datetime_=DAY)
    with pytest.raises(ProviderInvalidQueryError):
        call(p, 'cube', bbox=[-180, -90, 180, 90])


def test_spec_problems(mock_api: str) -> None:
    def problems(spec: dict[str, Any]) -> list[str]:
        return [f'{p.pointer} {p.message}' for p in validate(spec)]

    no_location = coops(mock_api)
    no_location['observations']['http']['query']['station'] = 'fixed'
    assert any('{location_id}' in m for m in problems(no_location))
    bad_token = coops(mock_api)
    bad_token['observations']['http']['query']['begin_date'] = '{begin}'
    assert any('Unknown placeholder {begin}' in m for m in problems(bad_token))
    bad_strftime = coops(mock_api)
    bad_strftime['observations']['http']['query']['product'] = '{parameter:%Y}'
    assert any('only date and time values take a format' in m for m in problems(bad_strftime))
    windows = coops(mock_api)
    windows['observations']['default_window'] = 'P60D'
    assert any('longer than max_window' in m for m in problems(windows))
    months = coops(mock_api)
    months['observations']['max_window'] = 'P1M'
    assert problems(months)  # months aren't a fixed length


# --- observations from SQL -----------------------------------------------------------------


@pytest.fixture
def obs_db(tmp_path: Path) -> Path:
    path = tmp_path / 'obs.sqlite'
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sites (site TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL);
        CREATE TABLE obs (site TEXT, param TEXT, t TEXT, v REAL);
        INSERT INTO sites VALUES ('A', 'Alpha', -105.0, 40.0), ('B', 'Beta', -104.0, 39.0);
        """
    )
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(48):
        moment = (start + timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn.execute('INSERT INTO obs VALUES (?, ?, ?, ?)', ('A', 'stage', moment, i / 10))
        conn.execute('INSERT INTO obs VALUES (?, ?, ?, ?)', ('B', 'stage', moment, i / 5))
    conn.commit()
    conn.close()
    return path


def sql_spec(db: Path) -> dict[str, Any]:
    source = {'dialect': 'sqlite', 'path': str(db)}
    return {
        'spec_version': 1,
        'template': 'timeseries-edr',
        'id': 'gages',
        'title': 'Gages',
        'locations': {
            'from': 'sql',
            'sql': {
                'source': source,
                'query': 'SELECT site, name, lon, lat FROM sites',
                'item_id': {'from': 'site'},
                'geometry': {'encoding': 'xy', 'x': 'lon', 'y': 'lat'},
                'properties': [{'name': 'name', 'type': 'string'}],
            },
        },
        'parameters': [{'id': 'stage', 'label': 'Stage', 'unit': {'symbol': 'm'}}],
        'observations': {
            'from': 'sql',
            'sql': {
                'source': source,
                'query': (
                    'SELECT t, v FROM obs WHERE site = :location_id AND param = :parameter '
                    'AND t BETWEEN :start AND :end ORDER BY t'
                ),
                'time': {'from': 't'},
                'value': {'from': 'v'},
            },
        },
    }


def test_sql_locations_and_observations(obs_db: Path) -> None:
    doc = sql_spec(obs_db)
    assert validate(doc) == []
    p = provider(doc)
    cc = call(
        p, 'locations', location_id='B', datetime_='2026-10-01T00:00:00Z/2026-10-01T05:00:00Z'
    )
    assert coveragejson.problems(cc) == []
    (coverage,) = cc['coverages']
    assert coverage['ranges']['stage']['values'] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    area = call(
        p,
        'area',
        wkt=wkt.loads('POLYGON((-106 39.5, -104.5 39.5, -104.5 41, -106 41, -106 39.5))'),
        datetime_='2026-10-01T00:00:00Z/2026-10-01T01:00:00Z',
    )
    assert [c['id'] for c in area['coverages']] == ['A-stage']
    bad = sql_spec(obs_db)
    bad['observations']['sql']['query'] = 'SELECT t, v FROM obs ORDER BY t'
    assert any('location_id' in p.message for p in validate(bad))
