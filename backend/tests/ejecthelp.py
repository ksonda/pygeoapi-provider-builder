"""Eject a spec into a folder, load the package from there, and compare it with the engine."""

from __future__ import annotations

import copy
import importlib
import itertools
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from provider_builder.eject import EjectRequest, eject_files, write
from pygeoapi.util import yaml_load

RUNTIME_TESTS = Path(__file__).resolve().parents[2] / 'runtime' / 'tests'
if str(RUNTIME_TESTS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_TESTS))  # the engine's fixtures: specs, sqlfixtures, …

#: Package names must differ across the session: Python caches imported modules.
COUNTER = itertools.count(1)

Call = tuple[str, dict[str, Any]]


@dataclass
class Ejected:
    folder: Path
    package: str
    files: dict[str, str]

    @property
    def src(self) -> Path:
        return self.folder / 'src'

    def provider_def(self) -> dict[str, Any]:
        """The example config's provider, read the way pygeoapi reads its config."""
        snippet = self.folder / 'examples' / 'pygeoapi-config.snippet.yml'
        with snippet.open(encoding='utf-8') as fh:
            (resource,) = yaml_load(fh).values()
        return dict(resource['providers'][0])

    def provider(self) -> Any:
        pdef = self.provider_def()
        module = importlib.import_module(f'{self.package}.provider')
        return getattr(module, pdef['name'].rsplit('.', 1)[1])(pdef)


def eject(root: Path, spec: dict[str, Any] | str, stem: str, version: str = '1.0.0') -> Ejected:
    """Eject a spec (a mapping, or its YAML text) into ``root`` and put its src on the path."""
    text = spec if isinstance(spec, str) else yaml.safe_dump(spec, sort_keys=False)
    pid = f'{stem}-{next(COUNTER)}'
    files = eject_files(text, version, EjectRequest(id=pid))
    folder = write(files, root / pid)
    sys.path.insert(0, str(folder / 'src'))
    package = next(
        name.split('/')[1]
        for name in files
        if name.startswith('src/') and name.endswith('/provider.py')
    )
    return Ejected(folder, package, files)


def outcome(provider: Any, method: str, kwargs: dict[str, Any]) -> tuple[Any, ...]:
    """A call's result, or the error it raises: its class and user message."""
    try:
        return ('ok', getattr(provider, method)(**copy.deepcopy(kwargs)))
    except Exception as err:  # noqa: BLE001 - comparing failures is the point
        return ('error', type(err).__name__, getattr(err, 'user_msg', None))


def differences(engine: Any, ejected: Any, calls: Iterable[Call]) -> list[tuple[Any, ...]]:
    """Every call whose outcome differs between the spec engine and the ejected provider."""
    out = []
    for method, kwargs in calls:
        a, b = outcome(engine, method, kwargs), outcome(ejected, method, kwargs)
        if a != b:
            out.append((method, kwargs, a, b))
    return out
