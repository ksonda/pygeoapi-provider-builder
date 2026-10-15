"""A mock JSON API for Mode B tests and demos (PLAN.md §12, "Runtime engine").

120 synthetic wells (seeded, so every run is identical) behind one endpoint per
pagination style, auth type and failure the spec engine has to handle, and a
NOAA CO-OPS look-alike (``/coops/…``: a station list and the datagetter, with
CO-OPS's response shapes and errors) for the time-series template. For Configure's
service providers: an ArcGIS FeatureServer look-alike (``/arcgis/…``, one open
layer and one behind a token), pygeoapi's own vector tiles as a static
``{z}/{x}/{y}.pbf`` server (``/tiles/…``), and a WMS GetMap (``/wms``). Flask
because it's already in the pinned pygeoapi image: the dev stack runs this
file there with gunicorn, and tests serve it in-process.

Test credentials (override with environment variables): X-API-Key / api_key
``test-key``, bearer ``test-token``, basic ``reader`` / ``test-password``.
"""

from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import threading
import time
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

API_KEY = os.environ.get('MOCK_API_KEY', 'test-key')
TOKEN = os.environ.get('MOCK_API_TOKEN', 'test-token')
USER = 'reader'
PASSWORD = os.environ.get('MOCK_API_PASSWORD', 'test-password')
MAX_PAGE = 100
COUNTIES = ['Ada', 'Bonner', 'Canyon', 'Kootenai', 'Twin Falls']
SORTABLE = {'well_id', 'well_name', 'depth', 'lastMeasurementDate'}


def make_wells(n: int = 120) -> list[dict[str, Any]]:
    rng = random.Random(20260923)  # noqa: S311  # seeded test data, not security
    start = datetime(2020, 1, 1, tzinfo=UTC)
    wells = []
    for i in range(1, n + 1):
        county = rng.choice(COUNTIES)
        lon = round(-117.2 + rng.random() * 6.1, 5)
        lat = round(42.0 + rng.random() * 6.9, 5)
        measured = start + timedelta(days=rng.randrange(0, 2000), seconds=rng.randrange(86400))
        wells.append(
            {
                'well_id': f'W{i:04d}',
                'well_name': f'{county} well {i}',
                'county_name': county,
                'longitude': lon,
                'latitude': lat,
                'depth': round(rng.uniform(20, 900), 1),
                'status': rng.choice(['active', 'inactive', 'abandoned']),
                'lastMeasurementDate': measured.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'measured_epoch': int(measured.timestamp()),
                'location': {'type': 'Point', 'coordinates': [lon, lat]},
                'wkt': f'POINT ({lon} {lat})',
            }
        )
    return wells


WELLS = make_wells()
app = Flask(__name__)
app.json.sort_keys = False  # keep fields in their natural order, as most APIs do
_flaky: dict[str, int] = {}
_flaky_lock = threading.Lock()


def number(name: str, default: int, cap: int | None = None) -> int:
    try:
        value = int(request.args.get(name, default))
    except ValueError:
        value = default
    value = max(0, value)
    return min(value, cap) if cap is not None else value


def filtered() -> list[dict[str, Any]]:
    """Server-side filters, for specs that push them down."""
    rows = list(WELLS)
    args = request.args
    if args.get('bbox'):
        minx, miny, maxx, maxy = (float(v) for v in args['bbox'].split(','))
        rows = [
            r for r in rows if minx <= r['longitude'] <= maxx and miny <= r['latitude'] <= maxy
        ]
    if args.get('from'):
        rows = [r for r in rows if r['lastMeasurementDate'] >= args['from']]
    if args.get('to'):
        rows = [r for r in rows if r['lastMeasurementDate'] <= args['to']]
    if args.get('county'):
        rows = [r for r in rows if r['county_name'] == args['county']]
    if args.get('search'):
        term = args['search'].lower()
        rows = [r for r in rows if term in r['well_name'].lower()]
    if args.get('sort'):
        field, _, order = args['sort'].partition(':')
        if field in SORTABLE:
            rows.sort(key=lambda r: r[field], reverse=order == 'desc')
    return rows


def offset_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    offset, limit = number('offset', 0), number('limit', 10, MAX_PAGE)
    return {
        'meta': {'total': len(rows), 'offset': offset, 'limit': limit},
        'data': {'items': rows[offset : offset + limit]},
    }


@app.get('/health')
def health() -> Response:
    return jsonify(status='ok', wells=len(WELLS))


@app.get('/v2/wells')
def wells_offset() -> Response:
    return jsonify(offset_page(filtered()))


@app.get('/v2/wells/<well_id>')
def well(well_id: str) -> tuple[Response, int] | Response:
    for row in WELLS:
        if row['well_id'] == well_id:
            return jsonify(data=row)
    return jsonify(error='not found'), 404


@app.get('/paged/wells')
def wells_paged() -> Response:
    page, size = max(1, number('page', 1)), max(1, number('per_page', 25, 50))
    rows = filtered()
    return jsonify(
        page=page, per_page=size, count=len(rows), results=rows[(page - 1) * size : page * size]
    )


@app.get('/linked/wells')
def wells_linked() -> Response:
    start, size = number('start', 0), max(1, number('size', 25, 50))
    rows = filtered()
    links = [{'rel': 'self', 'href': request.url}]
    if start + size < len(rows):
        links.append(
            {'rel': 'next', 'href': f'{request.base_url}?start={start + size}&size={size}'}
        )
    return jsonify(items=rows[start : start + size], links=links)


@app.get('/header-linked/wells')
def wells_header_linked() -> Response:
    start, size = number('start', 0), max(1, number('size', 25, 50))
    rows = filtered()
    response = jsonify(rows[start : start + size])
    if start + size < len(rows):
        response.headers['Link'] = (
            f'<{request.base_url}?start={start + size}&size={size}>; rel="next"'
        )
    return response


@app.get('/cursor/wells')
def wells_cursor() -> Response:
    cursor = request.args.get('cursor')
    start = int(base64.urlsafe_b64decode(cursor).decode()) if cursor else 0
    rows = filtered()
    nxt = start + 25
    token = base64.urlsafe_b64encode(str(nxt).encode()).decode() if nxt < len(rows) else None
    return jsonify(items=rows[start:nxt], next_cursor=token)


@app.get('/all/wells')
def wells_all() -> Response:
    return jsonify(filtered())


@app.get('/geojson/wells')
def wells_geojson() -> Response:
    return jsonify(
        items=[
            {'id': r['well_id'], 'name': r['well_name'], 'geometry': r['location']} for r in WELLS
        ]
    )


@app.get('/esri/wells')
def wells_esri() -> Response:
    if request.args.get('token') != API_KEY:
        return jsonify(error={'code': 498, 'message': 'Invalid token.'})
    features = [
        {
            'attributes': {
                'OBJECTID': i,
                'NAME': r['well_name'],
                'MEASURED': r['measured_epoch'] * 1000,
            },
            'geometry': {'x': r['longitude'], 'y': r['latitude']},
        }
        for i, r in enumerate(WELLS, 1)
    ]
    return jsonify(features=features)


def unauthorized() -> tuple[Response, int]:
    return jsonify(message='unauthorized'), 401


@app.get('/secure/header/wells')
def secure_header() -> tuple[Response, int] | Response:
    if request.headers.get('X-API-Key') != API_KEY:
        return unauthorized()
    return jsonify(offset_page(filtered()))


@app.get('/secure/query/wells')
def secure_query() -> tuple[Response, int] | Response:
    if request.args.get('api_key') != API_KEY:
        return unauthorized()
    return jsonify(offset_page(filtered()))


@app.get('/secure/bearer/wells')
def secure_bearer() -> tuple[Response, int] | Response:
    if request.headers.get('Authorization') != f'Bearer {TOKEN}':
        return unauthorized()
    return jsonify(offset_page(filtered()))


@app.get('/secure/basic/wells')
def secure_basic() -> tuple[Response, int] | Response:
    auth = request.authorization
    if not auth or auth.username != USER or auth.password != PASSWORD:
        return unauthorized()
    return jsonify(offset_page(filtered()))


@app.get('/flaky/wells')
def flaky() -> tuple[Response, int] | Response:
    """Every other request per X-Test-Run value is a 429 with Retry-After: 0."""
    key = request.headers.get('X-Test-Run', '')
    with _flaky_lock:
        _flaky[key] = _flaky.get(key, 0) + 1
        count = _flaky[key]
    if count % 2:
        response = jsonify(message='slow down')
        response.headers['Retry-After'] = '0'
        return response, 429
    return jsonify(offset_page(filtered()))


@app.get('/slow/wells')
def slow() -> Response:
    time.sleep(min(float(request.args.get('delay', '2')), 30.0))
    return jsonify(offset_page(filtered()))


@app.get('/big/wells')
def big() -> Response:
    padding = 'x' * 1000
    return Response(
        json.dumps({'items': [dict(r, padding=padding) for r in WELLS * 20]}),
        mimetype='application/json',
    )


@app.get('/not-json/wells')
def not_json() -> Response:
    return Response('<html>maintenance</html>', mimetype='text/html')


@app.get('/error/wells')
def server_error() -> tuple[Response, int]:
    return jsonify(message='boom'), 500


@app.get('/redirect/out')
def redirect_out() -> Response:
    return Response(status=302, headers={'Location': 'https://example.org/wells'})


@app.get('/redirect/in')
def redirect_in() -> Response:
    return Response(status=302, headers={'Location': '/v2/wells'})


# --- a NOAA CO-OPS look-alike (api.tidesandcurrents.noaa.gov) -------------------------------

STATIONS = [
    {'id': '9414290', 'name': 'San Francisco', 'state': 'CA', 'lat': 37.8063, 'lng': -122.4659},
    {'id': '8518750', 'name': 'The Battery', 'state': 'NY', 'lat': 40.7006, 'lng': -74.0142},
    {'id': '8443970', 'name': 'Boston', 'state': 'MA', 'lat': 42.3539, 'lng': -71.0503},
    {'id': '9447130', 'name': 'Seattle', 'state': 'WA', 'lat': 47.6026, 'lng': -122.3393},
    {'id': '8724580', 'name': 'Key West', 'state': 'FL', 'lat': 24.5508, 'lng': -81.8081},
    {'id': '1612340', 'name': 'Honolulu', 'state': 'HI', 'lat': 21.3033, 'lng': -157.8645},
]
#: Products each station offers; Honolulu has no air temperature here, to test "no data".
PRODUCTS = {
    'water_level': (timedelta(minutes=6), 1.0, 0.8, 12.42),
    'air_temperature': (timedelta(hours=1), 15.0, 5.0, 24.0),
}
NO_DATA = (
    'No data was found. This product may not be offered at this station at the requested time.'
)


def coops_date(text: str | None) -> datetime | None:
    for fmt in ('%Y%m%d %H:%M', '%Y%m%d'):
        try:
            return datetime.strptime(str(text), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def coops_error(message: str) -> Response:
    return jsonify(error={'message': message})  # CO-OPS answers HTTP 200 with an error body


@app.get('/coops/mdapi/prod/webapi/stations.json')
def coops_stations() -> Response:
    stations = [dict(s, tidal=True, greatlakes=False) for s in STATIONS]
    return jsonify(count=len(stations), units=None, stations=stations)


@app.get('/coops/api/prod/datagetter')
def coops_data() -> Response:
    args = request.args
    station = next((s for s in STATIONS if s['id'] == args.get('station')), None)
    product = args.get('product')
    if args.get('format') != 'json':
        return Response('format=json is required', status=400, mimetype='text/plain')
    if product not in PRODUCTS:
        return coops_error(f'Wrong Product: {product}')
    begin, end = coops_date(args.get('begin_date')), coops_date(args.get('end_date'))
    if begin is None or end is None:
        return coops_error('Wrong Begin Date or End Date')
    if end - begin > timedelta(days=31):
        return coops_error('The size limit for data retrieval for this product is 31 days')
    if station is None or (product == 'air_temperature' and station['state'] == 'HI'):
        return coops_error(NO_DATA)
    step, base, amplitude, period_h = PRODUCTS[product]
    phase = zlib.crc32(station['id'].encode()) % 360 / 180 * math.pi
    moment = begin + (datetime.min.replace(tzinfo=UTC) - begin) % step
    data = []
    while moment <= end:
        hours = moment.timestamp() / 3600
        value = base + amplitude * math.sin(2 * math.pi * hours / period_h + phase)
        data.append({'t': moment.strftime('%Y-%m-%d %H:%M'), 'v': f'{value:.3f}', 's': '0.003',
                     'f': '0,0,0,0', 'q': 'p'})  # fmt: skip
        moment += step
    if not data:
        return coops_error(NO_DATA)
    meta = {'id': station['id'], 'name': station['name'], 'lat': str(station['lat']),
            'lon': str(station['lng'])}  # fmt: skip
    return jsonify(metadata=meta, data=data)


# --- an ArcGIS REST FeatureServer look-alike, for pygeoapi's ESRI provider -----------------

ARCGIS_LAYER = '/arcgis/rest/services/<service>/FeatureServer/0'
ARCGIS_SERVICES = {'Wells': False, 'SecureWells': True}  # name -> needs a token
ARCGIS_MAX_RECORDS = 50
ARCGIS_FIELDS = [
    {'name': 'OBJECTID', 'type': 'esriFieldTypeOID', 'alias': 'OBJECTID'},
    {'name': 'well_id', 'type': 'esriFieldTypeString', 'alias': 'Well ID', 'length': 8},
    {'name': 'well_name', 'type': 'esriFieldTypeString', 'alias': 'Name', 'length': 64},
    {'name': 'county_name', 'type': 'esriFieldTypeString', 'alias': 'County', 'length': 32},
    {'name': 'depth', 'type': 'esriFieldTypeDouble', 'alias': 'Depth (ft)'},
    {'name': 'status', 'type': 'esriFieldTypeString', 'alias': 'Status', 'length': 16},
    {'name': 'measured', 'type': 'esriFieldTypeDate', 'alias': 'Last measured', 'length': 8},
]
WHERE_TERM = re.compile(
    r'^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>>=|<=|=)\s*'
    r"(?:TIMESTAMP\s+'(?P<ts>[^']*)'|'(?P<text>(?:[^']|'')*)'|(?P<number>-?\d+(?:\.\d+)?))\s*$"
)


def arcgis_row(i: int, r: dict[str, Any]) -> dict[str, Any]:
    return {
        'OBJECTID': i,
        'well_id': r['well_id'],
        'well_name': r['well_name'],
        'county_name': r['county_name'],
        'depth': r['depth'],
        'status': r['status'],
        'measured': r['measured_epoch'] * 1000,
    }


ARCGIS_ROWS = [(arcgis_row(i, r), r['location']) for i, r in enumerate(WELLS, 1)]


def arcgis_error(code: int, message: str) -> Response:
    return jsonify(error={'code': code, 'message': message, 'details': []})  # HTTP 200, as ArcGIS


def arcgis_denied(service: str) -> Response | None:
    if service not in ARCGIS_SERVICES:
        return arcgis_error(400, 'Invalid URL')
    if ARCGIS_SERVICES[service]:
        token = request.args.get('token', '')
        sent = request.headers.get('X-Esri-Authorization', '') or f'Bearer {token}'
        if sent != f'Bearer {TOKEN}':
            return arcgis_error(499, 'Token Required')
    return None


def arcgis_matches(row: dict[str, Any], where: str) -> bool:
    """The subset of ArcGIS SQL pygeoapi sends: 1 = 1, or terms joined by AND."""
    if where.strip() in ('', '1 = 1', '1=1'):
        return True
    for term in re.split(r'\s+AND\s+', where, flags=re.IGNORECASE):
        m = WHERE_TERM.match(term)
        if not m or m['field'] not in row:
            raise ValueError(f'Unable to perform query: {term}')
        value = row[m['field']]
        if m['ts'] is not None:
            when = datetime.strptime(m['ts'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
            other: Any = int(when.timestamp() * 1000)
        elif m['text'] is not None:
            other = m['text'].replace("''", "'")
            if isinstance(value, int | float) and not isinstance(value, bool):
                try:
                    other = float(other)
                except ValueError:
                    return False
        else:
            other = float(m['number'])
        if not {'=': value == other, '>=': value >= other, '<=': value <= other}[m['op']]:
            return False
    return True


@app.get(ARCGIS_LAYER)
def arcgis_layer(service: str) -> Response:
    denied = arcgis_denied(service)
    if denied is not None:
        return denied
    lons = [r['longitude'] for r in WELLS]
    lats = [r['latitude'] for r in WELLS]
    return jsonify(
        currentVersion=11.1,
        id=0,
        name=service,
        type='Feature Layer',
        geometryType='esriGeometryPoint',
        objectIdField='OBJECTID',
        displayField='well_name',
        extent={
            'xmin': min(lons),
            'ymin': min(lats),
            'xmax': max(lons),
            'ymax': max(lats),
            'spatialReference': {'wkid': 4326, 'latestWkid': 4326},
        },  # fmt: skip
        fields=ARCGIS_FIELDS,
        maxRecordCount=ARCGIS_MAX_RECORDS,
        supportedQueryFormats='JSON, geoJSON, PBF',
        capabilities='Query',
        advancedQueryCapabilities={
            'supportsPagination': True,
            'supportsOrderBy': True,
            'supportsQueryWithResultType': True,
        },  # fmt: skip
    )


@app.get(f'{ARCGIS_LAYER}/query')
def arcgis_query(service: str) -> Response:
    denied = arcgis_denied(service)
    if denied is not None:
        return denied
    args = request.args
    try:
        rows = [(a, g) for a, g in ARCGIS_ROWS if arcgis_matches(a, args.get('where', '1 = 1'))]
    except ValueError as err:
        return arcgis_error(400, str(err))
    if args.get('objectIds'):
        wanted = {int(v) for v in args['objectIds'].split(',') if v.strip().isdigit()}
        rows = [(a, g) for a, g in rows if a['OBJECTID'] in wanted]
    if args.get('geometry') and args.get('geometryType') == 'esriGeometryEnvelope':
        minx, miny, maxx, maxy = (float(v) for v in args['geometry'].split(','))
        rows = [
            (a, g)
            for a, g in rows
            if minx <= g['coordinates'][0] <= maxx and miny <= g['coordinates'][1] <= maxy
        ]
    if args.get('returnCountOnly') == 'true':
        return jsonify(count=len(rows))
    for clause in reversed([c for c in (args.get('orderByFields') or '').split(',') if c.strip()]):
        field, _, order = clause.strip().partition(' ')
        if field in rows[0][0] if rows else False:
            rows.sort(key=lambda ag, f=field: ag[0][f], reverse=order.strip().upper() == 'DESC')
    offset = number('resultOffset', 0)
    count = number('resultRecordCount', ARCGIS_MAX_RECORDS, ARCGIS_MAX_RECORDS)
    page = rows[offset : offset + count]
    out_fields = [f.strip() for f in (args.get('outFields') or '*').split(',') if f.strip()]
    geometry = args.get('returnGeometry', 'true') != 'false'
    features = [
        {
            'type': 'Feature',
            'id': a['OBJECTID'],
            'geometry': g if geometry else None,
            'properties': {k: v for k, v in a.items() if '*' in out_fields or k in out_fields},
        }
        for a, g in page
    ]
    collection: dict[str, Any] = {'type': 'FeatureCollection', 'features': features}
    if offset + count < len(rows):
        collection['properties'] = {'exceededTransferLimit': True}
    if args.get('f', '').lower() != 'geojson':
        return arcgis_error(400, 'This mock answers queries as f=geoJSON only')
    return jsonify(collection)


@app.post('/arcgis/sharing/rest/generateToken')
def arcgis_token() -> Response:
    form = request.form
    if form.get('username') != USER or form.get('password') != PASSWORD:
        return arcgis_error(400, 'Unable to generate token. Invalid username or password.')
    expires = int((time.time() + 3600) * 1000)
    return jsonify(token=TOKEN, expires=expires, ssl=False)


# --- static vector tiles ({z}/{x}/{y}.pbf, as tippecanoe writes them) ----------------------


def tiles_dir() -> Path:
    """pygeoapi's own tile test data: mounted at /opt/tiles, or the repository's copy."""
    for candidate in (
        os.environ.get('MOCK_TILES_DIR'),
        '/opt/tiles',
        Path(__file__).resolve().parents[2] / 'vendor' / 'pygeoapi' / 'tests' / 'data' / 'tiles',
    ):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return Path('/nonexistent')


@app.get('/')
def index() -> Response:
    """MVT providers GET the server root before each tile."""
    return jsonify(service='provider-builder mock API')


@app.get('/tiles/<layer>/metadata.json')
def tile_metadata(layer: str) -> tuple[Response, int] | Response:
    path = tiles_dir() / layer / 'metadata.json'
    if not layer.replace('_', '').isalnum() or not path.is_file():
        return jsonify(message='not found'), 404
    return Response(path.read_bytes(), mimetype='application/json')


@app.get('/tiles/<layer>/<int:z>/<int:x>/<int:y>.<ext>')
def tile(layer: str, z: int, x: int, y: int, ext: str) -> tuple[Response, int] | Response:
    if not layer.replace('_', '').isalnum() or ext not in ('pbf', 'mvt'):
        return jsonify(message='not found'), 404
    path = tiles_dir() / layer / str(z) / str(x) / f'{y}.pbf'
    if not path.is_file():
        return jsonify(message='no tile'), 404
    return Response(path.read_bytes(), mimetype='application/vnd.mapbox-vector-tile')


# --- a WMS 1.3.0 / 1.1.1 GetMap, for pygeoapi's WMSFacade --------------------------------

WMS_LAYERS = ('wells', 'graticule')


def png(width: int, height: int, pixels: bytearray) -> bytes:
    """An RGBA PNG from raw pixels, without an image library."""
    stride = width * 4
    raw = b''.join(b'\x00' + bytes(pixels[y * stride : (y + 1) * stride]) for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return len(body).to_bytes(4, 'big') + tag + body + crc.to_bytes(4, 'big')

    header = width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + bytes([8, 6, 0, 0, 0])
    body = chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b'')
    return b'\x89PNG\r\n\x1a\n' + body


def wms_exception(message: str, code: str = 'InvalidParameterValue') -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ServiceExceptionReport version="1.3.0" xmlns="http://www.opengis.net/ogc">'
        f'<ServiceException code="{code}">{message}</ServiceException></ServiceExceptionReport>'
    )
    return Response(body, mimetype='text/xml')


def to_lonlat(x: float, y: float, crs: str) -> tuple[float, float]:
    if crs in ('EPSG:3857', 'EPSG:900913'):
        lon = x / 6378137 * 180 / math.pi
        lat = math.degrees(2 * math.atan(math.exp(y / 6378137)) - math.pi / 2)
        return lon, lat
    return x, y


@app.get('/wms')
def wms() -> Response:
    args = {k.lower(): v for k, v in request.args.items()}
    if args.get('service', '').upper() != 'WMS' or args.get('request', '').lower() != 'getmap':
        return wms_exception('Only GetMap is implemented', 'OperationNotSupported')
    version = args.get('version', '1.3.0')
    crs = (args.get('crs') if version == '1.3.0' else args.get('srs')) or 'CRS:84'
    layers = [name for name in args.get('layers', '').split(',') if name]
    if not layers or any(name not in WMS_LAYERS for name in layers):
        return wms_exception(f'Unknown layer: {args.get("layers")}', 'LayerNotDefined')
    if args.get('format') != 'image/png':
        return wms_exception(f'Unsupported format: {args.get("format")}', 'InvalidFormat')
    try:
        width, height = int(args['width']), int(args['height'])
        bbox = [float(v) for v in args['bbox'].split(',')]
    except (KeyError, ValueError):
        return wms_exception('width, height and bbox are required')
    if not (0 < width <= 4096 and 0 < height <= 4096 and len(bbox) == 4):
        return wms_exception('Invalid width, height or bbox')
    if crs not in ('CRS:84', 'EPSG:4326', 'EPSG:3857', 'EPSG:900913'):
        return wms_exception(f'Unsupported CRS: {crs}', 'InvalidCRS')
    if version == '1.3.0' and crs == 'EPSG:4326':  # latitude first
        bbox = [bbox[1], bbox[0], bbox[3], bbox[2]]
    (minx, miny), (maxx, maxy) = to_lonlat(bbox[0], bbox[1], crs), to_lonlat(bbox[2], bbox[3], crs)
    transparent = args.get('transparent', 'FALSE').upper() == 'TRUE'
    pixels = bytearray(([0, 0, 0, 0] if transparent else [255, 255, 255, 255]) * width * height)

    def put(px: int, py: int, rgba: tuple[int, int, int, int]) -> None:
        if 0 <= px < width and 0 <= py < height:
            i = (py * width + px) * 4
            pixels[i : i + 4] = bytes(rgba)

    def project(lon: float, lat: float) -> tuple[int, int]:
        if crs in ('EPSG:3857', 'EPSG:900913'):  # draw in the map's own projection
            lat = max(-85.0, min(85.0, lat))
            fy = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            fy0 = math.log(math.tan(math.pi / 4 + math.radians(max(-85.0, miny)) / 2))
            fy1 = math.log(math.tan(math.pi / 4 + math.radians(min(85.0, maxy)) / 2))
            row = (fy1 - fy) / (fy1 - fy0)
        else:
            row = (maxy - lat) / (maxy - miny)
        return int((lon - minx) / (maxx - minx) * width), int(row * height)

    if 'graticule' in layers:
        grey = (150, 150, 150, 255)
        for lon in range(-180, 181, 10):
            for py in range(height):
                px, _ = project(lon, 0)
                put(px, py, grey)
        for lat in range(-80, 81, 10):
            _, py = project(0, lat)
            for px in range(width):
                put(px, py, grey)
    if 'wells' in layers:
        for r in WELLS:
            cx, cy = project(r['longitude'], r['latitude'])
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    put(cx + dx, cy + dy, (33, 102, 172, 255))
    return Response(png(width, height, pixels), mimetype='image/png')


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '8000')))
