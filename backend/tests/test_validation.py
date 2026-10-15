from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import yaml
from forms import BASE_CONFIG, csv_form, postgres_form
from provider_builder.catalog import Catalog
from provider_builder.render import FormState
from provider_builder.validation import (
    RuleContext,
    descriptor_findings,
    rule_findings,
    schema_findings,
)

BASE = yaml.safe_load(BASE_CONFIG.read_text())


def codes(findings: list[Any]) -> list[str]:
    return [f.code for f in findings]


# ------------------------------------------------------------------ layer 1


def test_valid_form_has_no_descriptor_findings(catalog: Catalog) -> None:
    assert descriptor_findings(FormState.model_validate(csv_form()), catalog) == []


def test_missing_required_fields(catalog: Catalog) -> None:
    form = csv_form()
    del form['providers'][0]['values']['geometry.x_field']
    findings = descriptor_findings(FormState.model_validate(form), catalog)
    assert [(f.pointer, f.message) for f in findings] == [
        ('/providers/0/values/geometry.x_field', 'X (longitude) column is required.')
    ]


def test_variant_required_fields(catalog: Catalog) -> None:
    form = postgres_form()
    form['providers'][0]['secrets'] = {}
    findings = descriptor_findings(FormState.model_validate(form), catalog)
    assert [f.pointer for f in findings] == ['/providers/0/values/data.password']


def test_wrong_types_and_ranges(catalog: Catalog) -> None:
    form = postgres_form()
    form['providers'][0]['values']['data.port'] = 70000
    findings = descriptor_findings(FormState.model_validate(form), catalog)
    assert [f.message for f in findings] == ['Port must be at most 65535.']


def test_unknown_key_gets_a_suggestion(catalog: Catalog) -> None:
    form = csv_form()
    form['providers'][0]['extra'] = {'id_feild': 'x'}
    (finding,) = descriptor_findings(FormState.model_validate(form), catalog)
    assert finding.severity == 'warning' and finding.hint == 'Did you mean `id_field`?'


# ------------------------------------------------------------------ layer 2


def merged(resource_id: str, resource: dict[str, Any]) -> dict[str, Any]:
    config = yaml.safe_load(BASE_CONFIG.read_text())
    config['resources'][resource_id] = resource
    return config


CRS84 = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
GOOD = {
    'type': 'collection',
    'title': 't',
    'description': 'd',
    'keywords': ['k'],
    'extents': {'spatial': {'bbox': [0, 0, 1, 1], 'crs': CRS84}},
    'providers': [{'type': 'feature', 'name': 'CSV', 'data': '/x.csv'}],
}


def test_base_config_is_schema_valid() -> None:
    assert schema_findings(BASE, 'lakes') == []


def test_missing_title_is_reported_plainly() -> None:
    """pygeoapi's own message here is "'collection' is not one of ['process']" (notes §6.1)."""
    resource = {k: v for k, v in GOOD.items() if k != 'title'}
    (f,) = schema_findings(merged('obs', resource), 'obs')
    assert f.severity == 'error'
    assert f.message == "resources.obs: 'title' is a required property"
    assert f.pointer == '/resource'


def test_provider_errors_point_at_the_form_field() -> None:
    resource = {**GOOD, 'providers': [{**GOOD['providers'][0], 'type': 'features'}]}
    (f,) = schema_findings(merged('obs', resource), 'obs')
    assert f.pointer == '/providers/0/values/type' and 'features' in f.message


def test_format_checks_pygeoapi_skips() -> None:
    resource = {**GOOD, 'providers': [{**GOOD['providers'][0], 'crs': ['not a uri']}]}
    (f,) = schema_findings(merged('obs', resource), 'obs')
    assert f.code == 'schema.format' and f.pointer == '/providers/0/values/crs'


def test_unknown_placeholder_downgrades_to_warning() -> None:
    config = merged('obs', GOOD)
    config['server']['bind']['port'] = '${PORT}'
    (f,) = schema_findings(config, 'obs')
    assert f.severity == 'warning' and 'environment variable' in f.message
    assert schema_findings(config, 'obs', env={'PORT': '80'}) == []


# ------------------------------------------------------------------ layer 3


def rules(resource: dict[str, Any], **kw: Any) -> list[str]:
    ctx = RuleContext(
        config=kw.pop('config', None) or merged('obs', resource),
        resource_id=kw.pop('resource_id', 'obs'),
        **kw,
    )
    return codes(rule_findings(ctx))


def test_good_resource_passes_rules() -> None:
    assert rules(GOOD) == []


@pytest.mark.parametrize(
    ('change', 'code'),
    [
        ({'type': None}, 'rule.resource-type'),
        (
            {'links': [{'type': 'text/html', 'rel': 'about', 'href': 'https://x'}]},
            'rule.link-title',
        ),
        (
            {
                'extents': {
                    'spatial': {'bbox': [0, 0, 1, 1], 'crs': CRS84},
                    'temporal': {'begin': dt.datetime(2000, 1, 1, tzinfo=dt.UTC)},
                }
            },
            'rule.temporal',
        ),
        (
            {
                'extents': {
                    'spatial': {'bbox': [0, 0, 1, 1], 'crs': CRS84},
                    'temporal': {'begin': '2000-01-01', 'end': None},
                }
            },
            'rule.temporal',
        ),
        ({'extents': {'spatial': {'bbox': [0, 0, 1, 1]}}}, 'rule.extent-crs'),
        (
            {'providers': [GOOD['providers'][0], GOOD['providers'][0]]},
            'rule.one-provider-per-type',
        ),
        (
            {
                'providers': [
                    {
                        'type': 'tile',
                        'name': 'MVT-proxy',
                        'data': 'x',
                        'format': {
                            'name': 'pbf',
                            'mimetype': 'application/vnd.mapbox-vector-tile',
                        },
                    },
                    GOOD['providers'][0],
                ]
            },
            'rule.default-provider',
        ),
        (
            {
                'providers': [
                    GOOD['providers'][0],
                    {'type': 'tile', 'name': 'MVT-proxy', 'data': 'x'},
                ]
            },
            'rule.format',
        ),
        ({'providers': [{**GOOD['providers'][0], 'crs': [4326]}]}, 'rule.crs-list'),
    ],
)
def test_rule_violations(change: dict[str, Any], code: str) -> None:
    resource = {**GOOD, **change}
    if change.get('type', 'x') is None:
        resource.pop('type')
    assert code in rules(resource)


VECTOR_TILES = {
    'type': 'tile',
    'name': 'MVT-tippecanoe',
    'data': '/data/tiles/lakes',
    'options': {'zoom': {'min': 0, 'max': 5}},
    'format': {'name': 'pbf', 'mimetype': 'application/vnd.mapbox-vector-tile'},
}


def test_a_tile_or_map_only_collection_is_allowed() -> None:
    """pygeoapi's own test config has a map-only collection; tiles alone get a warning."""
    tiles_only = rules({**GOOD, 'providers': [VECTOR_TILES]})
    assert 'rule.default-provider' not in tiles_only and 'rule.tile-items' in tiles_only
    wms = {
        'type': 'map',
        'name': 'WMSFacade',
        'data': 'https://wms.example.org/wms',
        'options': {'layer': 'x'},
        'format': {'name': 'png', 'mimetype': 'image/png'},
    }
    map_only = rules({**GOOD, 'providers': [wms]})
    assert not {'rule.default-provider', 'rule.tile-items'} & set(map_only)
    with_items = rules({**GOOD, 'providers': [GOOD['providers'][0], VECTOR_TILES]})
    assert not {'rule.default-provider', 'rule.tile-items'} & set(with_items)


@pytest.mark.parametrize('rid', ['my lakes', 'a?b', '-lead', 'items', ''])
def test_bad_ids(rid: str) -> None:
    config = merged(rid, GOOD)
    assert 'rule.resource-id' in rules(GOOD, config=config, resource_id=rid)


def test_server_level_rules() -> None:
    config = merged('obs', GOOD)
    del config['server']['languages']
    del config['metadata']['license']['url']
    del config['server']['limits']
    config['server']['admin'] = True
    config['resources']['obs']['providers'][0]['data'] = '${SECRET}'
    found = rules(GOOD, config=config)
    assert {
        'rule.server-languages',
        'rule.openapi-metadata',
        'rule.default-items',
        'rule.admin-secrets',
    } <= set(found)


def test_probe_based_rules() -> None:
    resource = {
        **GOOD,
        'providers': [
            {**GOOD['providers'][0], 'title_field': 'stn_id', 'properties': ['value', 'nope']}
        ],
    }
    fields = [
        {'id': {'type': 'string'}, 'stn_id': {'type': 'integer'}, 'value': {'type': 'number'}}
    ]
    found = rules(resource, probe_fields=fields, probe_samples=[{}])
    assert found.count('rule.fields-exist') == 1 and 'rule.title-field-string' in found


@pytest.mark.parametrize(
    ('values', 'severity'),
    [
        (['https://geoconnex.us/ref/gages/1', 'https://geoconnex.us/ref/gages/2'], None),
        (['https://example.org/1'], 'warning'),
        (['12345'], 'error'),
    ],
)
def test_geoconnex_uri_field(values: list[str], severity: str | None) -> None:
    resource = {**GOOD, 'providers': [{**GOOD['providers'][0], 'uri_field': 'uri'}]}
    ctx = RuleContext(
        config=merged('obs', resource),
        resource_id='obs',
        probe_fields=[{'uri': {'type': 'string'}}],
        probe_samples=[{'uri': {'values': values, 'nulls': 0}}],
    )
    found = [f for f in rule_findings(ctx) if f.code == 'rule.geoconnex-uri']
    assert [f.severity for f in found] == ([severity] if severity else [])
