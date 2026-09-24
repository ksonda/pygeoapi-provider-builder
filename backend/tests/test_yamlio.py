from __future__ import annotations

import datetime as dt

import pytest
import yaml
from provider_builder.yamlio import SpliceError, append_resource, detect_mapping_indent, dump

RESOURCE = {
    'type': 'collection',
    'title': 'New',
    'description': 'd',
    'keywords': ['k'],
    'extents': {
        'spatial': {'bbox': [0, 0, 1, 1], 'crs': 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'}
    },
    'providers': [{'type': 'feature', 'name': 'CSV', 'data': '/data/x.csv'}],
}


@pytest.mark.parametrize(
    'value',
    [
        'yes',
        'No',
        'on',
        'off',
        'y',
        '123',
        '1.5',
        '1_000',
        '2020-01-01',
        '~',
        'null',
        'true',
        '0x1F',
        '1:20',
    ],
)
def test_yaml11_lookalikes_are_quoted(value: str) -> None:
    text = dump({'k': value})
    assert yaml.safe_load(text) == {'k': value}, text  # PyYAML = pygeoapi's loader


@pytest.mark.parametrize(
    'value', ['${DB_PASSWORD}', '${A}:${B}', '${PORT:-5432}', 'postgresql://${U}:${P}@db/x']
)
def test_placeholders_stay_plain(value: str) -> None:
    assert dump({'k': value}) == f'k: {value}\n'


def test_datetime_and_null() -> None:
    text = dump({'begin': dt.datetime(2000, 10, 30, 18, 24, 39, tzinfo=dt.UTC), 'end': None})
    assert text == 'begin: 2000-10-30T18:24:39Z\nend: null\n'


def test_bbox_is_flow_style() -> None:
    assert dump({'bbox': [-180, -90, 180, 90]}) == 'bbox: [-180, -90, 180, 90]\n'


BASE = """# header comment
server:
    url: http://localhost:5000   # inline comment
    bbox: [-180,-90,180,90]

resources:
    lakes:  # keep me
        type: collection
        end: null

# comment about logging
logging:
    level: ERROR
"""


def test_append_preserves_every_other_byte() -> None:
    out = append_resource(BASE, 'new', RESOURCE)
    head, tail = BASE.split('\n# comment about logging\n')
    assert out.startswith(head)
    assert out.endswith('\n# comment about logging\nlogging:\n    level: ERROR\n')
    parsed = yaml.safe_load(out)
    assert list(parsed['resources']) == ['lakes', 'new']
    assert parsed['resources']['new']['providers'][0]['data'] == '/data/x.csv'
    assert '    new:\n        type: collection\n' in out


def test_append_at_end_of_file() -> None:
    base = 'server:\n    url: x\nresources:\n    a:\n        type: collection\n'
    out = append_resource(base, 'b', RESOURCE)
    assert out.startswith(base)
    assert list(yaml.safe_load(out)['resources']) == ['a', 'b']


def test_append_to_empty_resources() -> None:
    out = append_resource('server:\n    url: x\nresources: {}\n', 'b', RESOURCE)
    assert list(yaml.safe_load(out)['resources']) == ['b']


def test_append_without_resources_key() -> None:
    out = append_resource('server:\n    url: x\n', 'b', RESOURCE)
    assert list(yaml.safe_load(out)['resources']) == ['b']


def test_two_space_indent_and_crlf() -> None:
    base = 'server:\r\n  url: x\r\nresources:\r\n  a:\r\n    type: collection\r\n'
    assert detect_mapping_indent(base) == 2
    out = append_resource(base, 'b', RESOURCE)
    assert out.startswith(base) and '\r\n  b:\r\n    type: collection\r\n' in out
    assert '\n' not in out.replace('\r\n', '')


def test_duplicate_id_is_refused() -> None:
    with pytest.raises(SpliceError, match='already has a resource'):
        append_resource(BASE, 'lakes', RESOURCE)
