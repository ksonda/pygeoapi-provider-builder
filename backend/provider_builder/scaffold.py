"""Scaffolds for code-built providers (J5, PLAN.md §7.5).

A scaffold is a pip-installable package in its own Git repository under the
workspace (``workspace/providers/<id>/``), from ``scaffold_templates/``.

Method signatures and docstrings come from the **installed** pygeoapi, so a
scaffold matches the version the sandbox runs (the health check compares them).
For most methods that's the base class. ``BaseProvider.query()`` takes no
arguments in 0.24.0, though, while the API passes 13 keywords (notes §5.1), so
query signatures come from the core providers the API was written against:
GeoJSON for features and records, rasterio for coverages, the WMS facade for
maps. List and dict defaults become None, which the API always overrides anyway.

Values reach the generated code only as literals (``repr``), YAML and TOML as
JSON strings, docstrings escaped; identifiers are checked first (PLAN.md §7.5).
"""

from __future__ import annotations

import inspect
import json
import keyword
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pygeoapi
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from . import SOURCE_REPOSITORY

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / 'scaffold_templates'
SCAFFOLD_VERSION = 1

ProviderType = Literal['feature', 'record', 'coverage', 'edr', 'tile', 'map']
Style = Literal['rest', 'sql', 'files']
EdrQuery = Literal['position', 'radius', 'area', 'cube', 'trajectory', 'corridor', 'locations']

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
PACKAGE = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')
CLASS = re.compile(r'^[A-Z][A-Za-z0-9]{0,62}$')

OGC_API = {
    'feature': 'features',
    'record': 'records',
    'coverage': 'coverages',
    'edr': 'edr',
    'tile': 'tiles',
    'map': 'maps',
}
DATA_EXAMPLES = {
    'rest': 'https://api.example.gov/v1/items',
    'sql': 'postgresql://reader@db.example.org:5432/gis',
    'files': '/data/items.json',
}
DATA_HELP = {
    'rest': 'The URL of the source API. Declare its host under egress.hosts.',
    'sql': 'A database URL; keep the password in an environment variable, ${DB_PASSWORD}.',
    'files': 'The file the provider reads, as the pygeoapi server sees it.',
}
EDR_DOCS = {
    'position': 'The values at a point (``wkt`` is a shapely Point).',
    'radius': 'The values within ``within`` ``within_units`` of a point.',
    'area': 'The values inside a polygon (``wkt`` is a shapely Polygon).',
    'cube': 'The values inside ``bbox``.',
    'trajectory': 'The values along a line (``wkt`` is a shapely LineString).',
    'corridor': 'The values in a corridor around a line.',
    'locations': (
        'Without ``location_id``, a GeoJSON FeatureCollection of the locations;\n'
        "with one, that location's values."
    ),
}


class ScaffoldError(ValueError):
    pass


class ScaffoldRequest(BaseModel):
    provider_type: ProviderType
    id: str = Field(description='The provider id (a slug): the workspace folder and catalog entry')
    title: str
    description: str = ''
    package: str | None = Field(default=None, description='The Python package name')
    class_name: str | None = None
    style: Style = Field(default='rest', description='Where the data comes from')
    query_types: list[EdrQuery] = Field(default=['position'], description='EDR only')


def default_package(pid: str) -> str:
    name = re.sub(r'[^a-z0-9]+', '_', pid.lower()).strip('_')
    name = name if name and not name[0].isdigit() else f'p_{name}'
    return f'{name}_provider' if not name.endswith('provider') else name


def default_class(pid: str) -> str:
    parts = re.split(r'[^A-Za-z0-9]+', pid)
    name = ''.join(p[:1].upper() + p[1:] for p in parts if p) or 'My'
    name = name if name[0].isalpha() else f'P{name}'
    return name if name.endswith('Provider') else f'{name}Provider'


def check_names(req: ScaffoldRequest) -> tuple[str, str]:
    if not SLUG.match(req.id):
        raise ScaffoldError(f'{req.id!r} is not a provider id: lower-case letters, digits, -')
    if not req.title.strip():
        raise ScaffoldError('A title is required.')
    pkg = req.package or default_package(req.id)
    cls = req.class_name or default_class(req.id)
    if not PACKAGE.match(pkg) or keyword.iskeyword(pkg) or not pkg.isidentifier():
        raise ScaffoldError(f'{pkg!r} is not a Python package name: lower-case letters, digits, _')
    if not CLASS.match(cls) or keyword.iskeyword(cls) or not cls.isidentifier():
        raise ScaffoldError(f'{cls!r} is not a class name: CamelCase letters and digits')
    if pkg in sys.stdlib_module_names or pkg in ('pygeoapi', 'pygeoapi_spec_providers'):
        raise ScaffoldError(f'{pkg!r} would shadow another package; pick another name')
    if req.provider_type == 'edr' and not req.query_types:
        raise ScaffoldError('An EDR provider needs at least one query type.')
    return pkg, cls


# --- introspection ---------------------------------------------------------------------------


def signature_text(fn: Callable[..., Any]) -> str:
    """The signature, with mutable defaults replaced by None."""
    sig = inspect.signature(fn)
    params = []
    for p in sig.parameters.values():
        if p.default is not inspect.Parameter.empty and isinstance(p.default, list | dict | set):
            p = p.replace(default=None)
        params.append(p.replace(annotation=inspect.Parameter.empty))
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


def method(fn: Callable[..., Any]) -> dict[str, str]:
    doc = inspect.getdoc(fn) or ''
    # Drop the docstring's own :returns: lines; the templates say what to return.
    lines = [line for line in doc.splitlines() if not line.startswith(':returns')]
    return {'signature': signature_text(fn), 'doc': '\n'.join(lines).strip()}


def methods_for(provider_type: str) -> dict[str, dict[str, str]]:
    from pygeoapi.provider.base import BaseProvider
    from pygeoapi.provider.base_mvt import BaseMVTProvider
    from pygeoapi.provider.geojson import GeoJSONProvider

    if provider_type in ('feature', 'record'):
        return {
            'get_fields': method(BaseProvider.get_fields),
            'query': method(GeoJSONProvider.query),
            'get': method(BaseProvider.get),
        }
    if provider_type == 'coverage':
        from pygeoapi.provider.rasterio_ import RasterioProvider

        return {
            'get_fields': method(BaseProvider.get_fields),
            'query': method(RasterioProvider.query),
        }
    if provider_type == 'map':
        from pygeoapi.provider.wms_facade import WMSFacadeProvider

        return {'query': method(WMSFacadeProvider.query)}
    if provider_type == 'edr':
        return {'get_fields': method(BaseProvider.get_fields)}
    return {
        name: method(getattr(BaseMVTProvider, name))
        for name in ('get_layer', 'get_tiling_schemes', 'get_tiles_service', 'get_tiles')
    }


# --- rendering -------------------------------------------------------------------------------


def _doc(text: str, indent: int = 0) -> str:
    """Docstring text: no closing quotes, no stray escapes, continuation lines indented."""
    safe = str(text).replace('\\', '\\\\').replace('"""', '\\"\\"\\"')
    pad = ' ' * indent
    lines = safe.splitlines() or ['']
    return '\n'.join([lines[0], *[(pad + line) if line else '' for line in lines[1:]]])


def environment() -> Environment:
    env = Environment(  # noqa: S701 - renders Python, YAML and TOML, never HTML
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters['py'] = repr
    env.filters['json'] = lambda v: json.dumps(v, ensure_ascii=False)
    env.filters['doc'] = _doc
    return env


def context(req: ScaffoldRequest) -> dict[str, Any]:
    pkg, cls = check_names(req)
    base: list[str] = [] if req.provider_type in ('edr', 'tile') else ['BaseProvider']
    if req.style == 'rest':
        base += ['ProviderConnectionError', 'ProviderQueryError']
    return {
        'type': req.provider_type,
        'id': req.id,
        'title': req.title.strip(),
        'description': req.description.strip() or req.title.strip(),
        'pkg': pkg,
        'dist': pkg.replace('_', '-'),
        'class_name': cls,
        'style': req.style,
        'query_types': list(dict.fromkeys(req.query_types)),
        'edr_docs': EDR_DOCS,
        'methods': methods_for(req.provider_type),
        'base_imports': sorted(base, key=lambda n: (n != 'BaseProvider', n)),
        'pygeoapi_version': pygeoapi.__version__,
        'source_repository': SOURCE_REPOSITORY,
        'ogc_api': OGC_API[req.provider_type],
        'data_example': DATA_EXAMPLES[req.style],
        'data_help': DATA_HELP[req.style],
    }


def render(req: ScaffoldRequest) -> dict[str, str]:
    """Every file of the package, by path relative to its root."""
    env = environment()
    ctx = context(req)
    kind = 'feature' if req.provider_type == 'record' else req.provider_type
    pkg = ctx['pkg']

    def r(name: str) -> str:
        return env.get_template(name).render(**ctx)

    answers = {
        'scaffold_version': SCAFFOLD_VERSION,
        'pygeoapi': pygeoapi.__version__,
        **req.model_dump(),
        'package': pkg,
        'class_name': ctx['class_name'],
    }
    return {
        'pyproject.toml': r('package/pyproject.toml.j2'),
        'README.md': r('package/README.md.j2'),
        'CHANGELOG.md': r('package/CHANGELOG.md.j2'),
        '.gitignore': r('package/gitignore.j2'),
        'Dockerfile.test': r('package/Dockerfile.test.j2'),
        'examples/pygeoapi-config.snippet.yml': r('package/config-snippet.yml.j2'),
        f'src/{pkg}/__init__.py': r('package/init.py.j2'),
        f'src/{pkg}/provider.py': r(f'provider/{kind}.py.j2'),
        f'src/{pkg}/descriptor.yaml': r('package/descriptor.yaml.j2'),
        'tests/test_contract.py': r('package/test_contract.py.j2'),
        'tests/fixtures/README.md': 'Test data for the provider (no network in tests).\n',
        '.provider-builder.json': json.dumps(answers, indent=2) + '\n',
    }


# --- tidying ---------------------------------------------------------------------------------


def ruff_binary() -> str:
    found = shutil.which('ruff', path=str(Path(sys.executable).parent)) or shutil.which('ruff')
    if not found:
        raise ScaffoldError('ruff is not installed in the backend environment')
    return found


def ruff(args: list[str], source: str, filename: str, config: Path | None = None) -> str:
    """Run ruff over stdin; ruff only parses the code, it never runs it."""
    cmd = [ruff_binary(), *args, '--stdin-filename', filename]
    if config is not None:
        cmd += ['--config', str(config)]
    proc = subprocess.run(  # noqa: S603 - our ruff, fixed arguments, code on stdin
        [*cmd, '-'], input=source, capture_output=True, text=True, timeout=30, check=False
    )
    if proc.returncode not in (0, 1):
        raise ScaffoldError(f'ruff failed: {proc.stderr.strip()[:500]}')
    return proc.stdout


def tidy(files: dict[str, str], config: Path) -> dict[str, str]:
    """Sort imports and format the Python files, as the package's own ruff config says."""
    out = dict(files)
    for name, source in files.items():
        if name.endswith('.py'):
            imports = ['check', '--fix-only', '--select', 'I', '--exit-zero']
            sorted_ = ruff(imports, source, name, config)
            out[name] = ruff(['format'], sorted_ or source, name, config)
    return out


def write(req: ScaffoldRequest, root: Path) -> Path:
    """Render, tidy and write the package to ``root/<id>/``, which must not exist."""
    files = render(req)
    folder = root / req.id
    if folder.exists():
        raise ScaffoldError(f'{req.id} already exists in the workspace')
    folder.mkdir(parents=True)
    (folder / 'pyproject.toml').write_text(files['pyproject.toml'], encoding='utf-8')
    files = tidy(files, folder / 'pyproject.toml')
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return folder
