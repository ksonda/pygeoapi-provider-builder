"""Golden-file tests: form state -> YAML, for each Tier-1 provider (PLAN.md §12)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
import yaml
from forms import CASES, csv_form, postgres_form
from provider_builder.catalog import Catalog
from provider_builder.render import FormState, env_name, render
from pygeoapi.util import yaml_load

GOLDEN = Path(__file__).parent / 'golden'
UPDATE = os.environ.get('UPDATE_GOLDEN') == '1'


@pytest.mark.parametrize('name', sorted(CASES))
def test_golden(catalog: Catalog, name: str) -> None:
    form = FormState.model_validate(CASES[name]())
    result = render(form, catalog)
    golden = GOLDEN / f'{name}.yaml'
    if UPDATE or not golden.exists():
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(result.yaml, encoding='utf-8')
    assert result.yaml == golden.read_text(encoding='utf-8')


@pytest.mark.parametrize('name', sorted(CASES))
def test_output_loads_in_pygeoapi(
    catalog: Catalog, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pygeoapi's own loader reads our YAML back to the dict we rendered."""
    form = FormState.model_validate(CASES[name]())
    result = render(form, catalog)
    for env in result.env:
        monkeypatch.setenv(env.name, 'secret-value')
    loaded = yaml_load(io.StringIO(result.yaml))
    expected = json.loads(
        json.dumps(result.resource, default=str)
        .replace('${PYGEOAPI_WATERWAYS_DB_PASSWORD}', 'secret-value')
        .replace('${PYGEOAPI_DRAINS_DB_DSN}', 'secret-value')
    )
    got = json.loads(json.dumps(loaded[form.resource_id], default=str))
    assert got == expected


def test_secrets_are_placeholders_only(catalog: Catalog) -> None:
    result = render(FormState.model_validate(postgres_form()), catalog)
    provider = result.resource['providers'][0]
    assert provider['data']['password'] == '${PYGEOAPI_WATERWAYS_DB_PASSWORD}'
    assert [e.name for e in result.env] == ['PYGEOAPI_WATERWAYS_DB_PASSWORD']
    assert result.env[0].secret
    # plain scalar: never quoted, so pygeoapi substitutes it (notes §2)
    assert 'password: ${PYGEOAPI_WATERWAYS_DB_PASSWORD}\n' in result.yaml


def test_defaults_are_omitted(catalog: Catalog) -> None:
    form = postgres_form()
    form['providers'][0]['values']['data.port'] = 5432  # pygeoapi's default
    form['providers'][0]['values']['count'] = True  # pygeoapi's default
    provider = render(FormState.model_validate(form), catalog).resource['providers'][0]
    assert 'port' not in provider['data'] and 'count' not in provider


def test_canonical_key_order(catalog: Catalog) -> None:
    provider = render(FormState.model_validate(csv_form()), catalog).resource['providers'][0]
    assert list(provider)[:3] == ['type', 'name', 'data']


def test_extra_keys_are_kept(catalog: Catalog) -> None:
    form = csv_form()
    form['providers'][0]['extra'] = {'x-note': 'kept', 'geometry': {'z_field': 'elev'}}
    provider = render(FormState.model_validate(form), catalog).resource['providers'][0]
    assert provider['x-note'] == 'kept'
    assert provider['geometry'] == {'x_field': 'long', 'y_field': 'lat', 'z_field': 'elev'}


def test_temporal_always_has_both_keys(catalog: Catalog) -> None:
    form = csv_form()
    form['resource']['extents']['temporal'] = {'begin': '2000-10-30T18:24:39Z'}
    result = render(FormState.model_validate(form), catalog)
    temporal = yaml.safe_load(result.yaml)['obs']['extents']['temporal']
    assert set(temporal) == {'begin', 'end'} and temporal['end'] is None
    assert 'begin: 2000-10-30T18:24:39Z\n' in result.yaml and 'end: null\n' in result.yaml


@pytest.mark.parametrize(
    ('resource_id', 'field', 'expected'),
    [
        ('obs', {'key': 'data.password', 'env_name': 'DB_PASSWORD'}, 'PYGEOAPI_OBS_DB_PASSWORD'),
        ('poi-portugal', {'key': 'data'}, 'PYGEOAPI_POI_PORTUGAL_DATA'),
        ('a.b', {'key': 'options.api_key'}, 'PYGEOAPI_A_B_OPTIONS_API_KEY'),
    ],
)
def test_env_names(resource_id: str, field: dict, expected: str) -> None:
    assert env_name(resource_id, field) == expected
