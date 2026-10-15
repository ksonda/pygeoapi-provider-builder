"""The single renderer: form state -> resource dict -> YAML (PLAN.md §3).

Preview, validation, probe and export all call ``render``. It builds plain
dicts and never templates strings. Secrets are emitted only as ``${NAME}``.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from . import yamlio
from .catalog import VARIANT_KEY, Catalog, Descriptor

CRS84 = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
EMPTY: tuple[Any, ...] = (None, '', [], {})
# A provider the catalog doesn't describe, loaded from a config: kept as-is in ``extra``.
RAW_DESCRIPTOR = '_raw'


class SecretRef(BaseModel):
    env: str = Field(
        pattern=r'^[A-Za-z_][A-Za-z0-9_]*$',
        description='Environment variable the server must define',
    )


class ProviderState(BaseModel):
    descriptor: str
    variant: str | None = None
    values: dict[str, Any] = {}
    secrets: dict[str, SecretRef] = {}
    extra: dict[str, Any] = Field(default={}, description='Keys the descriptor does not know')


class Link(BaseModel):
    type: str
    rel: str
    title: str
    href: str
    hreflang: str | None = None
    length: int | None = None


class Spatial(BaseModel):
    bbox: list[int | float] = [-180, -90, 180, 90]
    crs: str = CRS84


class Temporal(BaseModel):
    begin: str | None = None
    end: str | None = None


class Extents(BaseModel):
    spatial: Spatial = Spatial()
    temporal: Temporal | None = None


class JsonLdChoice(BaseModel):
    """A JSON-LD template from the library and what fills its slots (jsonld.py)."""

    id: str
    slots: dict[str, str] = {}


class ResourceState(BaseModel):
    #: stac-collection for STAC providers (FileSystem, Hateoas, Azure): render sets it.
    type: Literal['collection', 'stac-collection'] = 'collection'
    title: str | dict[str, str] = ''
    description: str | dict[str, str] = ''
    keywords: list[str] | dict[str, list[str]] = []
    links: list[Link] = []
    extents: Extents = Extents()
    linked_data_context: list[dict[str, Any]] | None = None
    templates_path: str | None = None
    #: The item JSON-LD template to write to templates_path (Geoconnex options).
    jsonld_template: JsonLdChoice | None = None


class FormState(BaseModel):
    form_version: Literal[1] = 1
    resource_id: str
    resource: ResourceState = ResourceState()
    providers: list[ProviderState] = []


class EnvVar(BaseModel):
    name: str
    used_by: list[str]
    secret: bool


class RenderResult(BaseModel):
    resource_id: str
    resource: dict[str, Any]
    yaml: str
    env: list[EnvVar]
    warnings: list[str]


# ---------------------------------------------------------------------- env names


def env_name(resource_id: str, field: dict[str, Any]) -> str:
    """Suggested variable name: ``PYGEOAPI_<RESOURCE>_<FIELD>`` (PLAN.md §3)."""
    stem = field.get('env_name') or re.sub(r'^data\.', '', field['key'])
    name = f'PYGEOAPI_{resource_id}_{stem}'.upper()
    name = re.sub(r'[^A-Z0-9_]+', '_', name)
    return re.sub(r'_+', '_', name).strip('_')


def placeholder(name: str) -> str:
    return '${' + name + '}'


# ------------------------------------------------------------------------ render


def _set_nested(target: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a dotted key. A mapping merges with one already there (options and options.crs)."""
    parts = dotted.split('.')
    for part in parts[:-1]:
        nxt = target.get(part)
        if not isinstance(nxt, dict):
            nxt = target[part] = {}
        target = nxt
    current = target.get(parts[-1])
    if isinstance(value, dict):
        value = {**current, **value} if isinstance(current, dict) else dict(value)
    target[parts[-1]] = value


def _coerce(field: dict[str, Any], value: Any) -> Any:
    t = field['type']
    if t == 'integer' and isinstance(value, str) and re.fullmatch(r'-?\d+', value.strip()):
        return int(value)
    if t == 'number' and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if t in ('string', 'text', 'path', 'url', 'crs', 'field') and isinstance(value, str):
        return value.strip()
    if t == 'yaml' and isinstance(value, str):
        # A value whose shape varies, such as one column or a pair of them.
        try:
            return yaml.safe_load(value)
        except yaml.YAMLError:
            return value
    return value


def yaml_text(value: Any) -> str:
    """A config value as the one-line YAML a ``yaml`` field holds."""
    if isinstance(value, str):
        return value
    return (
        str(yaml.safe_dump(value, default_flow_style=True, sort_keys=False))
        .strip()
        .removesuffix('...')
        .strip()
    )


def _merge_extra(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _merge_extra(target[k], v)
        elif k not in target:
            target[k] = v


def variant_of(p: ProviderState) -> str | None:
    """The variant a provider's form uses."""
    chosen = p.variant or p.values.get(VARIANT_KEY)
    return str(chosen) if chosen else None


def render_provider(
    p: ProviderState, d: Descriptor, resource_id: str, env: dict[str, EnvVar]
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for f in d.active_fields(variant_of(p)):
        key = f['key']
        if f['type'] == 'secret':
            ref = p.secrets.get(key)
            if ref is None:
                continue
            value: Any = placeholder(ref.env)
            env.setdefault(ref.env, EnvVar(name=ref.env, used_by=[], secret=True))
            env[ref.env].used_by.append(f'{resource_id}: {key}')
        else:
            if key not in p.values:
                continue
            value = _coerce(f, p.values[key])
            if value in EMPTY:
                continue
            if 'default' in f and value == f['default'] and f.get('emit') != 'always':
                continue
        _set_nested(body, key, value)
    out: dict[str, Any] = {'type': d.provider_type, 'name': d.plugin}
    if 'data' in body:
        out['data'] = body.pop('data')
    out.update(body)
    _merge_extra(out, p.extra)
    return out


def _datetime(value: str | None) -> dt.datetime | None:
    if value in (None, ''):
        return None
    parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def render(form: FormState, catalog: Catalog, mapping_indent: int = 4) -> RenderResult:
    env: dict[str, EnvVar] = {}
    warnings: list[str] = []
    r = form.resource
    res: dict[str, Any] = {
        'type': r.type,
        'title': r.title,
        'description': r.description,
        'keywords': r.keywords,
    }
    if r.links:
        res['links'] = [link.model_dump(exclude_none=True) for link in r.links]
    if r.linked_data_context:
        res['linked-data'] = {'context': r.linked_data_context}
    extents: dict[str, Any] = {
        'spatial': {'bbox': list(r.extents.spatial.bbox), 'crs': r.extents.spatial.crs}
    }
    t = r.extents.temporal
    if t is not None and (t.begin or t.end):
        # Both keys, always: pygeoapi indexes begin *and* end (notes §1.4).
        extents['temporal'] = {'begin': _datetime(t.begin), 'end': _datetime(t.end)}
    res['extents'] = extents
    if r.templates_path:
        res['templates'] = {'path': r.templates_path}
    providers = []
    for p in form.providers:
        if p.descriptor == RAW_DESCRIPTOR:
            providers.append(dict(p.extra))
            continue
        d = catalog.get(p.descriptor)
        if (
            d.provider_type == 'stac'
        ):  # pygeoapi serves STAC providers from stac-collection resources
            res['type'] = 'stac-collection'
        providers.append(render_provider(p, d, form.resource_id, env))
        # Variables the provider reads itself, such as a created provider's spec placeholders.
        for var in d.raw.get('env', []):
            entry = env.setdefault(
                var['name'], EnvVar(name=var['name'], used_by=[], secret=bool(var.get('secret')))
            )
            entry.used_by.append(f'{form.resource_id}: the provider spec')
    res['providers'] = providers
    text = yamlio.dump({form.resource_id: res}, mapping_indent=mapping_indent)
    return RenderResult(
        resource_id=form.resource_id,
        resource=res,
        yaml=text,
        env=list(env.values()),
        warnings=warnings,
    )


def form_values_from_provider(
    d: Descriptor,
    provider: dict[str, Any],
    variant: str | None = None,
    resource_id: str = 'resource',
) -> ProviderState:
    """The reverse mapping: a provider dict (e.g. a docs example) -> form state.

    Keys the descriptor doesn't know go to ``extra``, so nothing is lost.
    A secret written as ``${NAME}`` becomes a reference to ``NAME``; any other
    secret value becomes a reference to a suggested name, and the value is dropped.
    """
    fields = {f['key']: f for f in d.active_fields(variant)}
    values: dict[str, Any] = {}
    secrets: dict[str, SecretRef] = {}
    extra: dict[str, Any] = {}

    def walk(obj: dict[str, Any], prefix: str, extra_node: dict[str, Any]) -> None:
        for k, v in obj.items():
            key = f'{prefix}{k}'
            if key in ('type', 'name'):
                continue
            f = fields.get(key)
            nested = isinstance(v, dict) and any(fk.startswith(key + '.') for fk in fields)
            if f is not None and f['type'] == 'object' and nested:
                # A free-form mapping some of whose keys are fields of their own (options.crs).
                depth = key.count('.') + 1
                own = {fk.split('.')[depth] for fk in fields if fk.startswith(key + '.')} & set(v)
                rest = {k2: v2 for k2, v2 in v.items() if k2 not in own}
                if rest:
                    values[key] = rest
                walk({k2: v[k2] for k2 in own}, key + '.', extra_node.setdefault(k, {}))
                if not extra_node[k]:
                    del extra_node[k]
            elif f is not None:
                if f['type'] == 'secret':
                    m = re.fullmatch(r'\$\{(\w+)(:-[^}]*)?\}', v) if isinstance(v, str) else None
                    name = m.group(1) if m else env_name(resource_id, f)
                    secrets[key] = SecretRef(env=name)
                elif f['type'] == 'yaml':
                    values[key] = yaml_text(v)
                else:
                    values[key] = v
            elif nested:
                sub: dict[str, Any] = {}
                walk(v, key + '.', sub)
                if sub:
                    extra_node[k] = sub
            else:
                extra_node[k] = v

    walk(provider, '', extra)
    if d.variants:
        values[VARIANT_KEY] = variant or d.variants[0]['id']
    return ProviderState(
        descriptor=d.id, variant=variant, values=values, secrets=secrets, extra=extra
    )


def detect_variant(d: Descriptor, provider: dict[str, Any]) -> str | None:
    """The variant whose fields best match a provider's keys and value shapes."""
    if not d.variants:
        return None

    def keys(obj: dict[str, Any], prefix: str = '') -> set[str]:
        out = set()
        for k, v in obj.items():
            out.add(f'{prefix}{k}')
            if isinstance(v, dict):
                out |= keys(v, f'{prefix}{k}.')
        return out

    present = keys(provider)
    best, score = d.variants[0]['id'], -1
    for v in d.variants:
        n = 0
        for f in v['fields']:
            if f['key'] in present:
                n += 2 if _fits(f, _dotted_get(provider, f['key'])) else -2
            elif f.get('required'):
                n -= 1
        if n > score:
            best, score = v['id'], n
    return str(best)


def _dotted_get(obj: dict[str, Any], key: str) -> Any:
    node: Any = obj
    for part in key.split('.'):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _fits(f: dict[str, Any], value: Any) -> bool:
    """Whether a config value has the shape a field expects: its choices, URL or path, pattern."""
    if isinstance(value, str) and '${' in value:
        return True  # a placeholder fits anything
    if f.get('choices') and value not in f['choices']:
        return False
    if f['type'] == 'url' and not (isinstance(value, str) and URL_SCHEME.match(value)):
        return False
    if f['type'] == 'path' and isinstance(value, str) and URL_SCHEME.match(value):
        return False
    return not (f.get('pattern') and isinstance(value, str) and not re.search(f['pattern'], value))


URL_SCHEME = re.compile(r'^https?://', re.IGNORECASE)


class LoadedResource(BaseModel):
    form: FormState
    editable: bool = Field(description='False when only the YAML can be shown')
    warnings: list[str] = []


def _iso(value: Any) -> str | None:
    if value in (None, ''):
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC).replace(tzinfo=None)
        return str(value.isoformat()) + 'Z'
    if isinstance(value, dt.date):
        return value.isoformat() + 'T00:00:00Z'
    return str(value)


def form_from_resource(resource_id: str, res: dict[str, Any], catalog: Catalog) -> LoadedResource:
    """A resource from a config -> form state (J2). Edits are merged back with ``merge3``."""
    warnings: list[str] = []
    editable = True
    if res.get('type') not in ('collection', 'stac-collection'):
        editable = False
        warnings.append(
            f'Resources of type {res.get("type")!r} can be listed, not edited, in forms.'
        )
    r = ResourceState()
    if res.get('type') == 'stac-collection':
        r.type = 'stac-collection'
    for key in ('title', 'description', 'keywords'):
        if isinstance(res.get(key), str | dict | list):
            setattr(r, key, res[key])
    links = []
    for i, link in enumerate(res.get('links') or []):
        try:
            links.append(Link(**link))
        except (TypeError, ValueError):
            warnings.append(f'Link {i + 1} is missing a required key; it is kept but not shown.')
    r.links = links
    ext = res.get('extents') or {}
    spatial = ext.get('spatial') or {}
    if isinstance(spatial.get('bbox'), list):
        r.extents.spatial.bbox = spatial['bbox']
    if isinstance(spatial.get('crs'), str):
        r.extents.spatial.crs = spatial['crs']
    temporal = ext.get('temporal')
    if isinstance(temporal, dict):
        r.extents.temporal = Temporal(
            begin=_iso(temporal.get('begin')), end=_iso(temporal.get('end'))
        )
    ld = res.get('linked-data')
    if isinstance(ld, dict) and isinstance(ld.get('context'), list):
        r.linked_data_context = ld['context']
    templates = res.get('templates')
    if isinstance(templates, dict) and isinstance(templates.get('path'), str):
        r.templates_path = templates['path']
    if 'template' in res:
        warnings.append(
            'The legacy template: key is ignored by pygeoapi 0.24 (it was an Internet of '
            'Water fork feature). Use templates: path: instead (docs/pygeoapi-notes.md §8).'
        )
    providers = []
    for i, pdef in enumerate(res.get('providers') or []):
        name = pdef.get('name') if isinstance(pdef, dict) else None
        d = catalog.by_plugin(name) if isinstance(name, str) else None
        if d is None:
            providers.append(ProviderState(descriptor=RAW_DESCRIPTOR, extra=dict(pdef or {})))
            warnings.append(
                f'Provider {i + 1} ({name}) is not in the catalog; it is kept as YAML.'
            )
            continue
        variant = detect_variant(d, pdef)
        providers.append(form_values_from_provider(d, pdef, variant, resource_id))
        for f in d.active_fields(variant):
            if f['type'] != 'secret':
                continue
            node: Any = pdef
            for part in f['key'].split('.'):
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, str) and node and not node.startswith('${'):
                warnings.append(
                    f'Provider {i + 1} has a literal value for {f["key"]}. It is kept, but '
                    'secrets belong in environment variables: replace it with a reference.'
                )
    form = FormState(resource_id=resource_id, resource=r, providers=providers)
    return LoadedResource(form=form, editable=editable, warnings=warnings)
