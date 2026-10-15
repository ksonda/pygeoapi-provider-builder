"""Every catalog file is valid, cited, and complete enough for its examples."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from provider_builder.catalog import Catalog, compile_descriptor
from provider_builder.render import FormState, ResourceState, form_values_from_provider, render
from provider_builder.service import derived_image, derived_run

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'check_citations', REPO / 'tools' / 'check_citations.py'
)
assert spec and spec.loader
check_citations = importlib.util.module_from_spec(spec)
sys.modules['check_citations'] = check_citations
spec.loader.exec_module(check_citations)

HAND_WRITTEN_ORIGINS = {'core', 'plugin'}
#: Every hand-written descriptor in the repository's catalog, for parametrizing.
HAND_WRITTEN = sorted(
    d.id
    for d in Catalog([REPO / 'catalog']).descriptors.values()
    if d.raw.get('origin') in HAND_WRITTEN_ORIGINS
)


def test_catalog_files_validate(catalog: Catalog) -> None:
    assert catalog.errors == []
    assert {'CSV', 'GeoJSON', 'SQLiteGPKG', 'PostgreSQL'} <= set(catalog.descriptors)


def _citation_ok(source: str, key: str) -> tuple[bool, str]:
    m = check_citations.CITATION.match(source)
    if not m:
        return False, f'not a path:line citation: {source}'
    target = check_citations.resolve(m['repo'], m['path'])
    if target is None or not target.is_file():
        return False, f'missing file: {source}'
    lines = target.read_text(encoding='utf-8', errors='replace').splitlines()
    start, end = int(m['start']), int(m['end'] or m['start'])
    if not 1 <= start <= end <= len(lines):
        return False, f'line out of range: {source}'
    window = '\n'.join(lines[max(0, start - 4) : min(len(lines), end + 3)])
    last = key.rsplit('.', 1)[-1]
    return (last in window, f'`{last}` not within 3 lines of {source}')


@pytest.mark.parametrize('descriptor_id', HAND_WRITTEN)
def test_every_field_is_cited(catalog: Catalog, descriptor_id: str) -> None:
    d = catalog.get(descriptor_id)
    assert d.raw['origin'] in HAND_WRITTEN_ORIGINS
    problems = []
    for f in d.all_fields():
        sources = f.get('source')
        if not sources:
            problems.append(f'{f["key"]}: no source')
            continue
        for src in [sources] if isinstance(sources, str) else sources:
            ok, why = _citation_ok(src, f['key'])
            if not ok:
                problems.append(f'{f["key"]}: {why}')
    assert problems == []


def test_base_fields_are_cited(catalog: Catalog) -> None:
    assert set(catalog.bases) >= {'feature', 'coverage', 'edr', 'tile', 'map', 'record'}
    for base in catalog.bases.values():
        for f in base['fields']:
            ok, why = _citation_ok(f['source'], f['key'])
            assert ok, (base['id'], why)


def test_drift_ignores_are_cited(catalog: Catalog) -> None:
    raws = [*catalog.bases.values(), *(d.raw for d in catalog.descriptors.values())]
    ignores = [i for raw in raws for i in raw.get('drift_ignore', [])]
    assert ignores
    for i in ignores:
        ok, why = _citation_ok(i['source'], i['key'])
        assert ok, why


def test_examples_are_cited_and_exist(catalog: Catalog) -> None:
    for d in catalog.descriptors.values():
        if d.raw.get('origin') == 'core':  # some org plugins document no configuration at all
            assert d.raw.get('examples'), f'{d.id} has no example from the pygeoapi docs'
        for ex in d.raw.get('examples', []):
            m = check_citations.CITATION.match(ex['source'])
            assert m, ex['source']
            path = check_citations.resolve(m['repo'], m['path'])
            text = path.read_text(encoding='utf-8').splitlines()
            block = '\n'.join(text[int(m['start']) - 1 : int(m['end'] or m['start'])])
            assert f'name: {ex["provider"]["name"]}' in block, ex['source']


def _strip_defaults(
    provider: dict[str, Any], fields: dict[str, dict[str, Any]], prefix: str = ''
) -> dict[str, Any]:
    out = {}
    for k, v in provider.items():
        key = f'{prefix}{k}'
        f = fields.get(key)
        if f and 'default' in f and v == f['default']:
            continue
        if isinstance(v, dict) and not f:
            v = _strip_defaults(v, fields, key + '.')
        out[k] = v
    return out


@pytest.mark.parametrize('descriptor_id', HAND_WRITTEN)
def test_docs_examples_round_trip(catalog: Catalog, descriptor_id: str) -> None:
    """A docs example -> form state -> render gives the example back.

    The only allowed differences: defaults are omitted, and secrets become
    ${ENV} placeholders instead of values.
    """
    d = catalog.get(descriptor_id)
    for ex in d.raw.get('examples', []):
        variant = ex.get('variant')
        state = form_values_from_provider(d, ex['provider'], variant)
        assert state.extra == {}, f'{ex["id"]}: keys the descriptor does not know: {state.extra}'
        form = FormState(
            resource_id='example', resource=ResourceState(title='t'), providers=[state]
        )
        rendered = render(form, catalog).resource['providers'][0]
        fields = {f['key']: f for f in d.active_fields(variant)}
        expected = _strip_defaults(ex['provider'], fields)
        for key, f in fields.items():
            if f['type'] == 'secret':
                node = expected
                parts = key.split('.')
                for part in parts[:-1]:
                    node = node.get(part, {})
                if parts[-1] in node:
                    node[parts[-1]] = rendered_value(rendered, key)
                    assert rendered_value(rendered, key).startswith('${')
        assert rendered == expected


def rendered_value(provider: dict[str, Any], dotted: str) -> Any:
    node: Any = provider
    for part in dotted.split('.'):
        node = node[part]
    return node


def test_compiled_schema_shape(catalog: Catalog) -> None:
    compiled = compile_descriptor(catalog.get('PostgreSQL'))
    schema = compiled['json_schema']
    assert schema['properties']['$variant']['enum'] == ['fields', 'dsn']
    assert schema['required'] == ['table', 'id_field']
    by_variant = {
        c['if']['properties']['$variant']['const']: c['then']['required'] for c in schema['allOf']
    }
    assert by_variant == {
        'fields': ['data.host', 'data.dbname', 'data.user', 'data.password'],
        'dsn': ['data'],
    }
    connect = compiled['ui']['groups'][0]
    assert connect['id'] == 'connect'
    assert 'table' in connect['keys'] and 'options.pool_size' in connect['advanced']
    assert compiled['ui']['fields']['data.password']['widget'] == 'secret'


def test_hidden_fields_are_dropped(catalog: Catalog) -> None:
    keys = {f['key'] for f in catalog.get('CSV').all_fields()}
    assert 'editable' not in keys
    assert {'data', 'geometry.x_field', 'title_field', 'properties'} <= keys


def test_derived_images_only_add_packages(catalog: Catalog) -> None:
    """pip is held to the pinned image's versions, or numpy 2 breaks its rasterio (ADR-0027)."""
    core = derived_image(['pyarrow', 'geopandas'], None)
    assert core['packages'] == ['pyarrow', 'geopandas'] and core['upgrades'] == []
    assert core['dockerfile'].startswith('FROM geopython/pygeoapi:0.24.0\n')
    assert 'list --format=freeze > /tmp/image.txt' in core['dockerfile']
    assert '-c /tmp/image.txt pyarrow geopandas' in core['dockerfile']

    packages = {
        d.raw['package']['name']: d.raw['package']
        for d in catalog.descriptors.values()
        if d.raw.get('package')
    }
    plugin = derived_image(['pygeoapi_plugins'], packages['pygeoapi-plugins'])
    assert plugin['packages'] == ['pygeoapi-plugins==0.6.0'] and plugin['upgrades'] == ['shapely']
    assert "grep -viE '^(shapely)=='" in plugin['dockerfile']
    # make images-plugins builds the test image from the card's own lines.
    dockerfile = (REPO / 'docker' / 'sandbox-plugins' / 'Dockerfile').read_text()
    assert derived_run(plugin['packages'], plugin['upgrades']) in dockerfile
