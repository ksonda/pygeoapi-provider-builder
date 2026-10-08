"""Drift check: do descriptors match the keys pygeoapi's providers read? (ADR-0010)

For each core descriptor, scan the provider class and its pygeoapi base classes
for provider_def keys (flow-insensitive alias tracking over the AST) and report:

- missing:  keys the code reads that the descriptor (with its base) lacks
- invented: descriptor keys the code never reads (unless marked
            ``drift: passthrough`` or read by pygeoapi's API layer)

Usage: python tools/drift_check.py [--catalog catalog] [DESCRIPTOR_ID...]
Exit status 1 when anything drifts.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

#: Variables that hold provider_def['data'] across function boundaries.
ALIAS_HINTS = {'connection_data': ('data',), 'conn_dic': ('data',)}
#: Keys pygeoapi's API layer reads from provider definitions (notes §1.7).
API_KEYS = {
    'type',
    'name',
    'data',
    'default',
    'crs',
    'always_xy',
    'storage_crs',
    'storage_crs_coordinate_epoch',
    'geom_field',
    'languages',
    'language',
    'format',
    'format.name',
    'format.mimetype',
    'uri_field',
    'title_field',
    'editable',
    'include_extra_query_parameters',
}
#: Keys read only for plumbing, never configured by users.
IGNORED = {'options', 'properties'}


def _target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f'{node.value.id}.{node.attr}'
    return None


def scan(source: str) -> dict[str, set[int]]:
    """Dotted provider_def keys read in a module, with line numbers."""
    tree = ast.parse(source)
    aliases: dict[str, tuple[str, ...]] = {
        'provider_def': (),
        'self.data': ('data',),
        'self.options': ('options',),
        **ALIAS_HINTS,
    }

    def path_of(node: ast.AST) -> tuple[str, ...] | None:
        name = _target(node)
        if name in aliases:
            return aliases[name]
        if isinstance(node, ast.Subscript):
            base = path_of(node.value)
            if (
                base is not None
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                return (*base, node.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ('get', 'pop', 'setdefault')
            and node.args
        ):
            base = path_of(node.func.value)
            arg = node.args[0]
            if base is not None and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return (*base, arg.value)
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                p = path_of(value)
                if p is not None:
                    return p
        return None

    for _ in range(4):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                p = path_of(node.value)
                if p:
                    for t in node.targets:
                        n = _target(t)
                        if n and n != 'provider_def':
                            aliases.setdefault(n, p)
    keys: dict[str, set[int]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript | ast.Call):
            p = path_of(node)
            if p:
                keys['.'.join(p)].add(node.lineno)
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In | ast.NotIn)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
        ):
            base = path_of(node.comparators[0])
            if base is not None:
                keys['.'.join((*base, node.left.value))].add(node.lineno)
    return keys


def code_keys(plugin: str) -> dict[str, set[str]]:
    """Keys read by the provider class and its pygeoapi base classes."""
    from pygeoapi.plugin import PLUGINS

    dotted = PLUGINS['provider'].get(plugin, plugin)
    module, cls_name = dotted.rsplit('.', 1)
    cls = getattr(importlib.import_module(module), cls_name)
    found: dict[str, set[str]] = defaultdict(set)
    for klass in inspect.getmro(cls):
        if not klass.__module__.startswith('pygeoapi.'):
            continue
        path = Path(inspect.getfile(klass))
        for key, lines in scan(path.read_text(encoding='utf-8')).items():
            rel = path.as_posix().split('site-packages/')[-1]
            found[key] |= {f'{rel}:{n}' for n in lines}
    return found


def descriptor_keys(catalog: Path, raw: dict[str, Any]) -> tuple[set[str], set[str]]:
    base = yaml.safe_load((catalog / '_base' / f'{raw["provider_type"]}.yaml').read_text())
    variant_fields = [f for v in raw.get('variants', []) for f in v['fields']]
    fields = list(raw.get('fields', [])) + variant_fields
    hidden = set(raw.get('hide', []))
    base_fields = [f for f in base.get('fields', []) if f['key'] not in hidden]
    keys = {f['key'] for f in fields} | {f['key'] for f in base_fields}
    ignored = {i['key'] for i in [*base.get('drift_ignore', []), *raw.get('drift_ignore', [])]}
    passthrough = {f['key'] for f in fields if f.get('drift') in ('passthrough', 'docs_only')}
    return keys | hidden | ignored, passthrough


def check(catalog: Path, ids: list[str]) -> int:
    problems = 0
    for path in sorted((catalog / 'core').glob('*.yaml')):
        raw = yaml.safe_load(path.read_text())
        if ids and raw['id'] not in ids:
            continue
        found = code_keys(raw['plugin'])
        known, passthrough = descriptor_keys(catalog, raw)
        read = set(found) - IGNORED
        # A parent of a known key (geometry for geometry.x_field) is not missing.
        missing = sorted(
            k for k in read - known - API_KEYS if not any(p.startswith(k + '.') for p in known)
        )
        invented = sorted(
            k
            for k in known - read - passthrough - API_KEYS - IGNORED
            if not any(
                r.startswith(k + '.') or k.startswith(r + '.') and r not in ('data', 'options')
                for r in read
            )
        )
        status = 'ok' if not (missing or invented) else 'DRIFT'
        print(f'{raw["id"]:12s} {status}')
        for k in missing:
            print(f'    missing from descriptor: {k}  (read at {", ".join(sorted(found[k]))})')
        for k in invented:
            print(f'    not read by the code:    {k}')
        problems += len(missing) + len(invented)
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('ids', nargs='*')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'catalog')
    args = parser.parse_args()
    return check(args.catalog, args.ids)


if __name__ == '__main__':
    sys.exit(main())
