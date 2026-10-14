"""Dev stack only: put every code-built provider's src folder on sys.path.

Production installs a code-built provider as a package (its Dockerfile.test
does that with /venv/bin/python3 -m pip install). The dev stack mounts the
workspace at /specs instead, so providers made in the code workspace import as
soon as they're published (PLAN.md §7.5). Python imports this module at startup
because PYTHONPATH includes its folder.
"""

import glob
import sys

for _path in sorted(glob.glob('/specs/*/src')):
    if _path not in sys.path:
        sys.path.append(_path)
