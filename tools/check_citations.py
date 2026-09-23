"""Check `path:line` source citations in the project's Markdown docs.

Citations look like `pygeoapi/provider/csv_.py:57` (relative to the pinned
pygeoapi checkout in vendor/pygeoapi) or `pygeoapi-plugins:pygeoapi_plugins/x.py:12`
(relative to vendor/<repo>). A range such as `:57-60` is allowed.

For every citation the checker verifies that the file exists and the line is in
range (errors), and that at least one of the other code spans on the same
Markdown line (a config key, method or identifier) appears within +/-3 lines of
the cited line (a warning when none does).

Usage: python tools/check_citations.py [--strict] [FILES...]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
DEFAULT_REPO = 'pygeoapi'
WINDOW = 3

CODE_SPAN = re.compile(r'`([^`\n]+)`')
CITATION = re.compile(
    r'^(?:(?P<repo>[A-Za-z0-9_.-]+):)?'
    r'(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z][A-Za-z0-9]*)'
    r':(?P<start>\d+)(?:-(?P<end>\d+))?$'
)
TOKEN_SPLIT = re.compile(r'[\s/|,=(){}\[\]<>"\']+')


def candidates(span: str) -> set[str]:
    """Strings to look for near a cited line, derived from one code span."""
    out = set()
    for part in TOKEN_SPLIT.split(span):
        part = part.strip('.:;*`@')
        if len(part) < 2:
            continue
        out.add(part)
        if '.' in part:
            out.add(part.rsplit('.', 1)[-1])
    return {c for c in out if len(c) >= 2}


def resolve(repo: str | None, path: str) -> Path | None:
    if repo and (VENDOR / repo).is_dir():
        return VENDOR / repo / path
    if repo:  # "repo:" prefix that is not a vendored repo, e.g. a scheme
        return None
    return VENDOR / DEFAULT_REPO / path


def check_file(md: Path) -> tuple[int, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    count = 0
    cache: dict[Path, list[str]] = {}
    for lineno, text in enumerate(md.read_text(encoding='utf-8').splitlines(), 1):
        spans = CODE_SPAN.findall(text)
        cites = [(s, CITATION.match(s)) for s in spans]
        cites = [(s, m) for s, m in cites if m]
        if not cites:
            continue
        others = [s for s in spans if not CITATION.match(s)]
        wanted = set().union(*(candidates(s) for s in others)) if others else set()
        for span, m in cites:
            target = resolve(m['repo'], m['path'])
            if target is None:
                continue
            count += 1
            where = f'{md.relative_to(ROOT)}:{lineno}'
            if not target.is_file():
                errors.append(f'{where}: missing file `{span}`')
                continue
            lines = cache.setdefault(
                target, target.read_text(encoding='utf-8', errors='replace').splitlines())
            start = int(m['start'])
            end = int(m['end'] or start)
            if not 1 <= start <= end <= len(lines):
                errors.append(f'{where}: line out of range `{span}` (file has {len(lines)})')
                continue
            if not wanted:
                continue
            lo, hi = max(0, start - 1 - WINDOW), min(len(lines), end + WINDOW)
            window = '\n'.join(lines[lo:hi])
            if not any(c in window for c in wanted):
                shown = ', '.join(sorted(wanted))[:80]
                warnings.append(f'{where}: none of [{shown}] near `{span}`')
    return count, errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--strict', action='store_true',
                        help='treat warnings as errors')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='only print the summary and errors')
    args = parser.parse_args()
    files = args.files or sorted((ROOT / 'docs').rglob('*.md'))
    if not (VENDOR / DEFAULT_REPO).is_dir():
        print('vendor/pygeoapi is missing; see README.md (Phase 0 setup)')
        return 2
    total, all_errors, all_warnings = 0, [], []
    for md in files:
        md = md.resolve()
        count, errors, warnings = check_file(md)
        total += count
        all_errors += errors
        all_warnings += warnings
    for e in all_errors:
        print('ERROR', e)
    if not args.quiet:
        for w in all_warnings:
            print('WARN ', w)
    print(f'{total} citations checked in {len(files)} files: '
          f'{len(all_errors)} errors, {len(all_warnings)} warnings')
    failed = all_errors or (args.strict and all_warnings)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
