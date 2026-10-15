"""Layered validation (PLAN.md §6.2, ADR-0014).

1. descriptor: the compiled descriptor JSON Schema over flat form values
2. pygeoapi-schema: pygeoapi's own config schema on the merged config, via
   ``iter_errors`` with ``anyOf`` branches chosen by resource type, plus the
   ``uri``/``date-time`` format checks pygeoapi skips (notes §6.1)
3. runtime-rules: cited rules for configs pygeoapi accepts but then serves
   with a 400 or 500
4. sandbox checks (probe, OpenAPI generation) run as jobs; see ``app``.

Errors block publishing; warnings don't.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match
from pydantic import BaseModel

from .catalog import VARIANT_KEY, Catalog, Descriptor, compile_descriptor
from .render import FormState

Severity = Literal['error', 'warning']
PLACEHOLDER_RE = re.compile(r'\$\{(?P<name>\w+)(:-(?P<default>[^}]*))?\}')
DUMMY = 'placeholder-value'
RESERVED_SEGMENTS = {
    'items',
    'schema',
    'queryables',
    'coverage',
    'tiles',
    'map',
    'styles',
    'position',
    'area',
    'cube',
    'radius',
    'trajectory',
    'corridor',
    'locations',
    'instances',
}
ID_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$')
DATA_TYPES = ('feature', 'record', 'coverage', 'edr')


class Finding(BaseModel):
    layer: Literal['descriptor', 'pygeoapi-schema', 'runtime-rules', 'probe', 'openapi']
    severity: Severity
    code: str
    message: str
    hint: str | None = None
    pointer: str | None = None
    source: str | None = None
    details: str | None = None


# ------------------------------------------------------------------ formats

FORMATS = FormatChecker(formats=())


def _check_uri(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parsed = urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc or parsed.path)


def _check_datetime(value: object) -> bool:
    if isinstance(value, str):
        dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return True


FORMATS.checks('uri')(_check_uri)
FORMATS.checks('date-time', raises=ValueError)(_check_datetime)


# ---------------------------------------------------------------- layer 1

_MESSAGES = {
    'required': '{title} is required.',
    'type': '{title} must be {expected}.',
    'minLength': "{title} can't be empty.",
    'minItems': '{title} needs at least {n} item(s).',
    'uniqueItems': '{title} has duplicate entries.',
    'minimum': '{title} must be at least {n}.',
    'maximum': '{title} must be at most {n}.',
    'enum': '{title} must be one of the listed choices.',
    'format': '{title} is not a valid {n}.',
}
_EXPECTED = {
    'integer': 'a whole number',
    'number': 'a number',
    'string': 'text',
    'boolean': 'true or false',
    'array': 'a list',
    'object': 'a mapping',
}


def flat_values(p: Any) -> dict[str, Any]:
    """Form values plus secret references, as the compiled schema sees them."""
    values = dict(p.values)
    for key, ref in p.secrets.items():
        values[key] = ref.env
    if p.variant and VARIANT_KEY not in values:
        values[VARIANT_KEY] = p.variant
    return {k: v for k, v in values.items() if v not in (None, '', [])}


def descriptor_findings(form: FormState, catalog: Catalog) -> list[Finding]:
    findings: list[Finding] = []
    for i, p in enumerate(form.providers):
        try:
            d = catalog.get(p.descriptor)
        except ValueError as err:
            findings.append(
                Finding(
                    layer='descriptor',
                    severity='error',
                    code='unknown-descriptor',
                    message=str(err),
                    pointer=f'/providers/{i}',
                )
            )
            continue
        compiled = compile_descriptor(d)
        values = flat_values(p)
        if d.variants and VARIANT_KEY not in values:
            values[VARIANT_KEY] = d.variants[0]['id']
        validator = Draft202012Validator(compiled['json_schema'], format_checker=FORMATS)
        for problem in validator.iter_errors(values):
            findings.extend(_descriptor_error(problem, i, d, values))
        findings.extend(_unknown_keys(i, d, p.extra))
    return findings


def _descriptor_error(
    err: ValidationError, i: int, d: Descriptor, values: dict[str, Any]
) -> Iterator[Finding]:
    if err.validator == 'required':
        keys = [k for k in err.validator_value if k not in values]
    else:
        keys = [str(err.path[0])] if err.path else ['']
    for key in keys:
        f = d.field(key, values.get(VARIANT_KEY)) or {'title': key}
        template = _MESSAGES.get(str(err.validator), '{title}: ' + err.message)
        n = err.validator_value
        message = template.format(
            title=f.get('title', key), expected=_EXPECTED.get(str(n), str(n)), n=n
        )
        hint = None
        if f.get('type') == 'secret' and err.validator == 'pattern':
            message = f'{f.get("title", key)}: use letters, digits and underscores.'
            hint = 'This is the name of an environment variable, not the secret itself.'
        yield Finding(
            layer='descriptor',
            severity='error',
            code=f'descriptor.{err.validator}',
            message=message,
            hint=hint,
            pointer=f'/providers/{i}/values/{key}',
            source=_first(f.get('source')),
        )


def _unknown_keys(i: int, d: Descriptor, extra: dict[str, Any]) -> Iterator[Finding]:
    known = [f['key'] for f in d.all_fields()]
    for key in extra:
        close = difflib.get_close_matches(key, known, n=1, cutoff=0.75)
        hint = f'Did you mean `{close[0]}`?' if close else None
        yield Finding(
            layer='descriptor',
            severity='warning',
            code='descriptor.unknown-key',
            message=f'`{key}` is not a key the {d.id} descriptor knows. It will be kept as it is.',
            hint=hint,
            pointer=f'/providers/{i}/extra/{key}',
        )


def _first(src: Any) -> str | None:
    if isinstance(src, list):
        return str(src[0]) if src else None
    return src if isinstance(src, str) else None


# ---------------------------------------------------------------- layer 2


def resolve_for_validation(config: Any, env: dict[str, str]) -> tuple[Any, set[tuple[Any, ...]]]:
    """Substitute placeholders like pygeoapi's yaml_load would, with dummies.

    Returns the substituted config and the paths of values that depended on an
    unknown variable, so errors there become warnings.
    """
    from pygeoapi.util import get_typed_value

    unknown: set[tuple[Any, ...]] = set()

    def walk(v: Any, path: tuple[Any, ...]) -> Any:
        if isinstance(v, dict):
            return {k: walk(x, (*path, k)) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, (*path, n)) for n, x in enumerate(v)]
        if isinstance(v, str) and PLACEHOLDER_RE.search(v):

            def sub(m: re.Match[str]) -> str:
                name = m.group('name')
                if name in env:
                    return env[name]
                if m.group(2) is not None:
                    return m.group('default') or ''
                unknown.add(path)
                return DUMMY

            return get_typed_value(PLACEHOLDER_RE.sub(sub, v))
        return v

    return walk(config, ()), unknown


def _leaves(err: ValidationError, instance: dict[str, Any]) -> Iterator[ValidationError]:
    if err.validator in ('anyOf', 'oneOf') and err.context:
        path = list(err.absolute_path)
        branch: int | None = None
        if len(path) == 2 and path[0] == 'resources':
            rtype = (instance.get('resources', {}).get(path[1]) or {}).get('type')
            branch = 1 if rtype == 'process' else 0
        if branch is not None:
            subs = [e for e in err.context if e.relative_schema_path[0] == branch]
        else:
            chosen = best_match(err.context)
            subs = [chosen] if chosen is not None else []
        if subs:
            for sub in subs:
                yield from _leaves(sub, instance)
            return
    yield err


def _pointer(path: list[Any], resource_id: str) -> str | None:
    if len(path) < 2 or path[0] != 'resources' or path[1] != resource_id:
        return None
    rest = path[2:]
    if len(rest) >= 2 and rest[0] == 'providers' and isinstance(rest[1], int):
        keys = []
        for part in rest[2:]:
            if isinstance(part, int):
                break
            keys.append(str(part))
        return (
            f'/providers/{rest[1]}/values/' + '.'.join(keys) if keys else f'/providers/{rest[1]}'
        )
    return '/resource/' + '/'.join(str(p) for p in rest) if rest else '/resource'


def schema_findings(
    config: dict[str, Any], resource_id: str, env: dict[str, str] | None = None
) -> list[Finding]:
    from pygeoapi.config import load_schema
    from pygeoapi.util import to_json

    resolved, unknown = resolve_for_validation(config, env or {})
    instance = json.loads(to_json(resolved))
    validator = Draft202012Validator(load_schema(), format_checker=FORMATS)
    findings = []
    for err in validator.iter_errors(instance):
        for leaf in _leaves(err, instance):
            path = list(leaf.absolute_path)
            depends = any(tuple(path[: len(u)]) == u for u in unknown)
            pointer = _pointer(path, resource_id)
            where = '.'.join(str(p) for p in path) or '(top level)'
            findings.append(
                Finding(
                    layer='pygeoapi-schema',
                    severity='warning' if depends else 'error',
                    code=f'schema.{leaf.validator}',
                    message=(
                        f"Couldn't fully check {where}: it uses an environment variable "
                        'whose value is unknown here.'
                    )
                    if depends
                    else f'{where}: {leaf.message}',
                    hint=None
                    if pointer or depends
                    else 'This is in the target config itself, outside the new resource.',
                    pointer=pointer,
                    source='pygeoapi/resources/schemas/config/pygeoapi-config-0.x.yml',
                )
            )
    return findings


# ---------------------------------------------------------------- layer 3


@dataclass(frozen=True)
class RuleContext:
    config: dict[str, Any]
    resource_id: str
    probe_fields: list[dict[str, Any]] | None = None  # per provider: {name: {type: ...}}
    probe_samples: list[dict[str, Any]] | None = None  # per provider: sample_stats
    geoconnex: bool = True


@dataclass(frozen=True)
class Rule:
    code: str
    severity: Severity
    source: str
    check: Callable[[RuleContext], Iterator[tuple[str, str | None, str | None]]]


def _nth(items: list[dict[str, Any]] | None, n: int) -> dict[str, Any] | None:
    return items[n] if items and n < len(items) else None


def _resource(ctx: RuleContext) -> dict[str, Any]:
    return (ctx.config.get('resources') or {}).get(ctx.resource_id) or {}


def _r_type(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    for rid, res in (ctx.config.get('resources') or {}).items():
        if not isinstance(res, dict) or 'type' not in res:
            yield (
                f'Resource {rid!r} has no type. pygeoapi accepts that, then fails every '
                '/collections request with a 500.',
                'Add type: collection.',
                '/resource/type' if rid == ctx.resource_id else None,
            )


def _r_id(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    rid = ctx.resource_id
    if not isinstance(rid, str) or not ID_RE.match(rid):
        yield (
            'Use letters, digits, ".", "_" or "-" for the collection id, starting and '
            'ending with a letter or digit.',
            'pygeoapi puts the id in URLs without escaping it.',
            '/resource_id',
        )
    elif rid in RESERVED_SEGMENTS:
        yield (f'{rid!r} is a pygeoapi route name; pick another id.', None, '/resource_id')


def _r_link_title(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    for n, link in enumerate(_resource(ctx).get('links') or []):
        if isinstance(link, dict) and not link.get('title'):
            yield (
                'Every link needs a title. Without one pygeoapi drops the collection from '
                '/collections and returns 500 for /collections/{id}.',
                None,
                f'/resource/links/{n}/title',
            )


def _r_temporal(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    temporal = (_resource(ctx).get('extents') or {}).get('temporal')
    if temporal is None:
        return
    for key in ('begin', 'end'):
        if key not in temporal:
            yield (
                f'The temporal extent needs both begin and end; {key} is missing '
                '(use null for open-ended).',
                None,
                f'/resource/extents/temporal/{key}',
            )
        elif temporal[key] is not None and not isinstance(temporal[key], dt.datetime):
            yield (
                f'The temporal {key} must be an unquoted RFC 3339 timestamp or null, or '
                'every datetime query fails with a 400.',
                None,
                f'/resource/extents/temporal/{key}',
            )


def _r_one_per_type(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    seen: dict[str, int] = {}
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        t = p.get('type')
        if t in seen:
            yield (
                f'Only the first {t} provider of a collection is ever used; provider '
                f'{n + 1} would be ignored.',
                None,
                f'/providers/{n}',
            )
        seen.setdefault(t, n)


DATA_TYPES = ('feature', 'record', 'coverage', 'edr')


def _default_provider(ctx: RuleContext) -> dict[str, Any] | None:
    providers = _resource(ctx).get('providers') or []
    return next((p for p in providers if p.get('default')), providers[0]) if providers else None


def _r_default(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    """A tile or map provider may describe a collection only when it has no data provider."""
    providers = _resource(ctx).get('providers') or []
    default = _default_provider(ctx)
    if default is None:
        yield ('A collection needs at least one provider.', None, '/providers')
        return
    has_data = any(p.get('type') in DATA_TYPES for p in providers)
    if default.get('type') in ('tile', 'map') and has_data:
        yield (
            'A tile or map provider must not be the default; put the data provider first '
            'or mark it default: true.',
            None,
            '/providers',
        )


def _w_tile_items(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    """pygeoapi describes a vector-tile-first collection as having items, which fail."""
    providers = _resource(ctx).get('providers') or []
    default = _default_provider(ctx)
    if default is None or default.get('type') != 'tile':
        return
    if any(p.get('type') in ('feature', 'record') for p in providers):
        return
    if str((default.get('format') or {}).get('name', '')).lower() in ('png', 'jpeg'):
        return
    yield (
        'pygeoapi lists an items link for a collection described by vector tiles, but '
        '/items fails without a feature provider.',
        'Add a feature provider for the same data (Collections → Add a provider) to make '
        'the link work, or ignore this if clients only use the tiles.',
        '/providers',
    )


def _r_format(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        fmt = p.get('format')
        if p.get('type') in ('tile', 'map', 'coverage') and not (
            isinstance(fmt, dict) and fmt.get('name') and fmt.get('mimetype')
        ):
            yield (
                f'{p.get("type")} providers need format: {{name, mimetype}}.',
                None,
                f'/providers/{n}/values/format',
            )


def _r_crs(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        crs = p.get('crs')
        if crs is not None and (
            not isinstance(crs, list) or not all(isinstance(c, str) and '://' in c for c in crs)
        ):
            yield (
                'crs must be a list of CRS URIs, e.g. http://www.opengis.net/def/crs/EPSG/0/4326.',
                None,
                f'/providers/{n}/values/crs',
            )


def _r_title_field(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        tf = p.get('title_field')
        fields = _nth(ctx.probe_fields, n)
        if tf and fields and tf in fields and fields[tf].get('type') != 'string':
            yield (
                f'title_field must be a text field; {tf!r} is {fields[tf].get("type")}. '
                'A non-text title makes every item page fail with a 500.',
                None,
                f'/providers/{n}/values/title_field',
            )


def _r_fields_exist(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    if not ctx.probe_fields:
        return
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        fields = _nth(ctx.probe_fields, n)
        stats = _nth(ctx.probe_samples, n) or {}
        if not fields:
            continue
        names = set(fields) | set(stats or {})
        for key in ('title_field', 'time_field', 'uri_field'):
            if p.get(key) and p[key] not in names:
                yield (
                    f'{key} {p[key]!r} is not a field of this data.',
                    None,
                    f'/providers/{n}/values/{key}',
                )
        for prop in p.get('properties') or []:
            if prop not in names:
                yield (
                    f'Property {prop!r} is not a field of this data.',
                    None,
                    f'/providers/{n}/values/properties',
                )


def _r_languages(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    server = ctx.config.get('server') or {}
    if not (server.get('languages') or server.get('language')):
        yield (
            "The target config has no server.languages. pygeoapi won't start without it.",
            'Add server.languages (e.g. [en-US]) to the target config.',
            None,
        )


def _r_openapi_metadata(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    meta = ctx.config.get('metadata') or {}
    if not (meta.get('identification') or {}).get('terms_of_service'):
        yield (
            'The target config has no metadata.identification.terms_of_service; '
            "pygeoapi's OpenAPI generation fails without it.",
            None,
            None,
        )
    if not (meta.get('license') or {}).get('url'):
        yield (
            'The target config has no metadata.license.url; '
            "pygeoapi's OpenAPI generation fails without it.",
            None,
            None,
        )


def _w_default_items(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    if not ((ctx.config.get('server') or {}).get('limits') or {}).get('default_items'):
        yield (
            'The target config has no server.limits.default_items, so the HTML items page '
            'will fail with a 500 (JSON still works).',
            None,
            None,
        )


def _w_extent_crs(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    spatial = (_resource(ctx).get('extents') or {}).get('spatial') or {}
    if 'crs' not in spatial:
        yield (
            'Write extents.spatial.crs explicitly: without it the JSON-LD has no spatial '
            'coverage.',
            None,
            '/resource/extents/spatial/crs',
        )


def _w_admin_secrets(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    if (ctx.config.get('server') or {}).get('admin') and PLACEHOLDER_RE.search(
        json.dumps(ctx.config.get('resources') or {}, default=str)
    ):
        yield (
            'This server has the Admin API on. pygeoapi then publishes the resolved config, '
            'secrets included, in /openapi.',
            'Turn server.admin off, or keep secrets out of this server.',
            None,
        )


def _r_geoconnex(ctx: RuleContext) -> Iterator[tuple[str, str | None, str | None]]:
    if not ctx.geoconnex or not ctx.probe_samples:
        return
    for n, p in enumerate(_resource(ctx).get('providers') or []):
        uf = p.get('uri_field')
        stats = _nth(ctx.probe_samples, n)
        if not uf or not stats or uf not in stats:
            continue
        values = [str(v) for v in stats[uf].get('values', [])]
        bad = [v for v in values if not re.match(r'^https?://[^/\s]+/', v)]
        filled = int(stats[uf].get('count', 0)) - int(stats[uf].get('nulls', 0))
        repeats = filled - int(stats[uf].get('distinct', filled))
        if repeats > 0 and not bad:
            yield (
                f'uri_field {uf!r} must identify each item, but {repeats} sample value(s) '
                "repeat another item's.",
                None,
                f'/providers/{n}/values/uri_field',
            )
        elif bad or stats[uf].get('nulls'):
            yield (
                f'uri_field {uf!r} must hold an absolute URI for every item; '
                f'the sample has {bad[0]!r}.'
                if bad
                else f'uri_field {uf!r} is empty for some items.',
                None,
                f'/providers/{n}/values/uri_field',
            )
        elif not all(v.startswith('https://geoconnex.us/') for v in values):
            yield (
                f'uri_field {uf!r} values are not Geoconnex PIDs (https://geoconnex.us/...).',
                'Fine if you are not registering with Geoconnex.',
                f'/providers/{n}/values/uri_field',
            )


def jsonld_findings(
    form: FormState, has_templates_dir: bool, fields: dict[str, Any] | None
) -> list[Finding]:
    """The collection's JSON-LD template choice (Geoconnex options)."""
    from . import jsonld

    choice = form.resource.jsonld_template
    if choice is None:
        return []
    pointer = '/resource/jsonld_template'
    out: list[Finding] = []

    def finding(severity: Severity, code: str, message: str, hint: str | None = None) -> None:
        out.append(
            Finding(
                layer='descriptor',
                severity=severity,
                code=code,
                message=message,
                hint=hint,
                pointer=pointer,
                source='pygeoapi/linked_data.py:255',
            )
        )

    try:
        jsonld.generate(choice.id, choice.slots)
    except jsonld.TemplateError as err:
        finding('error', 'jsonld.template', str(err))
        return out
    template = jsonld.LIBRARY[choice.id]
    for slot in template.slots:
        value = choice.slots.get(slot.id)
        if slot.kind == 'field' and value and fields is not None and value not in fields:
            finding(
                'warning',
                'jsonld.field',
                f'{slot.title}: {value!r} is not one of the fields the provider lists.',
            )
    if not has_templates_dir and not form.resource.templates_path:
        finding(
            'warning',
            'jsonld.where',
            "This target has no templates folder, so the template isn't written for you.",
            'Put item.jsonld at <templates path>/collections/items/item.jsonld on the server, '
            'and set the templates path.',
        )
    if not any(p.values.get('uri_field') for p in form.providers):
        finding(
            'warning',
            'jsonld.uri',
            "Without a uri_field, each item's @id is its pygeoapi URL, not a persistent "
            'identifier such as a Geoconnex PID.',
        )
    return out


RULES: list[Rule] = [
    Rule('rule.resource-type', 'error', 'pygeoapi/util.py:571', _r_type),
    Rule('rule.resource-id', 'error', 'pygeoapi/flask_app.py:234', _r_id),
    Rule('rule.link-title', 'error', 'pygeoapi/api/collection.py:150', _r_link_title),
    Rule('rule.temporal', 'error', 'pygeoapi/api/__init__.py:1191', _r_temporal),
    Rule(
        'rule.one-provider-per-type', 'error', 'pygeoapi/provider/__init__.py:51', _r_one_per_type
    ),
    Rule('rule.default-provider', 'error', 'pygeoapi/provider/__init__.py:81', _r_default),
    Rule('rule.tile-items', 'warning', 'pygeoapi/api/collection.py:243', _w_tile_items),
    Rule('rule.format', 'error', 'pygeoapi/api/collection.py:82', _r_format),
    Rule('rule.crs-list', 'error', 'pygeoapi/crs.py:111', _r_crs),
    Rule(
        'rule.title-field-string',
        'error',
        'pygeoapi/templates/collections/items/item.html:34',
        _r_title_field,
    ),
    Rule('rule.fields-exist', 'error', 'pygeoapi/api/itemtypes.py:468', _r_fields_exist),
    Rule('rule.server-languages', 'error', 'pygeoapi/l10n.py:444', _r_languages),
    Rule('rule.openapi-metadata', 'error', 'pygeoapi/openapi.py:272', _r_openapi_metadata),
    Rule(
        'rule.default-items',
        'warning',
        'pygeoapi/templates/collections/items/index.html:81',
        _w_default_items,
    ),
    Rule('rule.extent-crs', 'warning', 'pygeoapi/linked_data.py:147', _w_extent_crs),
    Rule('rule.admin-secrets', 'warning', 'pygeoapi/openapi.py:819', _w_admin_secrets),
    Rule('rule.geoconnex-uri', 'warning', 'pygeoapi/linked_data.py:212', _r_geoconnex),
]


def rule_findings(ctx: RuleContext) -> list[Finding]:
    findings = []
    for rule in RULES:
        for message, hint, pointer in rule.check(ctx):
            severity = rule.severity
            if rule.code == 'rule.geoconnex-uri' and (
                'must hold' in message or 'must identify' in message
            ):
                severity = 'error'
            findings.append(
                Finding(
                    layer='runtime-rules',
                    severity=severity,
                    code=rule.code,
                    message=message,
                    hint=hint,
                    pointer=pointer,
                    source=rule.source,
                )
            )
    return findings
