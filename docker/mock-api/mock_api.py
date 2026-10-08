"""A mock JSON API for Mode B tests and demos (PLAN.md §12, "Runtime engine").

120 synthetic wells (seeded, so every run is identical) behind one endpoint per
pagination style, auth type and failure the spec engine has to handle. Flask
because it's already in the pinned pygeoapi image: the dev stack runs this
file there with gunicorn, and tests serve it in-process.

Test credentials (override with environment variables): X-API-Key / api_key
``test-key``, bearer ``test-token``, basic ``reader`` / ``test-password``.
"""

from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
from datetime import UTC, datetime, timedelta
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


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '8000')))
