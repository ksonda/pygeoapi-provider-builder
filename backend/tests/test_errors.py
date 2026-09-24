"""Each plain-language rule in errors.yaml, against realistic raw errors."""

from __future__ import annotations

from typing import Any

import pytest

from provider_builder.errors import _rules, explain


def err(kind: str, message: str, traceback: str = '') -> dict[str, Any]:
    return {'type': kind, 'message': message, 'traceback': traceback,
            'chain': [{'type': kind, 'message': message}]}


PG = {'descriptor': 'PostgreSQL', 'host': 'db.example.org', 'port': 5432}

CASES = [
    ('timeout', err('SandboxTimeout', 'Stopped after 60 s'), {'timeout': 60},
     'The test took longer than 60 seconds and was stopped.'),
    ('sandbox-unavailable', err('SandboxUnavailable', 'x'), {}, "The test sandbox isn't running."),
    ('pg-auth', err('pygeoapi.provider.base.ProviderConnectionError',
                    'connection to server at "db" (10.0.0.2), port 5432 failed: FATAL:  '
                    'password authentication failed for user "reader"'), PG,
     'The database rejected the user name or password for user reader.'),
    ('pg-unknown-host', err('sqlalchemy.exc.OperationalError',
                            'could not translate host name "db.exmaple.org" to address'), PG,
     "The host name db.exmaple.org couldn't be found."),
    ('pg-refused', err('sqlalchemy.exc.OperationalError',
                       'connection to server at "db.example.org" (1.2.3.4), port 5432 failed: '
                       'Connection refused'), PG,
     "Couldn't reach db.example.org:5432. Check the host, port and firewall."),
    ('pg-timeout', err('sqlalchemy.exc.OperationalError', 'timeout expired'), PG,
     'Connecting to db.example.org:5432 timed out.'),
    ('pg-no-database', err('sqlalchemy.exc.OperationalError',
                           'FATAL:  database "tset" does not exist'), PG,
     'There is no database called tset on that server.'),
    ('sql-table', err('pygeoapi.provider.base.ProviderQueryError',
                      "Table 'waterway' not found in schema 'public' on postgresql://x"), PG,
     "There's no table or view called waterway in the schemas searched."),
    ('gpkg-readonly', err('pygeoapi.plugin.InvalidPluginError', ''),
     {'descriptor': 'SQLiteGPKG'}, "pygeoapi couldn't enable GeoPackage support for this file."),
    ('gpkg-table', err('pygeoapi.plugin.InvalidPluginError', ''),
     {'descriptor': 'SQLiteGPKG', 'table': 'pois'},
     "pygeoapi couldn't open table pois with that identifier column."),
    ('file-missing', err('FileNotFoundError',
                         "[Errno 2] No such file or directory: '/data/obs2.csv'"),
     {'descriptor': 'CSV'}, 'No file at /data/obs2.csv, as the server sees it.'),
    ('csv-bad-column', err('KeyError', "'lon'", "Traceback...\nKeyError: 'lon'"),
     {'descriptor': 'CSV'}, 'The CSV file has no column called lon.'),
    ('csv-no-id', err('KeyError', 'None', 'Traceback...\nKeyError: None'),
     {'descriptor': 'CSV'}, 'Choose the identifier column. pygeoapi needs it for every row.'),
    ('invalid-plugin', err('pygeoapi.plugin.InvalidPluginError', 'Plugin CSVv not found'), {},
     "pygeoapi has no provider called 'CSVv'."),
    ('module-missing', err('ModuleNotFoundError', "No module named 'pygeoapi_plugins'"), {},
     "The Python package that provides pygeoapi_plugins isn't installed where pygeoapi runs."),
    ('crs', err('pyproj.exceptions.CRSError', 'Invalid projection'), {},
     "That coordinate reference system isn't recognised."),
    ('bad-json', err('json.decoder.JSONDecodeError', 'Expecting value'), {},
     "The file isn't valid JSON."),
    ('empty-geojson', err('IndexError', 'list index out of range'), {'descriptor': 'GeoJSON'},
     'The GeoJSON file has no features.'),
]


@pytest.mark.parametrize(('code', 'error', 'context', 'message'), CASES,
                         ids=[c[0] for c in CASES])
def test_rule(code: str, error: dict[str, Any], context: dict[str, Any], message: str) -> None:
    logs = ''
    if code == 'gpkg-readonly':
        logs = "INFO SELECT AutoGPKGStart() returned 0.Detected GPKG but couldn't load support"
    e = explain(error, context, logs)
    assert (e.code, e.message) == (code, message)


def test_every_rule_has_a_case() -> None:
    covered = {c[0] for c in CASES} | {'spatialite', 'gpkg-missing'}
    assert {r['id'] for r in _rules()} <= covered


def test_unexplained_errors_show_the_raw_message() -> None:
    e = explain(err('ValueError', 'something odd'), {})
    assert (e.code, e.message) == ('unexplained', 'ValueError: something odd')
