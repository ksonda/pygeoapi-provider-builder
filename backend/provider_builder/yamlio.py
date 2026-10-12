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


def make_yaml(mapping_indent: int = 4, seq_offset: int | None = None) -> YAML:
    """``seq_offset``: the dash's indent under its key (default: ``mapping_indent``)."""
    offset = mapping_indent if seq_offset is None else seq_offset
    y = YAML(typ='rt')
    y.Representer = _Representer
    y.indent(mapping=mapping_indent, sequence=offset + 2, offset=offset)
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


def dump(obj: Any, mapping_indent: int = 4, seq_offset: int | None = None) -> str:
    buf = io.StringIO()
    make_yaml(mapping_indent, seq_offset).dump(to_tree(obj), buf)
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

    Everything else in the file is left byte-for-byte as it was (``configedit``).
    """
    from .configedit import EditError, load, resources, set_resources

    try:
        existing = resources(load(base_text) or {})
    except EditError as err:
        raise SpliceError(str(err)) from err
    if resource_id in existing:
        raise SpliceError(f'The config already has a resource called {resource_id!r}')
    try:
        return set_resources(base_text, {resource_id: resource}).text
    except EditError as err:
        raise SpliceError(str(err)) from err
