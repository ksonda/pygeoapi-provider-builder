"""The strict placeholder formatter for URL templates and parameter formats.

PLAN.md §7.2 and PROMPT.md §7: only ``{name}`` and ``{name:strftime}`` tokens,
names from a fixed set per context, strftime specs only on datetimes and only
from an allow-list of directives, ``{{`` and ``}}`` for literal braces. Values
in URL templates are URL-encoded. There is no attribute or index access, and
``str.format`` is never used.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

TOKEN = re.compile(r'\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}|[{}]')
#: strftime directives that don't depend on the locale.
ALLOWED_DIRECTIVES = frozenset('YmdHMSfjyzbBaAIpZ%')


class FormatError(ValueError):
    """A template that uses an unknown token, a bad format spec or a stray brace."""


@dataclass(frozen=True)
class Token:
    name: str
    spec: str | None


@dataclass(frozen=True)
class Template:
    text: str
    parts: tuple[str | Token, ...]

    @property
    def names(self) -> set[str]:
        return {p.name for p in self.parts if isinstance(p, Token)}


def check_strftime(spec: str) -> None:
    if '%' not in spec:
        raise FormatError(f'{spec!r} has no strftime directive')
    i = 0
    while i < len(spec):
        if spec[i] != '%':
            i += 1
            continue
        if i + 1 == len(spec):
            raise FormatError(f'{spec!r} ends with a lone %')
        if spec[i + 1] not in ALLOWED_DIRECTIVES:
            raise FormatError(
                f'%{spec[i + 1]} is not allowed in {spec!r}; use %Y, %m, %d, %H, %M, %S and such'
            )
        i += 2


def parse(text: str, allowed: Iterable[str], datetime_names: Iterable[str] = ()) -> Template:
    allowed, datetime_names = set(allowed), set(datetime_names)
    parts: list[str | Token] = []
    literal: list[str] = []
    pos = 0
    for m in TOKEN.finditer(text):
        literal.append(text[pos : m.start()])
        pos = m.end()
        whole = m.group(0)
        if whole in ('{{', '}}'):
            literal.append(whole[0])
            continue
        if whole in ('{', '}'):
            raise FormatError(
                f'Stray {whole!r} in {text!r}; write {whole * 2!r} for a literal brace'
            )
        name, spec = m.group(1), m.group(2)
        if name not in allowed:
            known = ', '.join(sorted(allowed)) or 'none'
            raise FormatError(
                f'Unknown placeholder {{{name}}} in {text!r} (allowed here: {known})'
            )
        if spec is not None:
            if name not in datetime_names:
                raise FormatError(f'{{{name}:{spec}}}: only date and time values take a format')
            check_strftime(spec)
        if literal:
            parts.append(''.join(literal))
            literal = []
        parts.append(Token(name, spec))
    literal.append(text[pos:])
    if any(literal):
        parts.append(''.join(literal))
    return Template(text, tuple(p for p in parts if p != ''))


def to_text(value: Any, spec: str | None = None) -> str:
    """Plain text for one value: ISO 8601 for datetimes, shortest round trip for floats."""
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo is not None else value
        return moment.strftime(spec or '%Y-%m-%dT%H:%M:%SZ')
    if isinstance(value, date):
        day: date = value
        return day.strftime(spec) if spec else day.isoformat()
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def render(
    template: Template,
    values: Mapping[str, Any],
    encode: Callable[[str], str] | None = None,
) -> str:
    out = []
    for part in template.parts:
        if isinstance(part, str):
            out.append(part)
            continue
        if part.name not in values:
            raise FormatError(f'No value for {{{part.name}}} in {template.text!r}')
        text = to_text(values[part.name], part.spec)
        out.append(encode(text) if encode else text)
    return ''.join(out)


def url_encode(text: str) -> str:
    return quote(text, safe='')


def render_url(template: Template, values: Mapping[str, Any]) -> str:
    """Fill a URL template; every value is percent-encoded, so it can't add path or query."""
    return render(template, values, encode=url_encode)
