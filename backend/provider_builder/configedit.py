"""Lossless config edits: minimal text patches to the original text (ADR-0003, PLAN.md §4).

``ruamel.yaml`` parses the text only to *locate* nodes. PyYAML (pygeoapi's
loader) gives the data that is compared. A change becomes the smallest patch
that can express it:

- a scalar replaced in place, keeping its quotes style and trailing comment
- a key's line span inserted or deleted
- a sequence item appended or deleted
- otherwise the smallest enclosing block, re-serialized in the file's style

The result must parse to exactly the intended document, or ``EditError`` is
raised and nothing is written. An edit that changes no data returns the input
unchanged, so a no-op save is byte-identical by construction.
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError as RuamelError

from . import yamlio


class EditError(ValueError):
    """The edit can't be applied losslessly; nothing should be written."""


@dataclass(frozen=True)
class Style:
    mapping_indent: int = 4
    seq_offset: int = 4  # the dash's column relative to its parent key


@dataclass(frozen=True)
class Note:
    """A block that was re-serialized: it may be normalised inside (ADR-0003)."""

    path: str
    message: str


@dataclass
class Edit:
    text: str
    changed: bool
    notes: list[Note] = field(default_factory=list)

    def diff(self, before: str, name: str = 'config') -> str:
        return unified_diff(before, self.text, name)


def etag(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def unified_diff(before: str, after: str, name: str = 'config') -> str:
    return ''.join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f'a/{name}',
            tofile=f'b/{name}',
        )
    )


def load(text: str) -> Any:
    """The data, as pygeoapi's PyYAML reads it (placeholders are not substituted)."""
    try:
        return pyyaml.safe_load(text)
    except pyyaml.YAMLError as err:
        raise EditError(f'The config is not valid YAML: {err}') from err


# ------------------------------------------------------------------ comparison


def canon(value: Any) -> Any:
    """A comparable form that keeps YAML types apart (``1``, ``1.0`` and ``true`` differ)."""
    if isinstance(value, dict):
        return {'__map__': sorted((canon(k), canon(v)) for k, v in value.items())}
    if isinstance(value, list | tuple):
        return ['__seq__', *[canon(v) for v in value]]
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC)
        return ('datetime', value.isoformat())
    if isinstance(value, dt.date):
        return ('date', value.isoformat())
    if value is None:
        return ('null', '')
    return (type(value).__name__, repr(value))


def same(a: Any, b: Any) -> bool:
    return bool(canon(a) == canon(b))


# ------------------------------------------------------------------ three-way merge

_MISSING = object()


def merge3(orig: Any, base: Any, ours: Any) -> Any:
    """Apply the change ``base -> ours`` to ``orig``.

    ``orig`` is what the file holds, ``base`` is the form as loaded rendered back
    to data, and ``ours`` is the edited form rendered. Parts the form can't
    represent (unknown keys, comments, a missing ``crs`` the form would default)
    stay as they are in ``orig`` unless the user changed them.
    """
    if same(base, ours):
        return orig
    if isinstance(orig, dict) and isinstance(base, dict) and isinstance(ours, dict):
        out = dict(orig)
        for k in list(base) + [k for k in ours if k not in base]:
            if k not in ours:
                out.pop(k, None)
            elif k not in base or k not in orig:
                out[k] = ours[k]
            else:
                out[k] = merge3(orig[k], base[k], ours[k])
        return out
    if isinstance(orig, list) and isinstance(base, list) and isinstance(ours, list):
        if len(orig) == len(base) == len(ours):
            # A reorder (e.g. providers) moves the original items.
            order: list[int] = []
            for item in ours:
                j = next((j for j, b in enumerate(base) if j not in order and same(b, item)), None)
                if j is None:
                    break
                order.append(j)
            else:
                return [orig[j] for j in order]
            return [merge3(o, b, u) for o, b, u in zip(orig, base, ours, strict=True)]
        if len(orig) == len(base) and len(ours) > len(base) and same(ours[: len(base)], base):
            return orig + ours[len(base) :]
        if len(orig) == len(base) == len(ours) + 1:
            for j in range(len(base)):
                if same(base[:j] + base[j + 1 :], ours):
                    return orig[:j] + orig[j + 1 :]
    return ours


# ------------------------------------------------------------------ style


def detect_style(text: str) -> Style:
    mapping = yamlio.detect_mapping_indent(text)
    lines = text.split('\n')
    for i, line in enumerate(lines):
        m = re.match(r'^( *)[^\s#\-][^#]*:\s*(#.*)?$', line)
        if not m:
            continue
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or nxt.lstrip().startswith('#'):
                continue
            d = re.match(r'^( *)-(\s|$)', nxt)
            if d and len(d.group(1)) >= len(m.group(1)):
                return Style(mapping, len(d.group(1)) - len(m.group(1)))
            break
    return Style(mapping, mapping)


# ------------------------------------------------------------------ the patcher


class _Fallback(Exception):  # noqa: N818 - control flow, not an error
    """This node can't be patched in place; re-serialize the enclosing block."""


Replace = Callable[[Any], None]


def _plain(key: Any) -> Any:
    """A ruamel key as the plain Python value PyYAML would give."""
    for t in (bool, int, float, str):
        if isinstance(key, t):
            return t(key)
    return key


def _path(parts: list[Any]) -> str:
    return '/' + '/'.join(str(p) for p in parts)


class _Patcher:
    def __init__(self, text: str):
        self.text = text
        self.lines = text.split('\n')
        self.starts: list[int] = []
        pos = 0
        for line in self.lines:
            self.starts.append(pos)
            pos += len(line) + 1
        try:
            self.rt = YAML(typ='rt').load(text)
        except RuamelError as err:
            raise EditError(f'The config could not be parsed for editing: {err}') from err
        self.style = detect_style(text)
        self.edits: list[tuple[int, int, str]] = []
        self.notes: list[Note] = []

    # ---- text geometry

    def off(self, line: int, col: int) -> int:
        return self.starts[line] + col if line < len(self.lines) else len(self.text)

    def line_start(self, line: int) -> int:
        return self.starts[line] if line < len(self.lines) else len(self.text)

    def content(self, i: int) -> bool:
        s = self.lines[i].strip()
        return bool(s) and not s.startswith('#')

    def indent(self, i: int) -> int:
        line = self.lines[i]
        return len(line) - len(line.lstrip(' '))

    def dash(self, i: int) -> bool:
        return re.match(r'^ *-(\s|$)', self.lines[i]) is not None

    def block_end(self, line: int, col: int) -> int:
        """The first line after ``line`` that isn't part of a node starting at ``col``."""
        for i in range(line + 1, len(self.lines)):
            if not self.content(i):
                continue
            ind = self.indent(i)
            if ind < col or (ind == col and not self.dash(i)):
                return i
        return len(self.lines)

    def trim(self, start: int, end: int) -> int:
        """Leave out trailing blank and comment lines: they introduce what follows."""
        while end - 1 > start and not self.content(end - 1):
            end -= 1
        return end

    def at_line_start(self, line: int, col: int) -> bool:
        return self.lines[line][:col].strip() == ''

    # ---- serialization in the file's style

    def dump_lines(self, obj: Any) -> list[str]:
        out = yamlio.dump(obj, self.style.mapping_indent, self.style.seq_offset)
        return out.rstrip('\n').split('\n')

    def entry_lines(self, key: Any, value: Any) -> list[str]:
        return self.dump_lines({key: value})

    def item_lines(self, value: Any) -> list[str]:
        lines = self.dump_lines({'_': [value]})[1:]
        d = len(lines[0]) - len(lines[0].lstrip(' '))
        return [line[d:] for line in lines]

    def place(self, lines: list[str], col: int, first_inline: bool) -> str:
        """Lines at ``col``; the first one without indent when it continues a line."""
        out = []
        for n, line in enumerate(lines):
            pad = '' if (n == 0 and first_inline) or not line else ' ' * col
            out.append(pad + line + '\n')
        return ''.join(out)

    def add(self, start: int, end: int, text: str) -> None:
        if end == len(self.text) and not self.text.endswith('\n') and text.endswith('\n'):
            text = text[:-1]
        self.edits.append((start, end, text))

    # ---- scalars

    def scalar_token(self, line: int, col: int) -> tuple[int, int, str] | None:
        """(start, end, quote) of a one-line scalar token at (line, col), or None."""
        s = self.lines[line]
        if col >= len(s):
            return None
        c = s[col]
        if c in '&*!|>[{':
            return None
        if c in '\'"':
            i = col + 1
            while i < len(s):
                if c == "'" and s[i] == "'":
                    if i + 1 < len(s) and s[i + 1] == "'":
                        i += 2
                        continue
                    return col, i + 1, c
                if c == '"' and s[i] == '\\':
                    i += 2
                    continue
                if c == '"' and s[i] == '"':
                    return col, i + 1, c
                i += 1
            return None
        m = re.search(r'\s#', s[col:])
        end = col + (m.start() if m else len(s) - col)
        while end > col and s[end - 1] in ' \t':
            end -= 1
        return col, end, ''

    def scalar_text(self, value: Any, quote: str) -> str:
        if (
            isinstance(value, str)
            and '\n' not in value
            and not yamlio.PLACEHOLDER_RE.search(value)
        ):
            if quote == "'":
                return "'" + value.replace("'", "''") + "'"
            if quote == '"':
                return json.dumps(value, ensure_ascii=False)
        lines = self.entry_lines('k', value)
        if len(lines) != 1 or not lines[0].startswith('k: '):
            raise _Fallback
        return lines[0][3:]

    def replace_scalar(self, line: int, col: int, single_line: bool, value: Any) -> None:
        if not single_line or isinstance(value, dict | list):
            raise _Fallback
        tok = self.scalar_token(line, col)
        if tok is None:
            raise _Fallback
        start, end, quote = tok
        self.add(self.off(line, start), self.off(line, end), self.scalar_text(value, quote))

    # ---- recursion

    def patch(self, rt: Any, old: Any, new: Any, path: list[Any], replace: Replace) -> None:
        if same(old, new):
            return
        try:
            if (
                isinstance(rt, CommentedMap)
                and isinstance(old, dict)
                and isinstance(new, dict)
                and not rt.fa.flow_style()
                and new
            ):
                self.patch_map(rt, old, new, path)
                return
            if (
                isinstance(rt, CommentedSeq)
                and isinstance(old, list)
                and isinstance(new, list)
                and not rt.fa.flow_style()
                and new
            ):
                self.patch_seq(rt, old, new, path)
                return
        except _Fallback:
            pass
        replace(new)

    def patch_map(
        self, rt: CommentedMap, old: dict[Any, Any], new: dict[Any, Any], path: list[Any]
    ) -> None:
        # Keys written in this mapping; a merge key (``<<: *x``) brings in others.
        own = getattr(rt, '_ok', None)
        keys: dict[Any, Any] = {}
        for rk in rt:
            if own is not None and rk not in own:
                continue
            for k in old:
                if k not in keys and same(k, _plain(rk)):
                    keys[k] = rk
        if any(k not in keys and (k not in new or not same(old[k], new[k])) for k in old):
            raise _Fallback
        removed = [k for k in old if k not in new]
        survivors = [k for k in old if k in new and k in keys]
        added = [k for k in new if k not in old]
        if added and not survivors:
            raise _Fallback
        # Check every deletion is possible before queuing anything at this level.
        deletions = [self.key_deletion(rt, keys[k]) for k in removed]
        mark = len(self.edits)
        try:
            for d in deletions:
                self.add(*d)
            for k in survivors:
                if same(old[k], new[k]):
                    continue
                rk = keys[k]
                child = rt[rk]
                kline, kcol = rt.lc.key(rk)
                vline, vcol = rt.lc.value(rk)

                def rep(v: Any, rk: Any = rk, k: Any = k) -> None:
                    self.replace_key(rt, rk, k, v, [*path, k])

                if isinstance(child, CommentedMap | CommentedSeq):
                    self.patch(child, old[k], new[k], [*path, k], rep)
                    continue
                single = vline == kline and self.block_end(kline, kcol) == kline + 1
                try:
                    self.replace_scalar(vline, vcol, single, new[k])
                except _Fallback:
                    rep(new[k])
            for k in added:
                self.insert_key(rt, keys, old, new, k, path)
        except _Fallback:
            del self.edits[mark:]
            raise

    def key_span(self, rt: CommentedMap, rk: Any) -> tuple[int, int, int]:
        line, col = rt.lc.key(rk)
        return line, col, self.trim(line, self.block_end(line, col))

    def key_deletion(self, rt: CommentedMap, rk: Any) -> tuple[int, int, str]:
        line, col = rt.lc.key(rk)
        if not self.at_line_start(line, col):
            raise _Fallback  # the first key of a sequence item shares the dash's line
        raw_end = self.block_end(line, col)
        end = self.trim(line, raw_end)
        # Comment lines indented under the key belong to it.
        while end < raw_end and self.lines[end].strip() and self.indent(end) > col:
            end += 1
        start = line
        if start > 0 and not self.lines[start - 1].strip():
            start -= 1
        elif end < len(self.lines) and not self.lines[end].strip() and end + 1 < len(self.lines):
            end += 1
        return self.line_start(start), self.line_start(end), ''

    def replace_key(self, rt: CommentedMap, rk: Any, k: Any, value: Any, path: list[Any]) -> None:
        line, col, end = self.key_span(rt, rk)
        text = self.place(self.entry_lines(k, value), col, first_inline=True)
        self.add(self.off(line, col), self.line_start(end), text)
        if end - line > 1 or isinstance(value, dict | list):
            self.notes.append(Note(_path(path), "This block was rewritten in the file's style."))

    def insert_key(
        self,
        rt: CommentedMap,
        keys: dict[Any, Any],
        old: dict[Any, Any],
        new: dict[Any, Any],
        k: Any,
        path: list[Any],
    ) -> None:
        order = list(new)
        anchor = None
        for prev in reversed(order[: order.index(k)]):
            if prev in keys and prev in new:
                anchor = prev
                break
        if anchor is None:
            anchor = [x for x in old if x in new and x in keys][-1]
        line, col, end = self.key_span(rt, keys[anchor])
        if not self.at_line_start(line, col):
            col = rt.lc.key(keys[anchor])[1]
        lines = self.entry_lines(k, new[k])
        text = self.place(lines, col, first_inline=False)
        if len(lines) > 1 and line > 0 and not self.lines[line - 1].strip():
            text = '\n' + text  # siblings are separated by blank lines
        self.add(self.line_start(end), self.line_start(end), text)

    def item_span(self, rt: CommentedSeq, i: int) -> tuple[int, int, int]:
        """(dash line, dash col, end line) of item ``i``."""
        line, col = rt.lc.item(i)
        s = self.lines[line]
        j = col - 1
        while j >= 0 and s[j] == ' ':
            j -= 1
        if j < 0 or s[j] != '-':
            # The item's content starts on the line after its dash.
            line -= 1
            while line >= 0 and not self.content(line):
                line -= 1
            s = self.lines[line].rstrip()
            if not s.endswith('-'):
                raise _Fallback
            j = len(s) - 1
        if not self.at_line_start(line, j):
            raise _Fallback  # nested sequences on one line (``- - x``)
        if i + 1 < len(rt):
            nline, _ = self.item_span(rt, i + 1)[:2]
            end = self.trim(line, nline)
        else:
            end = self.trim(line, self.block_end(line, j))
        return line, j, end

    def patch_seq(self, rt: CommentedSeq, old: list[Any], new: list[Any], path: list[Any]) -> None:
        if len(old) == len(new):
            for i, (o, n) in enumerate(zip(old, new, strict=True)):
                if same(o, n):
                    continue
                dline, dcol, end = self.item_span(rt, i)
                child = rt[i]

                def rep(
                    v: Any, dline: int = dline, dcol: int = dcol, end: int = end, i: int = i
                ) -> None:
                    text = self.place(self.item_lines(v), dcol, first_inline=True)
                    self.add(self.off(dline, dcol), self.line_start(end), text)
                    self.notes.append(
                        Note(_path([*path, i]), "This item was rewritten in the file's style.")
                    )

                if isinstance(child, CommentedMap | CommentedSeq):
                    self.patch(child, o, n, [*path, i], rep)
                    continue
                vline, vcol = rt.lc.item(i)
                try:
                    self.replace_scalar(vline, vcol, end == dline + 1, n)
                except _Fallback:
                    rep(n)
            return
        if len(new) > len(old) and old and same(new[: len(old)], old):
            _, dcol, end = self.item_span(rt, len(old) - 1)
            text = ''.join(
                self.place(self.item_lines(v), dcol, first_inline=False) for v in new[len(old) :]
            )
            self.add(self.line_start(end), self.line_start(end), text)
            return
        if len(new) == len(old) - 1:
            for j in range(len(old)):
                if same(old[:j] + old[j + 1 :], new):
                    dline, _, end = self.item_span(rt, j)
                    self.add(self.line_start(dline), self.line_start(end), '')
                    return
        raise _Fallback

    # ---- result

    def result(self) -> str:
        edits = sorted(self.edits, key=lambda e: (e[0], e[1]), reverse=True)
        for (s1, _e1, _), (_s2, e2, _) in zip(edits, edits[1:], strict=False):
            if e2 > s1:
                raise EditError('Internal error: overlapping edits')
        text = self.text
        for start, end, repl in edits:
            text = text[:start] + repl + text[end:]
        return text


def _root_replace(value: Any) -> None:
    raise EditError("The config's top level must stay a mapping.")


def apply(text: str, new_doc: Any) -> Edit:
    """Patch ``text`` so that it parses to ``new_doc``, changing as little as possible."""
    bom = text.startswith('﻿')
    body = text[1:] if bom else text
    crlf = '\r\n' in body
    if crlf:
        body = body.replace('\r\n', '\n')
    old_doc = load(body)
    if same(old_doc, new_doc):
        return Edit(text, False)
    if not isinstance(old_doc, dict) or not isinstance(new_doc, dict):
        raise EditError('The config must be a YAML mapping.')
    p = _Patcher(body)
    p.patch(p.rt, old_doc, new_doc, [], _root_replace)
    out = p.result()
    got = load(out)
    if not same(got, new_doc):
        raise EditError(
            'The edit could not be applied without changing other parts of the file, so '
            'nothing was written. This can happen with YAML anchors, merge keys or block '
            'scalars near the edited keys.'
        )
    if crlf:
        out = out.replace('\n', '\r\n')
    if bom:
        out = '﻿' + out
    return Edit(out, True, p.notes)


def resources(doc: Any) -> dict[str, Any]:
    res = doc.get('resources') if isinstance(doc, dict) else None
    if res is None:
        return {}
    if not isinstance(res, dict):
        raise EditError("The config's resources: is not a mapping.")
    return res


def set_resources(text: str, changes: dict[str, dict[str, Any] | None]) -> Edit:
    """Add, replace or (with ``None``) delete resources by id."""
    doc = load(text) or {}
    if not isinstance(doc, dict):
        raise EditError('The config must be a YAML mapping.')
    new = copy.deepcopy(doc)
    res = dict(resources(doc))
    for rid, value in changes.items():
        if value is None:
            if rid not in res:
                raise EditError(f'The config has no resource called {rid!r}.')
            del res[rid]
        else:
            res[rid] = value
    new['resources'] = res
    return apply(text, new)
