"""Write the backend's OpenAPI document for the typed frontend client (ADR-0011).

Usage: python tools/export_openapi.py [--check] frontend/src/api/openapi.json
With --check, write nothing and exit 1 when the file is out of date.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from provider_builder.app import create_app
from provider_builder.runner import SubprocessRunner
from provider_builder.service import Service
from provider_builder.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('out', type=Path)
    parser.add_argument('--check', action='store_true', help='compare only; write nothing')
    args = parser.parse_args()
    settings = load_settings(Path(__file__).resolve().parent.parent / 'provider-builder.yaml')
    service = Service(settings, runner=SubprocessRunner(settings.sandbox, settings.data_roots))
    doc = create_app(service=service, check_sandbox=False).openapi()
    text = json.dumps(doc, indent=2, sort_keys=True) + '\n'
    out: Path = args.out
    if args.check:
        if not out.is_file() or out.read_text(encoding='utf-8') != text:
            print(f'{out} is out of date: run make api-types-update')
            return 1
        print(f'{out} is up to date ({len(doc["paths"])} paths)')
        return 0
    out.write_text(text, encoding='utf-8')
    print(f'wrote {out} ({len(doc["paths"])} paths)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
