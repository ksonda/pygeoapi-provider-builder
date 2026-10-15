"""The JSON-LD template library (Geoconnex options): valid JSON whatever the data holds."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jinja2 import Environment
from provider_builder.jsonld import LIBRARY, TemplateError, generate, library

#: What pygeoapi passes an item template (pygeoapi/linked_data.py:205-259).
DATA: dict[str, Any] = {
    '@context': [{'schema': 'https://schema.org/', 'type': '@type'}],
    '@id': 'https://geoconnex.us/demo/gages/1',
    'NAME': 'Gage "A" & <B>',
    'DESC': None,
    'URL': 'https://example.org/data/1',
    'HUC': 'https://geoconnex.us/ref/hu08/10190004',
    'MS': 'https://geoconnex.us/ref/mainstems/29559',
    'RC': '10190004000123',
    'M': 42.5,
    'schema:geo': {'@type': 'schema:GeoCoordinates', 'schema:latitude': 40},
    'gsp:hasGeometry': {'gsp:asWKT': 'POINT (-105 40)'},
}
VALUES = {
    'schema-place': {'name': 'NAME', 'description': 'DESC', 'url': 'URL'},
    'geoconnex-location': {
        'name': 'NAME',
        'description': 'DESC',
        'provider': 'Colorado "DWR"',
        'data_url': 'URL',
        'within': 'https://geoconnex.us/ref/states/08',
        'within_field': 'HUC',
    },
    'geoconnex-gage': {'name': 'NAME', 'mainstem': 'MS', 'reachcode': 'RC', 'measure': 'M'},
}


def render(text: str, data: dict[str, Any]) -> Any:
    """As pygeoapi does: a plain Jinja environment (pygeoapi/util.py:488)."""
    return json.loads(Environment().from_string(text).render(data=data))


@pytest.mark.parametrize('template_id', sorted(LIBRARY))
def test_every_template_renders_valid_json_ld(template_id: str) -> None:
    doc = render(generate(template_id, VALUES[template_id]), DATA)
    assert doc['@id'] == DATA['@id']
    assert doc['name'] == 'Gage "A" & <B>'  # quotes and markup survive, escaped
    assert doc['@context'][0] == DATA['@context'][0]  # pygeoapi's context comes first
    assert doc['gsp:hasGeometry'] == DATA['gsp:hasGeometry']


def test_the_geoconnex_location_links_its_data_and_reference_features() -> None:
    doc = render(generate('geoconnex-location', VALUES['geoconnex-location']), DATA)
    assert 'sosa' in doc['@context'][-1]  # the example template forgot to declare it
    assert doc['geoWithin'] == ['https://geoconnex.us/ref/states/08', DATA['HUC']]
    assert doc['sosa:isFeatureOfInterestOf']['url'] == DATA['URL']
    assert doc['provider'] == {'@type': 'schema:Organization', 'name': 'Colorado "DWR"'}


def test_the_gage_is_placed_on_its_reach_and_mainstem() -> None:
    doc = render(generate('geoconnex-gage', VALUES['geoconnex-gage']), DATA)
    reach, mainstem = doc['hyf:referencedPosition']
    position = reach['hyf:HY_IndirectPosition']
    assert position['hyf:linearElement'].endswith('/reachcode/10190004000123')
    assert position['hyf:distanceExpression']['hyf:HY_DistanceFromReferent'] == {
        'hyf:interpolative': 42.5
    }
    assert mainstem['hyf:HY_IndirectPosition']['hyf:linearElement'] == DATA['MS']
    no_reach = render(generate('geoconnex-gage', VALUES['geoconnex-gage']), DATA | {'RC': None})
    assert len(no_reach['hyf:referencedPosition']) == 1  # an item without a reach skips it


@pytest.mark.parametrize(
    ('values', 'message'),
    [
        ({}, 'Name is required'),
        ({'name': 'a}}b'}, "characters a template can't hold"),
        ({'name': 'NAME', 'provider': '{% include "x" %}'}, "characters a template can't hold"),
        ({'name': 'NAME', 'within': 'geoconnex.us/ref'}, r'http\(s\) URI'),
        ({'name': 'NAME', 'nope': 'x'}, 'has no nope'),
    ],
)
def test_unsafe_or_incomplete_choices_are_refused(values: dict[str, str], message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        generate('geoconnex-location', values)


def test_the_library_lists_slots_and_never_the_legacy_template_key() -> None:
    entries = library()
    assert {e['id'] for e in entries} == set(LIBRARY)
    assert all(s['kind'] in ('field', 'text', 'uri') for e in entries for s in e['slots'])
    assert 'template:' not in generate('schema-place', {'name': 'NAME'})
