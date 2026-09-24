"""Turn sandbox failures into plain-language messages (PLAN.md §6.3)."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

RULES_FILE = Path(__file__).with_name('errors.yaml')


@dataclass(frozen=True)
class Explanation:
    code: str
    message: str
    hint: str | None


class _Default(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return '?'


@cache
def _rules() -> list[dict[str, Any]]:
    rules = yaml.safe_load(RULES_FILE.read_text(encoding='utf-8'))
    for r in rules:
        if 'text' in r:
            r['_re'] = re.compile(r['text'], re.IGNORECASE)
    return list(rules)


def explain(
    error: dict[str, Any], context: dict[str, Any] | None = None, logs: str = ''
) -> Explanation:
    """Explain a worker error (``describe_exception`` output) in plain words."""
    context = {k: v for k, v in (context or {}).items() if v not in (None, '')}
    chain = error.get('chain') or [error]
    types = {str(c.get('type', '')).rsplit('.', 1)[-1] for c in chain}
    haystack = '\n'.join(
        [
            *(str(c.get('message', '')) for c in chain),
            str(error.get('traceback', ''))[-4000:],
            logs[-4000:],
        ]
    )
    for rule in _rules():
        if 'descriptor' in rule and rule['descriptor'] != context.get('descriptor'):
            continue
        if 'types' in rule and not types.intersection(rule['types']):
            continue
        groups: dict[str, str] = {}
        if '_re' in rule:
            m = rule['_re'].search(haystack)
            if not m:
                continue
            groups = {k: v for k, v in m.groupdict().items() if v is not None}
        values = _Default({**{k: str(v) for k, v in context.items()}, **groups})
        fmt = string.Formatter()
        return Explanation(
            code=rule['id'],
            message=fmt.vformat(rule['message'], (), values),
            hint=fmt.vformat(rule['hint'], (), values) if rule.get('hint') else None,
        )
    kind = str(error.get('type', 'Error')).rsplit('.', 1)[-1]
    text = error.get('user_msg') or error.get('message') or ''
    return Explanation(
        code='unexplained',
        message=f'{kind}: {text}'.rstrip(': '),
        hint='The details below show exactly what pygeoapi reported.',
    )
