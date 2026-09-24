"""The single renderer: form state -> resource dict -> YAML (PLAN.md §3).

Preview, validation, probe and export all call ``render``. It builds plain
dicts and never templates strings. Secrets are emitted only as ``${NAME}``.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import yamlio
from .catalog import VARIANT_KEY, Catalog, Descriptor

CRS84 = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
EMPTY: tuple[Any, ...] = (None, '', [], {})


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
    bbox: list[float] = [-180, -90, 180, 90]
    crs: str = CRS84


class Temporal(BaseModel):
    begin: str | None = None
    end: str | None = None


class Extents(BaseModel):
    spatial: Spatial = Spatial()
    temporal: Temporal | None = None


class ResourceState(BaseModel):
    type: Literal['collection'] = 'collection'
    title: str | dict[str, str] = ''
    description: str | dict[str, str] = ''
    keywords: list[str] | dict[str, list[str]] = []
    links: list[Link] = []
    extents: Extents = Extents()
    linked_data_context: list[dict[str, Any]] | None = None
    templates_path: str | None = None


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
    parts = dotted.split('.')
    for part in parts[:-1]:
        nxt = target.get(part)
        if not isinstance(nxt, dict):
            nxt = target[part] = {}
        target = nxt
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
    return value


def _merge_extra(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _merge_extra(target[k], v)
        elif k not in target:
            target[k] = v


def render_provider(
    p: ProviderState, d: Descriptor, resource_id: str, env: dict[str, EnvVar]
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for f in d.active_fields(p.variant or p.values.get(VARIANT_KEY)):
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
        d = catalog.get(p.descriptor)
        providers.append(render_provider(p, d, form.resource_id, env))
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
    d: Descriptor, provider: dict[str, Any], variant: str | None = None
) -> ProviderState:
    """The reverse mapping: a provider dict (e.g. a docs example) -> form state.

    Keys the descriptor doesn't know go to ``extra``, so nothing is lost.
    Secret values become references to suggested variable names; the values
    themselves are dropped.
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
            if f is not None:
                if f['type'] == 'secret':
                    secrets[key] = SecretRef(env=env_name('resource', f))
                else:
                    values[key] = v
            elif isinstance(v, dict) and any(fk.startswith(key + '.') for fk in fields):
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
