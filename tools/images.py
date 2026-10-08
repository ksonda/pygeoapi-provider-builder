"""Build the sandbox image (``make images``), or print its tag (``--tag``).

Usage: python tools/images.py [--tag]
"""

from __future__ import annotations

import argparse
import sys

from provider_builder.images import build_sandbox, sandbox_tag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tag', action='store_true', help='print the tag; build nothing')
    args = parser.parse_args()
    print(sandbox_tag() if args.tag else build_sandbox())
    return 0


if __name__ == '__main__':
    sys.exit(main())
