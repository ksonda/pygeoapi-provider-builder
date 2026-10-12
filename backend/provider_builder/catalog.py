"""Provider descriptors: load, validate, merge with their base, compile.

A descriptor compiles to a JSON Schema over *flat* form values (dotted keys)
and a UI schema. The frontend renders from the UI schema and validates with
the JSON Schema; the backend validates the same JSON Schema (PLAN.md §5.3).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / 'catalog' / 'schema' / 'descriptor.schema.json'
VARIANT_KEY = '$variant'
ENV_NAME_PATTERN = '^[A-Za-z_][A-Za-z0-9_]*$'


class CatalogError(ValueError):
    pass


@dataclass
class Descriptor:
    id: str
    raw: dict[str, Any]
    path: Path
    fields: list[dict[str, Any]]  # merged with base, ordered, no variants
    variants: list[dict[str, Any]] = field(default_factory=list)

    @property
    def plugin(self) -> str:
        return str(self.raw['plugin'])

    @property
    def provider_type(self) -> str:
        return str(self.raw['provider_type'])

    def active_fields(self, variant: str | None) -> list[dict[str, Any]]:
        """Fields in form order for a variant: variant fields first, then the rest."""
        vfields: list[dict[str, Any]] = []
        if self.variants:
            chosen = variant or self.variants[0]['id']
            for v in self.variants:
                if v['id'] == chosen:
                    vfields = v['fields']
                    break
            else:
                raise CatalogError(f'{self.id} has no variant {chosen!r}')
        return [*vfields, *self.fields]

    def all_fields(self) -> list[dict[str, Any]]:
        out = list(self.fields)
        for v in self.variants:
            out.extend(v['fields'])
        return out

    def field(self, key: str, variant: str | None = None) -> dict[str, Any] | None:
        for f in self.active_fields(variant):
            if f['key'] == key:
                return f
        return None


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise CatalogError(f'{path}: not a mapping')
    return data


class Catalog:
    """All descriptors from one or more catalog directories."""

    def __init__(self, dirs: list[Path], created: Path | None = None):
        schema = json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))
        self.validator = Draft202012Validator(schema)
        self.bases: dict[str, dict[str, Any]] = {}
        self.descriptors: dict[str, Descriptor] = {}
        self.errors: list[str] = []
        files: list[Path] = []
        for d in dirs:
            files += sorted(p for p in d.rglob('*.yaml') if 'schema' not in p.parts)
        if created is not None and created.is_dir():  # workspace/providers/<id>/descriptor.yaml
            files += sorted(created.glob('*/descriptor.yaml'))
        raws = []
        for path in files:
            try:
                raw = _load_yaml(path)
            except (yaml.YAMLError, CatalogError) as err:
                self.errors.append(f'{path}: {err}')
                continue
            problems = sorted(self.validator.iter_errors(raw), key=lambda e: list(e.path))
            if problems:
                for p in problems:
                    where = '/'.join(str(x) for x in p.absolute_path)
                    self.errors.append(f'{path}: {where}: {p.message}')
                continue
            if raw.get('base'):
                self.bases[raw['provider_type']] = raw
            else:
                raws.append((path, raw))
        for path, raw in raws:
            if raw['id'] in self.descriptors:
                self.errors.append(f'{path}: duplicate descriptor id {raw["id"]!r}')
                continue
            self.descriptors[raw['id']] = self._merge(path, raw)

    def _merge(self, path: Path, raw: dict[str, Any]) -> Descriptor:
        base = self.bases.get(raw['provider_type'], {})
        hidden = set(raw.get('hide', []))
        own = {f['key']: f for f in raw.get('fields', [])}
        variant_keys = {f['key'] for v in raw.get('variants', []) for f in v['fields']}
        merged: list[dict[str, Any]] = []
        for f in raw.get('fields', []):
            base_f = next((b for b in base.get('fields', []) if b['key'] == f['key']), None)
            merged.append({**(base_f or {}), **f})
        for b in base.get('fields', []):
            if b['key'] in own or b['key'] in hidden or b['key'] in variant_keys:
                continue
            merged.append(dict(b))
        # Connection fields keep the provider's order; map-step fields keep the
        # base order, so every provider's "Map fields" step reads the same way.
        base_index = {b['key']: i for i, b in enumerate(base.get('fields', []))}
        position = {f['key']: i for i, f in enumerate(merged)}

        def order(f: dict[str, Any]) -> tuple[int, int]:
            if f.get('step', 'connect') == 'connect':
                return (0, position[f['key']])
            return (1, base_index.get(f['key'], len(base_index) + position[f['key']]))

        merged.sort(key=order)
        return Descriptor(
            id=raw['id'],
            raw=raw,
            path=path,
            fields=merged,
            variants=copy.deepcopy(raw.get('variants', [])),
        )

    def get(self, descriptor_id: str) -> Descriptor:
        try:
            return self.descriptors[descriptor_id]
        except KeyError:
            raise CatalogError(f'Unknown provider descriptor {descriptor_id!r}') from None

    def by_plugin(self, name: str) -> Descriptor | None:
        """The descriptor for a provider ``name``: a short name or its dotted path."""
        from pygeoapi.plugin import PLUGINS

        names = {name}
        for short, dotted in PLUGINS['provider'].items():
            if name in (short, dotted):
                names |= {short, dotted}
        for d in self.descriptors.values():
            if d.plugin in names:
                return d
        return None


# ------------------------------------------------------------------------ compile

_STRINGISH = {'string', 'text', 'path', 'url', 'crs', 'field', 'datetime'}


def field_schema(f: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema for one flat form value."""
    t = f['type']
    s: dict[str, Any]
    if t in _STRINGISH:
        s = {'type': 'string', 'minLength': 1}
        if t == 'url':
            s['format'] = 'uri'
        if t == 'datetime':
            s['format'] = 'date-time'
    elif t == 'secret':
        # The form holds the *name* of the environment variable, never the value.
        s = {'type': 'string', 'pattern': ENV_NAME_PATTERN}
    elif t == 'integer':
        s = {'type': 'integer'}
    elif t == 'number':
        s = {'type': 'number'}
    elif t == 'boolean':
        s = {'type': 'boolean'}
    elif t == 'enum':
        s = {'enum': f.get('choices', [])}
    elif t in ('string[]', 'crs[]', 'field[]'):
        s = {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'uniqueItems': True}
        if t == 'field[]':
            s['minItems'] = 1
    elif t == 'number[]':
        s = {'type': 'array', 'items': {'type': 'number'}}
    elif t == 'bbox':
        s = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 6}
    elif t == 'object':
        s = {'type': 'object'}
    elif t == 'yaml':
        s = {'type': 'string'}
    else:  # pragma: no cover - the descriptor schema restricts types
        raise CatalogError(f'unknown field type {t}')
    for src, dst in (('min', 'minimum'), ('max', 'maximum'), ('pattern', 'pattern')):
        if src in f and 'type' in s and s['type'] in ('integer', 'number', 'string'):
            s[dst] = f[src]
    if 'title' in f:
        s['title'] = f['title']
    if 'default' in f:
        s['default'] = f['default']
    return s


def compile_descriptor(d: Descriptor) -> dict[str, Any]:
    """The compiled form: JSON Schema, UI schema and card metadata."""
    fields = d.all_fields()
    properties = {f['key']: field_schema(f) for f in fields}
    schema: dict[str, Any] = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'properties': properties,
        'required': [f['key'] for f in d.fields if f.get('required')],
    }
    if d.variants:
        schema['properties'][VARIANT_KEY] = {'enum': [v['id'] for v in d.variants]}
        schema['allOf'] = [
            {
                'if': {'properties': {VARIANT_KEY: {'const': v['id']}}},
                'then': {'required': [f['key'] for f in v['fields'] if f.get('required')]},
            }
            for v in d.variants
        ]
    ui_fields = {f['key']: _ui_field(f) for f in fields}
    groups = []
    for step, title in (('connect', 'Connection'), ('map', 'Fields and output')):
        keys = [f['key'] for f in d.fields if f.get('step', 'connect') == step]
        groups.append(
            {
                'id': step,
                'title': title,
                'keys': [k for k in keys if not ui_fields[k]['advanced']],
                'advanced': [k for k in keys if ui_fields[k]['advanced']],
            }
        )
    caps = d.raw.get('capabilities', {})
    if not caps.get('transactions'):
        for g in groups:
            g['advanced'] = [k for k in g['advanced'] if k != 'editable']
    return {
        'id': d.id,
        'json_schema': schema,
        'ui': {
            'groups': groups,
            'variants': [
                {
                    'id': v['id'],
                    'title': v['title'],
                    'help': v.get('help'),
                    'keys': [f['key'] for f in v['fields']],
                }
                for v in d.variants
            ],
            'fields': ui_fields,
        },
    }


def _ui_field(f: dict[str, Any]) -> dict[str, Any]:
    widget = {
        'string': 'text',
        'text': 'textarea',
        'integer': 'number',
        'number': 'number',
        'boolean': 'checkbox',
        'enum': 'select',
        'datetime': 'datetime',
        'secret': 'secret',
        'path': 'path',
        'url': 'url',
        'crs': 'crs',
        'crs[]': 'crs-list',
        'bbox': 'bbox',
        'field': 'field',
        'field[]': 'field-list',
        'string[]': 'string-list',
        'number[]': 'number-list',
        'object': 'key-value',
        'yaml': 'yaml',
    }[f['type']]
    return {
        'key': f['key'],
        'type': f['type'],
        'widget': widget,
        'title': f.get('title', f['key']),
        'help': f.get('help'),
        'required': bool(f.get('required')),
        'default': f.get('default'),
        'suggest': f.get('suggest'),
        'choices': f.get('choices'),
        'choices_from': f.get('choices_from'),
        'field_types': f.get('field_types'),
        'extensions': f.get('extensions'),
        'advanced': bool(f.get('advanced')),
        'unit': f.get('unit'),
        'min': f.get('min'),
        'max': f.get('max'),
        'env_name': f.get('env_name'),
        'geoconnex': bool(f.get('geoconnex')),
        'source': f.get('source'),
        'docs': f.get('docs'),
    }


def card(d: Descriptor) -> dict[str, Any]:
    """Catalog card metadata (PLAN.md §6.1 step 2)."""
    raw = d.raw
    return {
        'id': d.id,
        'plugin': d.plugin,
        'provider_type': d.provider_type,
        'title': raw['title'],
        'summary': raw.get('summary', ''),
        'ogc_api': raw.get('ogc_api', []),
        'origin': raw.get('origin', 'core'),
        'docs': raw.get('docs'),
        'requires': raw.get('requires', []),
        'capabilities': raw.get('capabilities', {}),
        'notes': raw.get('notes', []),
        'verdict': raw.get('verdict'),
        'version': raw.get('version'),
        'env': raw.get('env', []),
    }
