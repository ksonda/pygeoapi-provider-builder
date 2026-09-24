"""The worker as a subprocess: one JSON job in, one JSON result out."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

DATA = Path(__file__).resolve().parents[2] / 'vendor' / 'pygeoapi' / 'tests' / 'data'
PATH_MAP = [{'server': '/data', 'host': str(DATA)}]


def run(job: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, '-m', 'pygeoapi_provider_harness.worker'],
                          input=json.dumps({'protocol': 1, 'path_map': PATH_MAP, **job}),
                          capture_output=True, text=True, timeout=120, check=True,
                          env={**os.environ, **(env or {})})
    return json.loads(proc.stdout)


CSV = """obs:
    type: collection
    providers:
        - type: feature
          name: CSV
          data: /data/obs.csv
          id_field: id
          geometry:
              x_field: long
              y_field: lat
"""


def test_probe_csv() -> None:
    out = run({'kind': 'probe', 'resource_yaml': CSV, 'limit': 2})
    assert out['ok'], out
    r = out['result']
    assert r['fields'] == {'id': {'type': 'string'}, 'stn_id': {'type': 'integer'},
                           'datetime': {'type': 'string'}, 'value': {'type': 'number'}}
    assert len(r['sample']['features']) == 2 and r['sample']['numberMatched'] == 5
    assert r['id_stats'] == {'count': 5, 'nulls': 0, 'duplicates': 0}


def test_probe_geojson() -> None:
    yaml_text = CSV.replace('name: CSV', 'name: GeoJSON').replace(
        '/data/obs.csv', '/data/ne_110m_lakes.geojson').split('          geometry:')[0]
    out = run({'kind': 'probe', 'resource_yaml': yaml_text, 'limit': 3})
    assert out['ok'], out
    assert out['result']['extent']['from'] == 'full'
    assert out['result']['sample']['numberMatched'] == 25


def test_placeholders_resolve_like_pygeoapi() -> None:
    yaml_text = CSV.replace('id_field: id', 'id_field: ${ID_COLUMN}')
    out = run({'kind': 'probe', 'resource_yaml': yaml_text}, env={'ID_COLUMN': 'stn_id'})
    assert out['ok'] and out['result']['id_stats']['duplicates'] > 0


def test_missing_variable_is_pygeoapis_error() -> None:
    out = run({'kind': 'probe', 'resource_yaml': CSV.replace('id_field: id', 'id_field: ${NOPE}')})
    assert not out['ok']
    assert out['error']['message'] == "Could not find the 'NOPE' environment variable"


def test_secrets_are_redacted() -> None:
    yaml_text = CSV.replace('/data/obs.csv', '/data/${SECRET_NAME}.csv')
    out = run({'kind': 'probe', 'resource_yaml': yaml_text, 'secret_env': ['SECRET_NAME']},
              env={'SECRET_NAME': 'hunter2-is-secret'})
    assert not out['ok']
    assert 'hunter2-is-secret' not in json.dumps(out) and '***' in out['error']['message']


def test_introspect_csv_and_geojson() -> None:
    csv = run({'kind': 'introspect', 'what': 'csv', 'values_yaml': 'data: /data/obs.csv\n'})
    assert {c['name']: c['type'] for c in csv['result']['columns']}['value'] == 'number'
    gj = run({'kind': 'introspect', 'what': 'geojson',
              'values_yaml': 'data: /data/ne_110m_lakes.geojson\n'})
    assert gj['result']['count'] == 25
    # this file keeps its ids in properties, not at the feature's top level
    assert not gj['result']['features_have_id']
    assert 'id' in [c['name'] for c in gj['result']['columns']]


def test_introspect_gpkg_needs_no_spatialite() -> None:
    out = run({'kind': 'introspect', 'what': 'sqlite',
               'values_yaml': 'data: /data/poi_portugal.gpkg\n'})
    (table,) = out['result']['tables']
    assert table['name'] == 'poi_portugal' and 'osm_id' in [c['name'] for c in table['columns']]


@pytest.mark.parametrize('job', [{'kind': 'nope'}, {'protocol': 99, 'kind': 'version'}])
def test_bad_jobs_are_reported(job: dict[str, Any]) -> None:
    out = run(job)
    assert not out['ok'] and out['error']['type'] == 'ValueError'


def test_version_and_availability() -> None:
    assert run({'kind': 'version'})['result']['pygeoapi'] == '0.24.0'
    mods = run({'kind': 'availability', 'modules': ['shapely', 'no_such_module']})
    assert mods['result']['modules'] == {'shapely': True, 'no_such_module': False}
