"""Spec loading and validation, and the strict placeholder formatter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pygeoapi_spec_providers import formatter
from pygeoapi_spec_providers.spec import SpecError, check_text, load_spec, validate
from specs import wells

BASE = 'https://api.example.org'


def problems(doc: dict) -> list[tuple[str, str]]:
    return [(p.pointer, p.message) for p in validate(doc)]


def test_a_good_spec_validates_and_gets_defaults() -> None:
    assert validate(wells(BASE)) == []
    spec = load_spec(wells(BASE))
    assert spec.data['source']['timeout_s'] == 20
    assert spec.data['limits'] == {'max_records_scanned': 10_000, 'max_pages': 50}
    assert spec.hosts() == {'api.example.org'}
    assert spec.id_property == 'well_id'


def test_unknown_keys_are_rejected() -> None:
    doc = wells(BASE, colour='blue')
    assert any('colour' in m for _, m in problems(doc))


@pytest.mark.parametrize(
    ('change', 'pointer', 'fragment'),
    [
        ({'records': '$.data[?__import__("os")]'}, '/records', 'not a valid JSONPath'),
        ({'time_field': 'well_name'}, '/time_field', 'must be a datetime property'),
        ({'title_field': 'nope'}, '/title_field', 'not one of the properties'),
        ({'item': {'url': BASE + '/wells/{well}'}}, '/item/url', 'Unknown placeholder {well}'),
        (
            {'filters': {'bbox': {'param': 'bbox', 'format': '{minx},{miny},{maxx},{max_y}'}}},
            '/filters/bbox/format',
            'Unknown placeholder',
        ),
        (
            {'filters': {'sortby': {'param': 's', 'properties': ['nope']}}},
            '/filters/sortby/properties/0',
            'not one of the properties',
        ),
    ],
)
def test_semantic_rules(change: dict, pointer: str, fragment: str) -> None:
    found = problems(wells(BASE, **change))
    assert any(p == pointer and fragment in m for p, m in found), found


def test_credentials_belong_under_auth() -> None:
    doc = wells(BASE)
    doc['source'] = {
        'url': 'https://user:pw@api.example.org/v2/wells',
        'headers': {'Authorization': 'x'},
        'query': {'apikey': 'x'},
    }
    found = dict(problems(doc))
    assert found['/source/url'] == 'The URL contains credentials.'
    assert found['/source/headers/Authorization'] == "'Authorization' looks like a credential."
    assert found['/source/query/apikey'] == "'apikey' looks like a credential."


def test_datetime_formats_are_checked() -> None:
    doc = wells(BASE)
    doc['properties'][5]['format'] = '%Y-%m-%d %c'
    assert any('%c is not allowed' in m for _, m in problems(doc))
    doc['properties'][0]['format'] = 'iso8601'
    assert ('/properties/0/format', 'Only datetime properties take a format.') in problems(doc)


TEXT = """spec_version: 1
template: http-features
id: x
title: X
source:
  url: https://api.example.org/x
  auth:
    type: header
    name: X-API-Key
    value: {value}
records: $.items
item_id:
  from: id
geometry:
  type: none
properties:
  - name: id
    type: string
"""


def test_check_text_accepts_a_plain_placeholder() -> None:
    assert check_text(TEXT.format(value='${API_KEY}')) == []


def test_check_text_rejects_quoted_flow_and_literal_secrets() -> None:
    quoted = check_text(TEXT.format(value='"${API_KEY}"'))
    assert [p.pointer for p in quoted] == ['/source/auth/value']
    assert quoted[0].message == 'This placeholder is quoted, so pygeoapi leaves it as text.'
    flow = check_text(
        TEXT.replace(
            'auth:\n    type: header\n    name: X-API-Key\n    value: {value}',
            'auth: {{type: header, name: X-API-Key, value: ${{API_KEY}}}}',
        )
    )
    assert flow[0].message.startswith("The YAML doesn't parse")
    assert flow[0].hint and 'block style' in flow[0].hint
    literal = check_text(TEXT.format(value='abc123'))
    assert literal[0].message == 'Secrets must come from an environment variable.'
    with_default = check_text(TEXT.format(value='${API_KEY:-abc123}'))
    assert with_default[0].pointer == '/source/auth/value'


def test_files_load_through_pygeoapi_yaml_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('API_KEY', '123456')
    path = tmp_path / 'spec.yaml'
    path.write_text(TEXT.format(value='${API_KEY}'))
    spec = load_spec(str(path))
    assert spec.data['source']['auth']['value'] == 123456  # re-typed by yaml_load (notes §2)
    assert load_spec(str(path)) is spec  # cached by path, mtime and size
    with pytest.raises(SpecError, match="Can't read the spec file"):
        load_spec(str(tmp_path / 'missing.yaml'))


def test_package_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / 'demo_specs'
    pkg.mkdir()
    (pkg / '__init__.py').write_text('')
    (pkg / 'spec.yaml').write_text(TEXT.format(value='${API_KEY}'))
    monkeypatch.setenv('API_KEY', 'k')
    monkeypatch.syspath_prepend(str(tmp_path))
    assert load_spec('pkg:demo_specs/spec.yaml').id == 'x'
    with pytest.raises(SpecError, match='pkg:<module>/<resource>'):
        load_spec('pkg:demo_specs')


# --- formatter -------------------------------------------------------------------------------


def test_tokens_render_and_encode() -> None:
    template = formatter.parse('https://h/wells/{id}?x={{1}}', ('id',))
    assert template.names == {'id'}
    assert formatter.render_url(template, {'id': '../admin?drop=1#'}) == (
        'https://h/wells/..%2Fadmin%3Fdrop%3D1%23?x={1}'
    )


@pytest.mark.parametrize(
    ('text', 'fragment'),
    [
        ('{id.__class__}', 'Stray'),
        ('{id[0]}', 'Stray'),
        ('{other}', 'Unknown placeholder {other}'),
        ('{id:%Y}', 'only date and time values take a format'),
        ('{start:%c}', '%c is not allowed'),
        ('{start:%Y%}', 'lone %'),
        ('a { b', 'Stray'),
    ],
)
def test_bad_templates(text: str, fragment: str) -> None:
    with pytest.raises(
        formatter.FormatError, match=fragment.replace('{', r'\{').replace('[', r'\[')
    ):
        formatter.parse(text, ('id', 'start'), datetime_names=('start',))


def test_values_render_as_plain_text() -> None:
    moment = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
    template = formatter.parse(
        '{a}|{b}|{c}|{start}|{start:%Y%m%d}', ('a', 'b', 'c', 'start'), ('start',)
    )
    assert formatter.render(
        template, {'a': 10.0, 'b': -117.123456789, 'c': True, 'start': moment}
    ) == ('10|-117.123456789|true|2024-05-06T07:08:09Z|20240506')
