"""HTTPFeatureProvider against the mock API: pagination, filters, get, auth and failures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pygeoapi.provider.base import (
    ProviderConnectionError,
    ProviderInvalidQueryError,
    ProviderItemNotFoundError,
    ProviderQueryError,
)
from specs import all_ids, ids, provider, wells

PAGINATION = {
    'offset': (
        '/v2/wells',
        '$.data.items',
        {
            'style': 'offset',
            'offset_param': 'offset',
            'limit_param': 'limit',
            'max_page_size': 100,
        },
    ),
    'page': (
        '/paged/wells',
        '$.results',
        {
            'style': 'page',
            'max_page_size': 50,
            'page': {'page_param': 'page', 'page_start': 1, 'size_param': 'per_page'},
        },
    ),
    'next_link': (
        '/linked/wells',
        '$.items',
        {
            'style': 'next_link',
            'next_link': {'next': "$.links[?@.rel=='next'].href"},
        },
    ),
    'link_header': (
        '/header-linked/wells',
        '$[*]',
        {
            'style': 'next_link',
            'next_link': {'next': 'header:Link'},
        },
    ),
    'cursor': (
        '/cursor/wells',
        '$.items',
        {
            'style': 'cursor',
            'cursor': {'cursor_param': 'cursor', 'cursor_path': '$.next_cursor'},
        },
    ),
    'none': ('/all/wells', '$', {'style': 'none'}),
}


def styled(base: str, style: str, **extra: Any) -> dict[str, Any]:
    path, records, pagination = PAGINATION[style]
    return wells(base, path, records=records, pagination=dict(pagination), **extra)


def test_offset_window_and_total(mock_api: str) -> None:
    result = provider(wells(mock_api)).query(offset=10, limit=5)
    assert ids(result) == all_ids()[10:15]
    assert result['numberMatched'] == 120
    assert result['numberReturned'] == 5
    first = result['features'][0]
    assert first['geometry']['type'] == 'Point'
    assert set(first['properties']) == {
        'well_id',
        'well_name',
        'county',
        'depth_ft',
        'status',
        'last_measured',
    }
    assert first['properties']['last_measured'].endswith('Z')


def test_limit_beyond_the_page_cap_takes_several_pages(mock_api: str) -> None:
    result = provider(wells(mock_api)).query(offset=0, limit=150)
    assert ids(result) == all_ids()


def test_hits_reads_the_total_and_returns_no_features(mock_api: str) -> None:
    result = provider(wells(mock_api)).query(resulttype='hits')
    assert result['features'] == []
    assert result['numberMatched'] == 120


@pytest.mark.parametrize('style', list(PAGINATION))
def test_every_pagination_style_reads_every_record(mock_api: str, style: str) -> None:
    p = provider(styled(mock_api, style))
    assert ids(p.query(offset=0, limit=500)) == all_ids()
    assert ids(p.query(offset=37, limit=30)) == all_ids()[37:67]


@pytest.mark.parametrize('style', list(PAGINATION))
def test_local_filters_count_across_pages(mock_api: str, style: str) -> None:
    result = provider(styled(mock_api, style)).query(
        properties=[('county', 'Ada')], resulttype='hits'
    )
    expected = provider(wells(mock_api, filters={'properties': {'county': 'county'}}))
    assert (
        result['numberMatched']
        == expected.query(properties=[('county', 'Ada')], resulttype='hits')['numberMatched']
    )
    assert result['numberMatched'] > 0


QUERIES: list[dict[str, Any]] = [
    {'bbox': [-115.0, 43.0, -112.0, 46.0]},
    {'datetime_': '2021-01-01T00:00:00Z/2022-06-30T00:00:00Z'},
    {'datetime_': '../2020-06-01T00:00:00Z'},
    {'properties': [('county', 'Kootenai')]},
    {'q': 'bonner'},
    {'sortby': [{'property': 'depth_ft', 'order': '-'}]},
    {'sortby': [{'property': 'well_name', 'order': '+'}], 'properties': [('county', 'Canyon')]},
]
PUSHDOWN = {
    'bbox': {'param': 'bbox', 'format': '{minx},{miny},{maxx},{maxy}'},
    'datetime': {'start_param': 'from', 'end_param': 'to', 'format': 'iso8601'},
    'properties': {'county': 'county'},
    'sortby': {
        'param': 'sort',
        'format': '{property}:{order}',
        'properties': ['depth_ft', 'well_name'],
    },
    'q': {'param': 'search'},
}


@pytest.mark.parametrize('query', QUERIES)
def test_pushdown_and_local_filtering_agree(mock_api: str, query: dict[str, Any]) -> None:
    pushed = provider(wells(mock_api, filters=PUSHDOWN)).query(limit=500, **query)
    local = provider(wells(mock_api)).query(limit=500, **query)
    assert ids(pushed) == ids(local)
    assert pushed['numberMatched'] == local['numberMatched'] == len(local['features'])
    assert 0 < len(local['features']) < 120 or 'sortby' in query


def test_select_properties_and_skip_geometry(mock_api: str) -> None:
    result = provider(wells(mock_api)).query(
        limit=2, select_properties=['depth_ft'], skip_geometry=True
    )
    assert [f['properties'] for f in result['features']] == [
        {'depth_ft': f['properties']['depth_ft']} for f in result['features']
    ]
    assert all(f['geometry'] is None for f in result['features'])


def test_get_with_an_item_url(mock_api: str) -> None:
    p = provider(wells(mock_api, item={'url': mock_api + '/v2/wells/{id}', 'record': '$.data'}))
    assert p.get('W0042')['properties']['well_id'] == 'W0042'
    with pytest.raises(ProviderItemNotFoundError):
        p.get('W9999')


def test_get_by_scanning(mock_api: str) -> None:
    p = provider(wells(mock_api))
    assert p.get('W0099')['id'] == 'W0099'
    with pytest.raises(ProviderItemNotFoundError):
        p.get('nope')


def test_an_item_id_cannot_escape_the_url_template(mock_api: str) -> None:
    p = provider(wells(mock_api, item={'url': mock_api + '/v2/wells/{id}', 'record': '$.data'}))
    with pytest.raises(ProviderItemNotFoundError):
        p.get('../../health')  # percent-encoded into one path segment, so it's just an unknown id


def test_relative_paths_epoch_times_and_api_errors(mock_api: str) -> None:
    spec = wells(
        mock_api,
        '/esri/wells',
        source={
            'url': mock_api + '/esri/wells',
            'error_path': '$.error',
            'retries': {'max': 0},
            'auth': {'type': 'query', 'name': 'token', 'value': 'test-key'},
        },
        records='$.features',
        item_id={'from': '$.attributes.OBJECTID'},
        geometry={'type': 'point', 'x': '$.geometry.x', 'y': '$.geometry.y'},
        properties=[
            {'name': 'name', 'type': 'string', 'from': '$.attributes.NAME'},
            {
                'name': 'measured',
                'type': 'datetime',
                'from': '$.attributes.MEASURED',
                'format': 'epoch_ms',
            },
        ],
        time_field='measured',
        title_field='name',
        pagination={'style': 'none'},
    )
    feature = provider(spec).query(limit=1)['features'][0]
    assert feature['id'] == 1
    assert feature['properties']['measured'].startswith('20')
    bad = dict(
        spec, source=dict(spec['source'], auth={'type': 'query', 'name': 'token', 'value': 'nope'})
    )
    with pytest.raises(ProviderQueryError) as err:
        provider(bad).query()
    assert err.value.user_msg == 'The data source reported an error.'
    assert 'Invalid token.' in str(err.value)


def test_geojson_geometries(mock_api: str) -> None:
    spec = wells(
        mock_api,
        '/geojson/wells',
        records='$.items',
        item_id={'from': 'id'},
        geometry={'type': 'geojson', 'field': 'geometry'},
        properties=[{'name': 'name', 'type': 'string'}],
        time_field=None,
        title_field='name',
        pagination={'style': 'none'},
    )
    del spec['time_field']
    feature = provider(spec).query(limit=1)['features'][0]
    assert feature['geometry']['type'] == 'Point'
    assert len(feature['geometry']['coordinates']) == 2


def write_spec(tmp_path: Path, text: str) -> str:
    path = tmp_path / 'spec.yaml'
    path.write_text(text, encoding='utf-8')
    return str(path)


AUTH = {
    'header': (
        '/secure/header/wells',
        'type: header\n    name: X-API-Key\n    value: ${MOCK_KEY}',
        'test-key',
    ),
    'query': (
        '/secure/query/wells',
        'type: query\n    name: api_key\n    value: ${MOCK_KEY}',
        'test-key',
    ),
    'bearer': ('/secure/bearer/wells', 'type: bearer\n    value: ${MOCK_KEY}', 'test-token'),
    'basic': (
        '/secure/basic/wells',
        'type: basic\n    username: reader\n    password: ${MOCK_KEY}',
        'test-password',
    ),
}

SPEC_YAML = """spec_version: 1
template: http-features
id: secure-wells
title: Secure wells
source:
  url: {url}
  retries:
    max: 0
  auth:
    {auth}
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
pagination:
  style: offset
  offset_param: offset
  limit_param: limit
  total: $.meta.total
"""


@pytest.mark.parametrize('kind', list(AUTH))
def test_auth_from_environment_variables(
    mock_api: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path, auth, secret = AUTH[kind]
    spec = write_spec(tmp_path, SPEC_YAML.format(url=mock_api + path, auth=auth))
    monkeypatch.setenv('MOCK_KEY', secret)
    assert provider(spec).query(limit=1)['numberMatched'] == 120
    (tmp_path / 'other').mkdir()
    spec = write_spec(tmp_path / 'other', SPEC_YAML.format(url=mock_api + path, auth=auth))
    monkeypatch.setenv('MOCK_KEY', 'wrong-secret')
    with pytest.raises(ProviderConnectionError) as err:
        provider(spec).query(limit=1)
    assert err.value.user_msg == 'The data source rejected the credentials.'
    assert 'wrong-secret' not in str(err.value) and 'wrong-secret' not in repr(err.value.__cause__)


def test_a_missing_variable_is_a_clear_error(mock_api: str, tmp_path: Path) -> None:
    path, auth, _ = AUTH['header']
    spec = write_spec(
        tmp_path,
        SPEC_YAML.format(url=mock_api + path, auth=auth.replace('MOCK_KEY', 'UNSET_VAR_X')),
    )
    with pytest.raises(ProviderConnectionError) as err:
        provider(spec)
    assert 'UNSET_VAR_X' in str(err.value)


def test_retries_ride_out_a_429(mock_api: str) -> None:
    spec = wells(mock_api, '/flaky/wells')
    spec['source'] = {
        'url': mock_api + '/flaky/wells',
        'headers': {'X-Test-Run': 'retry-ok'},
        'retries': {'max': 2, 'backoff_s': 0},
    }
    assert len(provider(spec).query(limit=3)['features']) == 3
    spec['source'] = dict(spec['source'], headers={'X-Test-Run': 'no-retry'}, retries={'max': 0})
    with pytest.raises(ProviderQueryError) as err:
        provider(spec).query(limit=3)
    assert 'HTTP 429' in str(err.value)


def test_a_slow_source_times_out(mock_api: str) -> None:
    spec = wells(mock_api, '/slow/wells')
    spec['source'] = {
        'url': mock_api + '/slow/wells',
        'query': {'delay': 2},
        'timeout_s': 0.5,
        'retries': {'max': 0},
    }
    with pytest.raises(ProviderConnectionError) as err:
        provider(spec).query(limit=1)
    assert err.value.user_msg == 'The data source timed out.'


def test_the_size_cap(mock_api: str) -> None:
    spec = wells(mock_api, '/big/wells', records='$.items', pagination={'style': 'none'})
    spec['source'] = {
        'url': mock_api + '/big/wells',
        'max_response_bytes': 100_000,
        'retries': {'max': 0},
    }
    with pytest.raises(ProviderQueryError) as err:
        provider(spec).query(limit=1)
    assert err.value.user_msg == 'The data source sent a response larger than allowed.'


@pytest.mark.parametrize(
    ('path', 'message'),
    [
        ('/not-json/wells', "The data source didn't return JSON."),
        ('/error/wells', 'The data source returned HTTP 500.'),
    ],
)
def test_bad_responses(mock_api: str, path: str, message: str) -> None:
    with pytest.raises(ProviderQueryError) as err:
        provider(wells(mock_api, path)).query(limit=1)
    assert err.value.user_msg == message


def test_redirects_stay_on_declared_hosts(mock_api: str) -> None:
    assert provider(wells(mock_api, '/redirect/in')).query(limit=2)['numberReturned'] == 2
    with pytest.raises(ProviderConnectionError) as err:
        provider(wells(mock_api, '/redirect/out')).query(limit=1)
    assert err.value.user_msg == "The data source isn't allowed by this server's egress rules."
    assert 'example.org is not a host this spec declares' in str(err.value)


def test_cql_filters_are_rejected_not_ignored(mock_api: str) -> None:
    with pytest.raises(ProviderInvalidQueryError):
        provider(wells(mock_api)).query(filterq=object())


def test_fields_and_defaults_from_the_spec(mock_api: str) -> None:
    p = provider(wells(mock_api))
    assert p.get_fields() == p.fields
    assert p.fields['depth_ft'] == {'type': 'number'}
    assert p.fields['last_measured'] == {'type': 'string', 'format': 'date-time'}
    assert (p.id_field, p.title_field, p.time_field) == ('well_id', 'well_name', 'last_measured')
