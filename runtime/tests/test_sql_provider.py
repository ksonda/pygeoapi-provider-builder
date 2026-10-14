"""``sql-features`` on SQLite: a join of the mock API's wells and their measurements."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pygeoapi.provider.base import (
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderQueryError,
)
from pygeoapi_spec_providers import db as dbmod
from pygeoapi_spec_providers import sqlguard
from pygeoapi_spec_providers.spec import SpecError, check_text, validate
from shapely.geometry import box, shape
from sqlfixtures import make_database, provider, spec

QUERY: dict[str, Any] = {
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

GEOMETRIES = {
    'xy': {'encoding': 'xy', 'x': 'longitude', 'y': 'latitude'},
    'wkt': {'encoding': 'wkt', 'column': 'wkt'},
    'gpkg': {'encoding': 'gpkg', 'column': 'gpkg'},
}


def q(p: Any, **kwargs: Any) -> dict[str, Any]:
    return dict(p.query(**{**QUERY, **kwargs}))


def ids(result: dict[str, Any]) -> list[Any]:
    return [f['id'] for f in result['features']]


@pytest.fixture(scope='module')
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_database(tmp_path_factory.mktemp('sql') / 'wells.sqlite')


@pytest.fixture(params=sorted(GEOMETRIES))
def wells(request: pytest.FixtureRequest, db: Path) -> Any:
    return provider(spec(db, GEOMETRIES[request.param]))


def test_the_spec_validates(db: Path) -> None:
    assert validate(spec(db)) == []


def test_fields_counts_and_stable_order(wells: Any) -> None:
    assert wells.fields['depth_ft'] == {'type': 'number'}
    assert wells.fields['last_measured'] == {'type': 'string', 'format': 'date-time'}
    first = q(wells)
    assert (first['numberMatched'], first['numberReturned']) == (120, 10)
    assert ids(first) == [f'W{i:04d}' for i in range(1, 11)]
    feature = first['features'][0]
    assert feature['geometry']['type'] == 'Point'
    assert set(feature['properties']) == {
        'well_id', 'well_name', 'county', 'depth_ft', 'status', 'active', 'last_measured',
    }  # fmt: skip
    assert isinstance(feature['properties']['active'], bool)
    assert feature['properties']['last_measured'].endswith('Z')
    second = q(wells, offset=10)
    assert not set(ids(first)) & set(ids(second))
    assert ids(q(wells, limit=20)) == ids(first) + ids(second)


def test_hits_counts_without_features(wells: Any) -> None:
    hits = q(wells, resulttype='hits')
    assert hits['features'] == [] and hits['numberMatched'] == 120


def test_bbox_pushed_down_or_local_gives_the_same_items(wells: Any) -> None:
    area = [-115.0, 43.0, -113.0, 45.0]
    got = q(wells, bbox=area, limit=500)
    inside = box(*area)
    assert got['features']
    assert all(shape(f['geometry']).intersects(inside) for f in got['features'])
    assert got['numberMatched'] == len(got['features'])
    everything = q(wells, limit=500)['features']
    expected = [f['id'] for f in everything if shape(f['geometry']).intersects(inside)]
    assert ids(got) == expected


def test_datetime_property_sort_and_q(wells: Any) -> None:
    sample = q(wells, limit=500)['features']
    target = sample[7]
    moment = target['properties']['last_measured']
    exact = q(wells, datetime_=f'{moment}/{moment}', limit=500)
    assert target['id'] in ids(exact)
    assert all(f['properties']['last_measured'] == moment for f in exact['features'])
    later = q(wells, datetime_=f'{moment}/..', limit=500)
    assert all(f['properties']['last_measured'] >= moment for f in later['features'])

    ada = q(wells, properties=[('county', 'Ada')], limit=500)
    assert ada['features'] and all(f['properties']['county'] == 'Ada' for f in ada['features'])
    active = q(wells, properties=[('active', 'true')], limit=500)
    assert all(f['properties']['status'] == 'active' for f in active['features'])
    assert q(wells, properties=[('depth_ft', 'deep')])['features'] == []

    deep = q(wells, sortby=[{'property': 'depth_ft', 'order': '-'}], limit=20)
    depths = [f['properties']['depth_ft'] for f in deep['features']]
    assert depths == sorted(depths, reverse=True)

    found = q(wells, q='kootenai', limit=500)
    assert found['features']
    assert all('kootenai' in f['properties']['well_name'].lower() for f in found['features'])
    assert q(wells, q='zqxjkvwy')['features'] == []


def test_select_skip_and_cql(wells: Any) -> None:
    got = q(wells, select_properties=['county'], skip_geometry=True, limit=3)
    assert all(set(f['properties']) == {'county'} for f in got['features'])
    assert all(f['geometry'] is None for f in got['features'])
    with pytest.raises(ProviderInvalidQueryError):
        q(wells, filterq=object())


def test_get_round_trips_and_unknown_ids_are_404(wells: Any) -> None:
    assert wells.get('W0042')['properties']['well_id'] == 'W0042'
    with pytest.raises(ProviderItemNotFoundError):
        wells.get('W9999')


def test_integer_ids_take_the_column_type(db: Path) -> None:
    doc = spec(
        db,
        query='SELECT rowid AS fid, w.well_id, w.longitude, w.latitude FROM wells AS w',
        item_id={'from': 'fid', 'type': 'integer'},
        properties=[{'name': 'well_id', 'type': 'string'}],
        time_field=None,
        title_field=None,
    )
    doc = {k: v for k, v in doc.items() if v is not None}
    p = provider(doc)
    assert p.get('5')['id'] == 5
    with pytest.raises(ProviderItemNotFoundError):
        p.get('five')


def test_spec_problems(db: Path) -> None:
    def problems(**extra: Any) -> list[str]:
        return [f'{p.pointer} {p.message}' for p in validate(spec(db, **extra))]

    assert any('DELETE' in m for m in problems(query='WITH d AS (DELETE FROM wells) SELECT 1'))
    native = problems(geometry={'encoding': 'native', 'column': 'g'})
    assert any('/geometry/encoding' in m for m in native)
    with_param = problems(query='SELECT * FROM wells WHERE well_id = :id')
    assert any('Unexpected parameter' in m for m in with_param)
    text = (
        'template: sql-features\nsource:\n  dialect: postgresql\n  connection:\n'
        '    host: db\n    dbname: x\n    user: u\n    password: hunter2\n'
    )
    assert [p.pointer for p in check_text(text)] == ['/source/connection/password']
    assert check_text(text.replace('hunter2', '${DB_PASSWORD}')) == []


def test_the_connection_is_read_only_and_timed(db: Path) -> None:
    target = dbmod.target_for({'dialect': 'sqlite', 'path': str(db), 'statement_timeout_s': 0.3})
    database = dbmod.Database(target)
    with pytest.raises(dbmod.DatabaseError) as err, database.connect() as conn:
        conn.exec_driver_sql('CREATE TABLE sneaky (a)')
    assert err.value.kind == 'readonly'
    slow = 'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n'
    with pytest.raises(dbmod.DatabaseError) as err, database.connect() as conn:
        conn.exec_driver_sql(sqlguard.check(slow, 'sqlite').sql).scalar()
    assert err.value.kind == 'timeout'


def test_a_slow_query_is_a_provider_error_with_a_user_message(db: Path) -> None:
    doc = spec(
        db,
        query=(
            'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n LIMIT 100000000) '
            'SELECT w.* FROM wells AS w JOIN n ON n.i = 1'
        ),
        source={'dialect': 'sqlite', 'path': str(db), 'statement_timeout_s': 0.3},
        properties=[{'name': 'well_id', 'type': 'string'}],
        time_field=None,
        title_field=None,
    )
    doc = {k: v for k, v in doc.items() if v is not None}
    with pytest.raises(ProviderQueryError) as err:
        q(provider(doc), resulttype='hits')
    assert err.value.user_msg == 'The database query took too long.'


def test_a_missing_file_is_a_connection_error(tmp_path: Path) -> None:
    doc = spec(tmp_path / 'nope.sqlite')
    with pytest.raises(Exception) as err:
        q(provider(doc))
    assert getattr(err.value, 'user_msg', '') == "The database couldn't be reached."


def test_an_invalid_spec_does_not_load(db: Path) -> None:
    with pytest.raises(Exception) as err:
        provider(spec(db, query='DROP TABLE wells'))
    assert isinstance(err.value.__cause__, SpecError)
