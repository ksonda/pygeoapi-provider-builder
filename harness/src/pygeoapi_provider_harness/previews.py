"""Previews for the probe: coverage and map images, vector tile contents.

The sandbox image has numpy but neither Pillow nor mapbox_vector_tile, so PNGs
are written with zlib, and vector tiles are read with a small protobuf reader
that only looks at layer names, keys and geometry types.
"""

from __future__ import annotations

import base64
import gzip
import math
import struct
import zlib
from collections.abc import Iterator, Mapping
from typing import Any

#: Viridis at five stops, interpolated linearly: readable, and fine in grayscale.
RAMP = [
    (0.0, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.5, (33, 145, 140)),
    (0.75, (94, 201, 98)),
    (1.0, (253, 231, 37)),
]
MAX_PREVIEW_PX = 256
GEOMETRY_TYPES = {1: 'point', 2: 'line', 3: 'polygon'}
WEB_MERCATOR_LAT = 85.0511287798066


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def png(width: int, height: int, rgba: bytes) -> bytes:
    """An RGBA PNG: 8 bits per channel, no interlacing, filter 0 on every row."""
    stride = width * 4
    raw = b''.join(b'\x00' + rgba[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', crc)

    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', header)
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )


def _lut() -> Any:
    import numpy as np

    stops = np.array([s for s, _ in RAMP])
    colours = np.array([c for _, c in RAMP], dtype=float)
    at = np.linspace(0, 1, 256)
    return np.stack([np.interp(at, stops, colours[:, i]) for i in range(3)], axis=1).astype(
        'uint8'
    )


def _axis(axis: Mapping[str, Any]) -> tuple[float, float, int] | None:
    """(first, last, count) of a CoverageJSON domain axis."""
    if isinstance(axis.get('values'), list) and axis['values']:
        values = axis['values']
        try:
            return float(values[0]), float(values[-1]), len(values)
        except (TypeError, ValueError):
            return None
    if {'start', 'stop', 'num'} <= axis.keys():
        return float(axis['start']), float(axis['stop']), int(axis['num'])
    return None


def coverage_image(doc: Any, parameter: str | None = None) -> dict[str, Any] | None:
    """One 2-D slice of a CoverageJSON grid as a north-up PNG, with its value range.

    Each value goes where the domain's ``x`` and ``y`` axes say it is, as a
    CoverageJSON client draws it, so a document whose axes disagree with its
    values looks wrong here too. Other axes (time, height) take their first
    value. Missing values are transparent.
    """
    import numpy as np

    if not isinstance(doc, Mapping) or doc.get('type') != 'Coverage':
        return None
    axes = (doc.get('domain') or {}).get('axes') or {}
    ranges = doc.get('ranges') or {}
    key = parameter if parameter in ranges else next(iter(ranges), None)
    if key is None:
        return None
    rng = ranges[key]
    names, shape = list(rng.get('axisNames') or []), list(rng.get('shape') or [])
    values = rng.get('values')
    if 'x' not in names or 'y' not in names or not isinstance(values, list):
        return None
    try:
        grid = np.array([np.nan if v is None else v for v in values], dtype=float).reshape(shape)
    except (TypeError, ValueError):
        return None
    index = tuple(slice(None) if n in ('x', 'y') else 0 for n in names)
    grid = grid[index]
    if [n for n in names if n in ('x', 'y')] == ['x', 'y']:
        grid = grid.T  # rows are y
    x, y = _axis(axes.get('x') or {}), _axis(axes.get('y') or {})
    if y and y[0] < y[1]:
        grid = grid[::-1]  # the first row drawn is the top (largest y)
    step = max(1, math.ceil(max(grid.shape) / MAX_PREVIEW_PX))
    grid = grid[::step, ::step]
    finite = np.isfinite(grid)
    if not finite.any():
        lo = hi = None
        rgba = np.zeros((*grid.shape, 4), dtype='uint8')
    else:
        lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
        scaled = np.zeros(grid.shape) if hi == lo else (grid - lo) / (hi - lo)
        idx = np.clip(np.nan_to_num(scaled) * 255, 0, 255).astype('uint8')
        rgba = np.zeros((*grid.shape, 4), dtype='uint8')
        rgba[..., :3] = _lut()[idx]
        rgba[..., 3] = np.where(finite, 255, 0)
    height, width = grid.shape
    unit = ((doc.get('parameters') or {}).get(key) or {}).get('unit') or {}
    symbol = unit.get('symbol')
    if isinstance(symbol, Mapping):
        symbol = symbol.get('value')
    bbox = None
    if x and y:
        bbox = [min(x[0], x[1]), min(y[0], y[1]), max(x[0], x[1]), max(y[0], y[1])]
    return {
        'mimetype': 'image/png',
        'data': b64(png(width, height, rgba.tobytes())),
        'width': width,
        'height': height,
        'parameter': key,
        'min': lo,
        'max': hi,
        'unit': symbol if isinstance(symbol, str) else None,
        'bbox': bbox,
        'ramp': [list(c) for _, c in RAMP],
    }


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG, GIF or JPEG, read from its header."""
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        width, height = struct.unpack('>II', data[16:24])
        return int(width), int(height)
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        width, height = struct.unpack('<HH', data[6:10])
        return int(width), int(height)
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            length = struct.unpack('>H', data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack('>HH', data[i + 5 : i + 9])
                return int(width), int(height)
            i += 2 + length
    return None


def image_mimetype(data: bytes) -> str | None:
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


# ------------------------------------------------------------------ vector tiles


def gunzip(data: bytes) -> bytes:
    """Tiles as served: tippecanoe and many services gzip them."""
    return gzip.decompress(data) if data[:2] == b'\x1f\x8b' else data


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError('varint too long')


def _fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        value: Any
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            if i + size > len(buf):
                raise ValueError('truncated field')
            value, i = buf[i : i + size], i + size
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        else:
            raise ValueError(f'unsupported wire type {wire}')
        yield number, wire, value


def mvt_layers(tile: bytes) -> list[dict[str, Any]]:
    """The layers of a Mapbox Vector Tile (spec 2.1): name, feature count, keys, geometry types.

    Raises ``ValueError`` (or ``IndexError``) when the bytes aren't a vector tile.
    """
    layers = []
    for number, wire, value in _fields(tile):
        if number != 3 or wire != 2:
            raise ValueError('not a vector tile: unexpected top-level field')
        name, count, keys, kinds = None, 0, [], set()
        for n, w, v in _fields(value):
            if n == 1 and w == 2:
                name = bytes(v).decode('utf-8', 'replace')
            elif n == 2 and w == 2:
                count += 1
                for fn, fw, fv in _fields(v):
                    if fn == 3 and fw == 0:
                        kinds.add(GEOMETRY_TYPES.get(fv, 'unknown'))
            elif n == 3 and w == 2:
                keys.append(bytes(v).decode('utf-8', 'replace'))
        if name is None:
            raise ValueError('not a vector tile: a layer has no name')
        layers.append(
            {'name': name, 'features': count, 'keys': keys, 'geometry_types': sorted(kinds)}
        )
    return layers


def merge_layers(found: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Layers across several tiles: features summed, keys and geometry types united."""
    merged: dict[str, dict[str, Any]] = {}
    for layers in found:
        for layer in layers:
            m = merged.setdefault(
                layer['name'],
                {'name': layer['name'], 'features': 0, 'keys': [], 'geometry_types': []},
            )
            m['features'] += layer['features']
            m['keys'] = list(dict.fromkeys(m['keys'] + layer['keys']))
            m['geometry_types'] = sorted(set(m['geometry_types']) | set(layer['geometry_types']))
    return list(merged.values())


def web_mercator_tiles(bbox: list[float] | None, z: int) -> Iterator[tuple[int, int]]:
    """(x, y) of the WebMercatorQuad tiles at zoom ``z`` that cover a CRS84 bbox."""
    n = 1 << z
    minx, miny, maxx, maxy = bbox if bbox and len(bbox) == 4 else (-180, -90, 180, 90)

    def col(lon: float) -> int:
        return min(n - 1, max(0, int((lon + 180) / 360 * n)))

    def row(lat: float) -> int:
        lat = max(-WEB_MERCATOR_LAT, min(WEB_MERCATOR_LAT, lat))
        rad = math.radians(lat)
        y = (1 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2 * n
        return min(n - 1, max(0, int(y)))

    for x in range(col(minx), col(maxx) + 1):
        for y in range(row(maxy), row(miny) + 1):
            yield x, y
