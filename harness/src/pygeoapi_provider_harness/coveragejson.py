"""CoverageJSON checks for EDR responses (PLAN.md §7.7, risk R7).

``problems()`` validates a document against the OGC CoverageJSON schema
(vendored, see ``schemas/README.md``), then adds structural checks the schema
leaves out: every range's shape and value count match the domain's axes, and
every range has a parameter that describes it.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import math
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator


@functools.cache
def validator() -> Draft202012Validator:
    text = importlib.resources.files(__package__).joinpath('schemas/coveragejson.json')
    return Draft202012Validator(json.loads(text.read_text('utf-8')))


def _pointer(parts: Any) -> str:
    return '/' + '/'.join(str(p) for p in parts) if parts else '/'


def schema_problems(doc: Any) -> list[str]:
    out = []
    for err in sorted(validator().iter_errors(doc), key=lambda e: list(e.absolute_path)):
        leaves = list(err.context) or [err]
        for leaf in leaves[:3]:
            out.append(f'{_pointer(leaf.absolute_path)}: {leaf.message[:200]}')
    return list(dict.fromkeys(out))[:20]


def _axis_length(axis: Mapping[str, Any]) -> int | None:
    if 'values' in axis and isinstance(axis['values'], list):
        return len(axis['values'])
    if 'num' in axis:
        return int(axis['num'])
    return None


def coverage_problems(
    coverage: Mapping[str, Any], parameters: Mapping[str, Any], where: str
) -> list[str]:
    out = []
    domain = coverage.get('domain')
    axes = domain.get('axes', {}) if isinstance(domain, Mapping) else {}
    own = coverage.get('parameters') or {}
    for key, rng in (coverage.get('ranges') or {}).items():
        if key not in own and key not in parameters:
            out.append(f'{where}/ranges/{key}: no parameter describes this range')
        if not isinstance(rng, Mapping) or rng.get('type') != 'NdArray':
            continue
        names, shape, values = (
            rng.get('axisNames') or [],
            rng.get('shape') or [],
            rng.get('values'),
        )
        if len(names) != len(shape):
            out.append(f'{where}/ranges/{key}: {len(names)} axis names but {len(shape)} sizes')
            continue
        for name, size in zip(names, shape, strict=True):
            length = _axis_length(axes.get(name, {})) if name in axes else None
            if name not in axes:
                out.append(f'{where}/ranges/{key}: the domain has no axis {name!r}')
            elif length is not None and length != size:
                out.append(
                    f'{where}/ranges/{key}: axis {name} has {length} values, shape says {size}'
                )
        if isinstance(values, list) and shape and len(values) != math.prod(int(s) for s in shape):
            out.append(f'{where}/ranges/{key}: {len(values)} values for shape {shape}')
    return out


def problems(doc: Any) -> list[str]:
    """Schema and structural problems; an empty list means the document is valid."""
    found = schema_problems(doc)
    if found or not isinstance(doc, Mapping):
        return found
    if doc.get('type') == 'Coverage':
        return coverage_problems(doc, {}, '')
    if doc.get('type') == 'CoverageCollection':
        parameters = doc.get('parameters') or {}
        out = []
        for i, coverage in enumerate(doc.get('coverages') or []):
            out += coverage_problems(coverage, parameters, f'/coverages/{i}')
        return out[:20]
    return []


def range_keys(doc: Mapping[str, Any]) -> set[str]:
    """The parameters a Coverage or CoverageCollection has values for."""
    coverages = doc.get('coverages') if doc.get('type') == 'CoverageCollection' else [doc]
    return {key for c in coverages or [] for key in (c.get('ranges') or {})}
