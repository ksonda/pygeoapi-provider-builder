"""Write the backend's OpenAPI document for the typed frontend client (ADR-0011).

Usage: python tools/export_openapi.py frontend/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from provider_builder.app import create_app
from provider_builder.runner import SubprocessRunner
from provider_builder.service import Service
from provider_builder.settings import load_settings


def main() -> None:
    settings = load_settings(Path(__file__).resolve().parent.parent / 'provider-builder.yaml')
    service = Service(settings, runner=SubprocessRunner(settings.sandbox, settings.data_roots))
    doc = create_app(service=service, check_sandbox=False).openapi()
    out = Path(sys.argv[1])
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'wrote {out} ({len(doc["paths"])} paths)')


if __name__ == '__main__':
    main()
