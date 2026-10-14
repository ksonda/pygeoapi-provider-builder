"""A SQLite database for the ``sql-features`` tests: the mock API's wells, split in two.

``wells`` holds where each well is, ``measurements`` what was measured there, so
the specs publish a join. Geometries come three ways: x/y columns, WKT text and
GeoPackage blobs.
"""

from __future__ import annotations

import copy
import importlib.util
import sqlite3
import struct
from pathlib import Path
from typing import Any

from pygeoapi_spec_providers.sql import SQLFeatureProvider
from shapely import wkb
from shapely.geometry import Point

MOCK_API = Path(__file__).resolve().parents[2] / 'docker' / 'mock-api' / 'mock_api.py'

JOIN = (
    'SELECT w.well_id, w.well_name, w.county, w.longitude, w.latitude, w.wkt, w.gpkg,\n'
    '       m.depth, m.measured_at, m.status, m.active\n'
    'FROM wells AS w JOIN measurements AS m ON m.well_id = w.well_id'
)

PROPERTIES = [
    {'name': 'well_id', 'type': 'string'},
    {'name': 'well_name', 'type': 'string'},
    {'name': 'county', 'type': 'string'},
    {'name': 'depth_ft', 'type': 'number', 'from': 'depth'},
    {'name': 'status', 'type': 'string'},
    {'name': 'active', 'type': 'boolean'},
    {'name': 'last_measured', 'type': 'datetime', 'from': 'measured_at'},
]


def gpkg_blob(x: float, y: float, srs_id: int = 4326) -> bytes:
    """A GeoPackage geometry: magic, version 0, little-endian flags, no envelope, WKB."""
    return b'GP' + bytes([0, 0b0000_0001]) + struct.pack('<i', srs_id) + wkb.dumps(Point(x, y))


def mock_wells() -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location('mock_api_for_sql', MOCK_API)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.make_wells())


def make_database(path: Path) -> Path:
    wells = mock_wells()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE wells (well_id TEXT PRIMARY KEY, well_name TEXT, county TEXT,
                            longitude REAL, latitude REAL, wkt TEXT, gpkg BLOB);
        CREATE TABLE measurements (well_id TEXT REFERENCES wells, depth REAL,
                                   measured_at TEXT, status TEXT, active INTEGER);
        """
    )
    for w in wells:
        conn.execute(
            'INSERT INTO wells VALUES (?, ?, ?, ?, ?, ?, ?)',
            (
                w['well_id'],
                w['well_name'],
                w['county_name'],
                w['longitude'],
                w['latitude'],
                w['wkt'],
                gpkg_blob(w['longitude'], w['latitude']),
            ),
        )
        conn.execute(
            'INSERT INTO measurements VALUES (?, ?, ?, ?, ?)',
            (
                w['well_id'],
                w['depth'],
                w['lastMeasurementDate'],
                w['status'],
                1 if w['status'] == 'active' else 0,
            ),
        )
    conn.commit()
    conn.close()
    return path


def spec(db: Path, geometry: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        'spec_version': 1,
        'template': 'sql-features',
        'id': 'sql-wells',
        'title': 'Wells (SQL join)',
        'version': '1.0.0',
        'source': {'dialect': 'sqlite', 'path': str(db)},
        'query': JOIN,
        'item_id': {'from': 'well_id'},
        'geometry': geometry or {'encoding': 'xy', 'x': 'longitude', 'y': 'latitude'},
        'properties': copy.deepcopy(PROPERTIES),
        'time_field': 'last_measured',
        'title_field': 'well_name',
    }
    doc.update(extra)
    return doc


def provider(doc: dict[str, Any], **config: Any) -> SQLFeatureProvider:
    return SQLFeatureProvider(
        {
            'name': 'pygeoapi_spec_providers.sql.SQLFeatureProvider',
            'type': 'feature',
            'data': doc,
            **config,
        }
    )
