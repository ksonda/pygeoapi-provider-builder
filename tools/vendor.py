"""Check out the source repositories the docs cite, at their pinned commits.

Reads tools/vendor.txt. A missing repository is fetched (shallow, by commit)
into vendor/<name>. An existing one is checked against its pinned commit and
for local changes: pygeoapi's SQLiteGPKG provider, for one, rewrites the
.gpkg files it opens (docs/pygeoapi-notes.md §5.4).

Usage: python tools/vendor.py [--check] [NAME...]
Exit status 1 when a checkout is missing (with --check), at another commit or
modified.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
SOURCES = ROOT / 'tools' / 'vendor.txt'


class Source(NamedTuple):
    name: str
    url: str
    commit: str


def sources() -> list[Source]:
    out = []
    for line in SOURCES.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            name, url, commit = line.split()
            out.append(Source(name, url, commit))
    return out


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ['git', *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def head(path: Path) -> str | None:
    if not (path / '.git').exists():
        return None
    try:
        return git('rev-parse', 'HEAD', cwd=path)
    except subprocess.CalledProcessError:  # an empty repository after a failed fetch
        return None


def fetch(src: Source) -> None:
    dest = VENDOR / src.name
    dest.mkdir(parents=True, exist_ok=True)
    git('init', '-q', cwd=dest)
    git('fetch', '-q', '--depth', '1', src.url, src.commit, cwd=dest)
    git('checkout', '-q', 'FETCH_HEAD', cwd=dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('names', nargs='*')
    parser.add_argument('--check', action='store_true', help='verify only; fetch nothing')
    args = parser.parse_args()
    problems = 0
    for src in sources():
        if args.names and src.name not in args.names:
            continue
        dest = VENDOR / src.name
        current = head(dest)
        if current is None and not args.check:
            print(f'fetching {src.name} at {src.commit[:12]}')
            fetch(src)
            current = head(dest)
        if current is None:
            print(f'MISSING   {src.name} (run make vendor)')
        elif current != src.commit:
            print(f'DIFFERENT {src.name}: at {current[:12]}, pinned {src.commit[:12]}')
        elif changed := git('status', '--porcelain', cwd=dest):
            paths = [line.split(maxsplit=1)[-1] for line in changed.splitlines()]
            print(f'MODIFIED  {src.name}: {", ".join(paths)}')
        else:
            print(f'ok        {src.name} {src.commit[:12]}')
            continue
        problems += 1
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
