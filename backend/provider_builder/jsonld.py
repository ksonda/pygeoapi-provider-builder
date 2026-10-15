"""JSON-LD templates for items (Geoconnex options, PLAN.md §6.1 step 6).

pygeoapi 0.24 renders an item's JSON-LD with ``collections/items/item.jsonld``
from the collection's ``templates.path`` (``pygeoapi/linked_data.py:255``). The
legacy ``template:`` key of the Internet of Water fork is never written (notes §8).

The library holds fixed versions of the Geoconnex examples
(pygeoapi-geoconnex-examples:jsonld/hydrologic-location.jsonld and ref-gages.jsonld):
the ``sosa`` prefix is declared, the reference feature is a choice instead of
California, and values are escaped. Each generated template builds the item as
a Jinja dict and writes it with ``tojson``, so a quote in the data can't break
the JSON. Field names and constants reach the template only as JSON string
literals, which Jinja reads as plain strings; nothing here renders Jinja.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

HYF = 'https://www.opengis.net/def/schema/hy_features/hyf/'
GEOCONNEX_CONTEXT: dict[str, Any] = {
    'schema': 'https://schema.org/',
    'skos': 'http://www.w3.org/2004/02/skos/core#',
    'hyf': HYF,
    'gsp': 'http://www.opengis.net/ont/geosparql#',
    'sosa': 'http://www.w3.org/ns/sosa/',
    'name': 'schema:name',
    'description': 'schema:description',
    'provider': 'schema:provider',
    'sameAs': {'@id': 'schema:sameAs', '@type': '@id'},
    'related': 'skos:related',
    'geo': 'schema:geo',
    'geoWithin': {'@id': 'schema:geoWithin', '@type': '@id'},
    'url': {'@id': 'schema:url', '@type': '@id'},
}
#: What a field name or constant may contain: printable, no template delimiters.
SAFE = re.compile(r'^(?!.*(\{\{|\}\}|\{%|%\}|\{#|#\}))[^\x00-\x1f]{1,300}$')
URI = re.compile(r'^https?://[^\s"<>]+$')


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Slot:
    """One thing a template needs: a property of the items, or a constant."""

    id: str
    title: str
    kind: Literal['field', 'text', 'uri']
    help: str = ''
    required: bool = False


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    description: str
    slots: tuple[Slot, ...] = field(default_factory=tuple)


LIBRARY: dict[str, Template] = {
    t.id: t
    for t in (
        Template(
            'schema-place',
            'A schema.org place',
            'Each item as a schema:Place with a name, a description and a page, as '
            'search engines read it.',
            (
                Slot('name', 'Name', 'field', 'The property that names each item.', True),
                Slot('description', 'Description', 'field'),
                Slot('url', 'Web page', 'field', 'A property holding a URL about the item.'),
            ),
        ),
        Template(
            'geoconnex-location',
            'Geoconnex hydrologic location',
            'A monitoring location (HY_Features HY_HydroLocation) for the Geoconnex '
            'knowledge graph: names it, says who provides it, where it is, and links '
            'its data.',
            (
                Slot('name', 'Name', 'field', 'The property that names each location.', True),
                Slot('description', 'Description', 'field'),
                Slot('provider', 'Provider', 'text', 'The organization that runs the sites.'),
                Slot(
                    'data_url',
                    'Data page',
                    'field',
                    "A property holding the URL of the location's observations.",
                ),
                Slot(
                    'within',
                    'Within',
                    'uri',
                    'A Geoconnex reference feature containing every item, such as '
                    'https://geoconnex.us/ref/states/08.',
                ),
                Slot(
                    'within_field',
                    'Within (per item)',
                    'field',
                    'A property holding a reference feature URI, such as a HUC.',
                ),
            ),
        ),
        Template(
            'geoconnex-gage',
            'Geoconnex stream gage on a river',
            'A gage (HY_HydroLocation) placed on the river network: its mainstem, and '
            'its NHDPlus reach and measure, which Geoconnex uses to join it to rivers.',
            (
                Slot('name', 'Name', 'field', 'The property that names each gage.', True),
                Slot('description', 'Description', 'field'),
                Slot('provider', 'Provider', 'text'),
                Slot(
                    'mainstem',
                    'Mainstem',
                    'field',
                    'A property holding the Geoconnex mainstem URI '
                    '(https://geoconnex.us/ref/mainstems/…).',
                ),
                Slot('reachcode', 'NHDPlus reach code', 'field'),
                Slot('measure', 'Reach measure', 'field', 'The percent along the reach.'),
            ),
        ),
    )
}


def _lit(value: Any) -> str:
    """A Jinja literal for a JSON value (strings, lists and dicts of them)."""
    return json.dumps(value, ensure_ascii=True)


def _field(name: str) -> str:
    return f'data.get({_lit(name)})'


def _check(template: Template, values: dict[str, str]) -> dict[str, str]:
    known = {s.id: s for s in template.slots}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise TemplateError(f'{template.title} has no {", ".join(unknown)}')
    out = {}
    for slot in template.slots:
        value = str(values.get(slot.id) or '').strip()
        if not value:
            if slot.required:
                raise TemplateError(f'{slot.title} is required for {template.title}.')
            continue
        if not SAFE.match(value):
            raise TemplateError(f"{slot.title}: {value!r} has characters a template can't hold.")
        if slot.kind == 'uri' and not URI.match(value):
            raise TemplateError(f'{slot.title} must be an http(s) URI.')
        out[slot.id] = value
    return out


def generate(template_id: str, values: dict[str, str]) -> str:
    """The text of ``collections/items/item.jsonld`` for a library template."""
    template = LIBRARY.get(template_id)
    if template is None:
        raise TemplateError(f'No JSON-LD template {template_id!r}')
    v = _check(template, values)
    entries: list[tuple[str, str]] = [('@id', _field('@id'))]
    if template_id == 'schema-place':
        context: dict[str, Any] = {'schema': 'https://schema.org/', 'name': 'schema:name',
                                   'description': 'schema:description',
                                   'url': {'@id': 'schema:url', '@type': '@id'},
                                   'geo': 'schema:geo',
                                   'gsp': 'http://www.opengis.net/ont/geosparql#'}  # fmt: skip
        entries.append(('@type', _lit('schema:Place')))
    else:
        context = GEOCONNEX_CONTEXT
        entries.append(('@type', _lit([f'{HYF}HY_HydrometricFeature', f'{HYF}HY_HydroLocation'])))
        entries.append(('hyf:HY_HydroLocationType', _lit('hydrometricStation')))
    entries.append(('name', _field(v['name'])))
    if 'description' in v:
        entries.append(('description', _field(v['description'])))
    if 'url' in v:
        entries.append(('url', _field(v['url'])))
    if 'provider' in v:
        entries.append(('provider', _lit({'@type': 'schema:Organization', 'name': v['provider']})))
    if 'data_url' in v:
        collection = {'@type': 'sosa:ObservationCollection', 'url': '@@'}
        entries.append(
            ('sosa:isFeatureOfInterestOf',
             _lit(collection).replace('"@@"', _field(v['data_url']))),
        )  # fmt: skip
    if 'within' in v and 'within_field' in v:
        entries.append(('geoWithin', f'[{_lit(v["within"])}, {_field(v["within_field"])}]'))
    elif 'within' in v:
        entries.append(('geoWithin', _lit(v['within'])))
    elif 'within_field' in v:
        entries.append(('geoWithin', _field(v['within_field'])))
    entries.append(('geo', _field('schema:geo')))
    entries.append(('gsp:hasGeometry', _field('gsp:hasGeometry')))
    lines = [f'{{#- {template.title}: generated by pygeoapi Provider Builder. -#}}']
    positions = []
    if 'reachcode' in v:
        distance = {'hyf:HY_DistanceFromReferent': {'hyf:interpolative': '@@measure'}}
        reach = {
            'hyf:HY_IndirectPosition': {
                'hyf:linearElement': '@@reach',
                'hyf:distanceExpression': distance,
                'hyf:distanceDescription': {'hyf:HY_DistanceDescription': 'upstream'},
            }
        }
        text = (
            _lit(reach)
            .replace(
                '"@@reach"',
                f'"https://geoconnex.us/nhdplusv2/reachcode/" ~ {_field(v["reachcode"])}',
            )
            .replace('"@@measure"', _field(v['measure']) if 'measure' in v else 'none')
        )
        positions.append((v['reachcode'], text))
    if 'mainstem' in v:
        positions.append(
            (v['mainstem'], _lit({'hyf:HY_IndirectPosition': {'hyf:linearElement': '@@'}}).replace(
                '"@@"', _field(v['mainstem'])))
        )  # fmt: skip
    if positions:
        lines.append('{%- set positions = [] -%}')
        for name, text in positions:
            append = f'{{%- set _ = positions.append({text}) -%}}'
            lines.append(f'{{%- if {_field(name)} -%}}{append}{{%- endif -%}}')
        entries.append(('hyf:referencedPosition', 'positions'))
    body = ',\n  '.join(f'{_lit(k)}: {expr}' for k, expr in entries)
    lines.append(
        '{%- set item = {\n  "@context": (data.get("@context") or []) + ['
        + _lit(context)
        + '],\n  '
        + body
        + '\n} -%}'
    )
    lines.append('{{ item | tojson(indent=2) }}')
    return '\n'.join(lines) + '\n'


def library() -> list[dict[str, Any]]:
    return [
        {
            'id': t.id,
            'title': t.title,
            'description': t.description,
            'slots': [
                {
                    'id': s.id,
                    'title': s.title,
                    'kind': s.kind,
                    'help': s.help,
                    'required': s.required,
                }
                for s in t.slots
            ],
        }
        for t in LIBRARY.values()
    ]
