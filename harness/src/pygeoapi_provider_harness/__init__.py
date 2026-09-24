"""Probe worker and contract checks for pygeoapi providers.

This package runs *inside the sandbox* (the pinned pygeoapi image, or a
subprocess in Phase 1). It never runs in the Provider Builder backend process.
"""

__version__ = '0.1.0'

#: Version of the JSON job/result protocol spoken by ``worker``.
PROTOCOL = 1
