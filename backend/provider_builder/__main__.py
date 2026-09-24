"""Run the backend: ``python -m provider_builder [--port 8765] [--reload]``.

Binds to 127.0.0.1 only (PROMPT.md §1, ADR-0012).
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    uvicorn.run(
        'provider_builder.app:create_app',
        factory=True,
        host='127.0.0.1',
        port=args.port,
        reload=args.reload,
    )


if __name__ == '__main__':
    main()
