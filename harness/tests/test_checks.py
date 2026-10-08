"""Harness self-tests (PLAN.md §12): core providers pass, broken ones fail clearly."""

from __future__ import annotations

import importlib.util
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pygeoapi_provider_harness.checks import Checklist, run_contract
from test_worker import run

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / 'vendor' / 'pygeoapi' / 'tests' / 'data'


def resource(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        'type': 'collection',
        'title': 'Harness test',
        'description': 'A resource for the harness self-tests',
        'keywords': ['test'],
        'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
        'providers': [provider],
    }


def statuses(checklist: Checklist) -> dict[str, str]:
    return {c.id: c.status for c in checklist.results}


def by_id(checklist: Checklist, check_id: str) -> Any:
    return next(c for c in checklist.results if c.id == check_id)


CSV_CAPABILITIES = {
    'bbox': True,
    'properties': True,
    'select_properties': True,
    'skip_geometry': True,
    'datetime': False,
    'sortby': False,
    'q': False,
}


def test_csv_passes() -> None:
    out = run_contract(
        'obs',
        resource(
            {
                'type': 'feature',
                'name': 'CSV',
                'data': str(DATA / 'obs.csv'),
                'id_field': 'id',
                'geometry': {'x_field': 'long', 'y_field': 'lat'},
            }
        ),
        capabilities=CSV_CAPABILITIES,
        levels=('L1', 'L2'),
    )
    assert out.ok(), [c for c in out.results if c.status == 'fail']
    got = statuses(out)
    assert got['L1.fields'] == got['L1.query'] == got['L1.get_unknown'] == 'pass'
    assert got['L1.bbox'] == got['L1.properties'] == 'pass'
    assert {got['L2.items'], got['L2.item'], got['L2.queryables'], got['L2.schema']} == {'pass'}


def test_geojson_passes() -> None:
    out = run_contract(
        'lakes',
        resource(
            {
                'type': 'feature',
                'name': 'GeoJSON',
                'data': str(DATA / 'ne_110m_lakes.geojson'),
                'id_field': 'id',
            }
        ),
        levels=('L1', 'L2'),
    )
    assert out.ok(), [c for c in out.results if c.status == 'fail']


def broken(name: str) -> Checklist:
    return run_contract(
        'broken',
        resource(
            {
                'type': 'feature',
                'name': f'broken_providers.{name}',
                'data': 'unused',
            }
        ),
        levels=('L1', 'L2'),
    )


def test_the_good_fixture_passes() -> None:
    out = broken('Good')
    assert out.ok(), [c for c in out.results if c.status == 'fail']


@pytest.mark.parametrize(
    ('provider', 'check_id', 'fragment'),
    [
        ('StringFields', 'L1.fields', 'not dicts with a type'),
        ('EmptyFields', 'L1.fields', 'p.fields is empty'),
        ('NoFeaturesOnHits', 'L1.hits', 'has no features key'),
        ('GetReturnsNone', 'L1.get_unknown', 'returned None for an unknown id'),
    ],
)
def test_broken_providers_fail_with_the_right_message(
    provider: str, check_id: str, fragment: str
) -> None:
    out = broken(provider)
    check = by_id(out, check_id)
    assert check.status == 'fail', check
    assert fragment in check.message
    assert check.hint
    assert all(c.status == 'skip' for c in out.results if c.level == 'L2')


def test_edr_without_units_fails() -> None:
    out = run_contract(
        'edr',
        resource({'type': 'edr', 'name': 'broken_providers.EdrNoUnits', 'data': 'unused'}),
        levels=('L1',),
    )
    check = by_id(out, 'L1.edr_fields')
    assert check.status == 'fail'
    assert 'no x-ogc-unit' in check.message


def load_mock_api() -> Any:
    spec = importlib.util.spec_from_file_location(
        'mock_api_h', REPO / 'docker' / 'mock-api' / 'mock_api.py'
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def mock_api() -> Iterator[str]:
    from werkzeug.serving import make_server

    os.environ['PYGEOAPI_SPEC_PRIVATE_HOSTS'] = '127.0.0.1'
    server = make_server('127.0.0.1', 0, load_mock_api().app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


SPEC = """spec_version: 1
template: http-features
id: mock-wells
title: Mock wells
source:
  url: {base}/v2/wells
records: $.data.items
item_id:
  from: well_id
geometry:
  type: point
  x: longitude
  y: latitude
properties:
  - name: well_id
    type: string
  - name: well_name
    type: string
  - name: county
    type: string
    from: county_name
  - name: depth_ft
    type: number
    from: depth
  - name: last_measured
    type: datetime
    from: lastMeasurementDate
time_field: last_measured
title_field: well_name
pagination:
  style: offset
  offset_param: offset
  limit_param: limit
  total: $.meta.total
"""

SPEC_RESOURCE = """mock-wells:
    type: collection
    title: Mock wells
    description: Wells from the mock API
    keywords: [wells]
    extents:
        spatial:
            bbox: [-118, 41, -110, 49]
    providers:
        - type: feature
          name: pygeoapi_spec_providers.http.HTTPFeatureProvider
          data: @files@/spec.yaml
"""
EVERYTHING = dict.fromkeys(
    ('bbox', 'datetime', 'properties', 'sortby', 'select_properties', 'skip_geometry', 'q'), True
)


def test_the_spec_engine_passes_every_check_through_the_worker(mock_api: str) -> None:
    """The harness job, as the sandbox runs it: the spec arrives as a job file."""
    out = run(
        {
            'kind': 'harness',
            'resource_yaml': SPEC_RESOURCE,
            'levels': ['L1', 'L2', 'L3'],
            'files': {'spec.yaml': SPEC.format(base=mock_api)},
            'capabilities': EVERYTHING,
        },
        env={'PYGEOAPI_SPEC_PRIVATE_HOSTS': '127.0.0.1'},
    )
    assert out['ok'], out
    result = out['result']
    failures = [c for c in result['checks'] if c['status'] == 'fail']
    assert result['ok'] and not failures, failures
    got = {c['id']: c['status'] for c in result['checks']}
    for capability in EVERYTHING:
        assert got[f'L1.{capability}'] == 'pass', (capability, result['checks'])
    assert got['L2.item'] == 'pass'
    assert got['L3.server'] in ('pass', 'skip')  # gunicorn is in the sandbox image, not the venv


def test_the_sample_job_fetches_through_the_engine_client(mock_api: str) -> None:
    out = run(
        {'kind': 'sample', 'source_yaml': f'url: {mock_api}/v2/wells\nquery:\n  limit: 2\n'},
        env={'PYGEOAPI_SPEC_PRIVATE_HOSTS': '127.0.0.1'},
    )
    assert out['ok'], out
    result = out['result']
    assert result['status'] == 200
    assert result['json']['meta']['total'] == 120
    assert len(result['json']['data']['items']) == 2
    assert result['headers']['content-type'].startswith('application/json')


def test_the_sample_job_keeps_the_egress_rules(mock_api: str) -> None:
    out = run(
        {'kind': 'sample', 'source_yaml': f'url: {mock_api}/v2/wells\n'},
        env={'PYGEOAPI_SPEC_PRIVATE_HOSTS': ''},
    )
    assert not out['ok']
    assert (
        'non-public address' in out['error']['message'] or 'plain http' in out['error']['message']
    )
