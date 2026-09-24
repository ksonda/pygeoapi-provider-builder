"""YAML output in the style pygeoapi configs use, and appending a resource.

pygeoapi reads configs with PyYAML (YAML 1.1) while ruamel.yaml writes
YAML 1.2. So strings that YAML 1.1 would read as something else (``yes``,
``on``, ``1_000``, ``2020-01-01``) are always quoted. ``${VAR}`` placeholders
are always emitted as plain scalars, because pygeoapi substitutes only plain
scalars (docs/pygeoapi-notes.md §2). Whole documents are never re-serialized
(ADR-0003): new resources are spliced into the original text.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.representer import RoundTripRepresenter

_PYYAML_RESOLVER = pyyaml.resolver.Resolver()
FLOW_KEYS = {'bbox'}
PLACEHOLDER_RE = re.compile(r'\$\{\w+(:-[^}]*)?\}')


def _needs_quotes(value: str) -> bool:
    """Would PyYAML (as pygeoapi uses it) read this plain scalar as a non-string?"""
    tag = _PYYAML_RESOLVER.resolve(  # type: ignore[no-untyped-call]
        pyyaml.ScalarNode, value, (True, False)
    )
    return bool(tag != 'tag:yaml.org,2002:str')


class _Representer(RoundTripRepresenter):
    pass


def _represent_str(rep: RoundTripRepresenter, data: str) -> Any:
    style = "'" if _needs_quotes(data) else None
    return rep.represent_scalar('tag:yaml.org,2002:str', data, style=style)


def _represent_datetime(rep: RoundTripRepresenter, data: dt.datetime) -> Any:
    if data.tzinfo is None:
        data = data.replace(tzinfo=dt.UTC)
    text = data.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    return rep.represent_scalar('tag:yaml.org,2002:timestamp', text)


def _represent_none(rep: RoundTripRepresenter, data: None) -> Any:
    return rep.represent_scalar('tag:yaml.org,2002:null', 'null')


_Representer.add_representer(str, _represent_str)
_Representer.add_representer(dt.datetime, _represent_datetime)
_Representer.add_representer(type(None), _represent_none)


def make_yaml(mapping_indent: int = 4) -> YAML:
    y = YAML(typ='rt')
    y.Representer = _Representer
    y.indent(mapping=mapping_indent, sequence=mapping_indent + 2, offset=mapping_indent)
    y.width = 4096
    return y


def to_tree(obj: Any, key: str | None = None) -> Any:
    """Plain dicts/lists to ruamel nodes; ``bbox`` lists in flow style."""
    if isinstance(obj, dict):
        m = CommentedMap()
        for k, v in obj.items():
            m[k] = to_tree(v, k)
        return m
    if isinstance(obj, list | tuple):
        s = CommentedSeq([to_tree(v) for v in obj])
        if key in FLOW_KEYS and not any(
            isinstance(v, str) and PLACEHOLDER_RE.search(v) for v in obj
        ):
            s.fa.set_flow_style()
        return s
    return obj


def dump(obj: Any, mapping_indent: int = 4) -> str:
    buf = io.StringIO()
    make_yaml(mapping_indent).dump(to_tree(obj), buf)
    return buf.getvalue()


def detect_mapping_indent(text: str, default: int = 4) -> int:
    """Indentation of the first nested mapping key in a config."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r'^[A-Za-z_][\w-]*:\s*(#.*)?$', line):
            for nxt in lines[i + 1 :]:
                if not nxt.strip() or nxt.lstrip().startswith('#'):
                    continue
                n = len(nxt) - len(nxt.lstrip(' '))
                if n > 0:
                    return n
                break
    return default


class SpliceError(ValueError):
    pass


def append_resource(base_text: str, resource_id: str, resource: dict[str, Any]) -> str:
    """Return ``base_text`` with a new resource added at the end of ``resources:``.

    Everything else in the file is left byte-for-byte as it was.
    """
    try:
        parsed = pyyaml.safe_load(base_text) or {}
    except pyyaml.YAMLError as err:
        raise SpliceError(f'The base config is not valid YAML: {err}') from err
    resources = parsed.get('resources') if isinstance(parsed, dict) else None
    if isinstance(resources, dict) and resource_id in resources:
        raise SpliceError(f'The config already has a resource called {resource_id!r}')

    newline = '\r\n' if '\r\n' in base_text else '\n'
    lines = base_text.splitlines()
    indent = detect_mapping_indent(base_text)
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(r'^resources:\s*(\{\s*\})?\s*(#.*)?$', line)
        ),
        None,
    )
    block = dump({resource_id: resource}, mapping_indent=indent).rstrip('\n').split('\n')

    if start is None:
        body = [*lines, '', 'resources:', *[' ' * indent + b for b in block]]
        return newline.join(body) + newline
    if re.match(r'^resources:\s*\{\s*\}', lines[start]):
        lines[start] = 'resources:'
    content = [
        j
        for j in range(start + 1, len(lines))
        if lines[j].strip() and not lines[j].lstrip().startswith('#')
    ]
    end = next((j for j in content if not lines[j].startswith(' ')), len(lines))
    first_child = next((j for j in content if j < end), None)
    child_indent = indent
    if first_child is not None:
        child_indent = len(lines[first_child]) - len(lines[first_child].lstrip(' '))
    # Leave blank lines and column-0 comments that introduce the next top-level key.
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith('#')):
        end -= 1
    insert = [' ' * child_indent + b for b in block]
    if end > start + 1 and lines[end - 1].strip():
        insert = ['', *insert]
    out = lines[:end] + insert + lines[end:]
    text = newline.join(out)
    return text + newline if base_text.endswith(('\n', '\r\n')) or not base_text else text
