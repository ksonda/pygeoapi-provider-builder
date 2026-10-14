"""Created providers: a folder and a Git repository each (PLAN.md §7.4 step 6, §7.8).

::

    workspace/providers/<id>/
      descriptor.yaml           generated from every version; Mode A's catalog reads it
      CHANGELOG.md
      <version>/spec.yaml       immutable once saved: configs point at it by path
      <version>/harness.json    the checklist the version was saved with

Each save is a commit tagged ``v<version>``. A spec is data: it's validated
here with the spec engine's own checks, and only ever run in the sandbox.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pygeoapi_spec_providers.spec import Problem, check_text, secret_env, validate

from .settings import Workspace

SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$')
PLACEHOLDER = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)')
PLUGIN = 'pygeoapi_spec_providers.http.HTTPFeatureProvider'
#: Base keys a spec already decides, or that don't apply to a spec-driven source.
HIDDEN = ['editable', 'storage_crs', 'always_xy', 'storage_crs_coordinate_epoch', 'id_field']

#: What each template becomes in Mode A's catalog.
TEMPLATES: dict[str, dict[str, Any]] = {
    'http-features': {
        'plugin': PLUGIN,
        'provider_type': 'feature',
        'ogc_api': ['features'],
        'requires': ['pygeoapi_spec_providers', 'httpx', 'jsonpath'],
        'summary': 'An HTTP JSON API, made with the template wizard.',
    },
    'sql-features': {
        'plugin': 'pygeoapi_spec_providers.sql.SQLFeatureProvider',
        'provider_type': 'feature',
        'ogc_api': ['features'],
        'requires': ['pygeoapi_spec_providers', 'sqlglot', 'sqlalchemy'],
        'summary': 'A SQL query, made with the template wizard.',
    },
    'timeseries-edr': {
        'plugin': 'pygeoapi_spec_providers.timeseries.TimeSeriesEDRProvider',
        'provider_type': 'edr',
        'ogc_api': ['edr'],
        'requires': ['pygeoapi_spec_providers'],
        'summary': 'Time series at monitoring locations, made with the template wizard.',
    },
}
EDR_QUERY_TYPES = ['locations', 'position', 'area', 'cube']


def template_of(doc: dict[str, Any]) -> dict[str, Any]:
    return TEMPLATES.get(str(doc.get('template')), TEMPLATES['http-features'])


def plugin_for(doc: dict[str, Any]) -> str:
    return str(template_of(doc)['plugin'])


def provider_type_for(doc: dict[str, Any]) -> str:
    return str(template_of(doc)['provider_type'])


def requires_for(doc: dict[str, Any]) -> list[str]:
    """The modules a spec's provider imports: the engine plus each source's client."""
    out = list(template_of(doc)['requires'])
    sources: list[dict[str, Any]] = []
    if doc.get('template') == 'timeseries-edr':
        for block in ('locations', 'observations'):
            part = doc.get(block) or {}
            kind = part.get('from')
            if kind == 'http':
                out += ['httpx', 'jsonpath']
            elif kind == 'sql':
                out += ['sqlglot', 'sqlalchemy']
                sources.append((part.get('sql') or {}).get('source') or {})
    else:
        sources.append(doc.get('source') or {})
    if any(s.get('dialect') == 'postgresql' for s in sources):
        out.append('psycopg2')
    return list(dict.fromkeys(out))


def query_types_for(doc: dict[str, Any]) -> list[str]:
    return list(doc.get('query_types') or EDR_QUERY_TYPES)


def harness_capabilities(doc: dict[str, Any]) -> dict[str, Any]:
    """What the contract harness holds a spec's provider to."""
    if doc.get('template') == 'timeseries-edr':
        return {'query_types': query_types_for(doc)}
    return dict(capabilities_for(doc))


def capabilities_for(doc: dict[str, Any]) -> dict[str, bool]:
    """What the engine does for a spec: pushed down to the source, or applied locally."""
    strings = any(p.get('type') == 'string' for p in doc.get('properties') or [])
    return {
        'bbox': True,
        'datetime': bool(doc.get('time_field')),
        'properties': True,
        'sortby': True,
        'select_properties': True,
        'skip_geometry': True,
        'q': True if doc.get('template') != 'sql-features' else strings,
        'hits': True,
        'cql': False,
        'transactions': False,
    }


class WorkspaceError(ValueError):
    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.problems = problems or []


def version_key(version: str) -> tuple[int, int, int, str]:
    m = SEMVER.match(version)
    if not m:
        return (0, 0, 0, version)
    major, minor, patch, pre = m.groups()
    # A pre-release sorts before its release: '~' sorts after any pre-release text.
    return (int(major), int(minor), int(patch), pre or '~')


def spec_env(doc: dict[str, Any]) -> list[str]:
    """Environment variables the spec reads (its placeholders), sorted."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            names.update(PLACEHOLDER.findall(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return sorted(names)


def secret_names(doc: dict[str, Any]) -> set[str]:
    return set(secret_env(doc))


def check_spec_text(text: str) -> tuple[dict[str, Any] | None, list[Problem]]:
    """The raw spec and its problems: placeholders and secrets first, then the schema."""
    problems = check_text(text)
    if problems:
        return None, problems
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return None, [Problem('', 'A spec must be a YAML mapping.')]
    return doc, validate(doc)


def generated_descriptor(
    pid: str, versions: list[tuple[str, dict[str, Any]]], server_path: str
) -> dict[str, Any]:
    """The catalog descriptor for every saved version (newest first)."""
    latest_version, latest = versions[0]
    paths = [f'{server_path.rstrip("/")}/{pid}/{v}/spec.yaml' for v, _ in versions]
    secrets = secret_names(latest)
    info = template_of(latest)
    requires = requires_for(latest)
    descriptor: dict[str, Any] = {
        'descriptor_version': 1,
        'id': f'created/{pid}',
        'plugin': info['plugin'],
        'provider_type': info['provider_type'],
        'origin': 'created',
        'title': str(latest['title']),
        'summary': str(latest.get('description') or info['summary']),
        'ogc_api': list(info['ogc_api']),
        'version': latest_version,
        'requires': requires,
        'probe': {'kind': info['provider_type']},
        'egress': {'kind': 'spec'},
        'hide': HIDDEN,
        'notes': [
            'The pygeoapi server needs pygeoapi-spec-providers installed and the spec file '
            f'at the path below; the dev stack mounts the workspace at {server_path}.',
        ],
        'fields': [
            {
                'key': 'data',
                'type': 'enum',
                'title': 'Provider version',
                'help': 'The spec file the pygeoapi server reads. Newest first.',
                'required': True,
                'choices': paths,
                'suggest': paths[0],
                'step': 'connect',
            },
        ],
    }
    if info['provider_type'] == 'edr':
        descriptor['query_types'] = query_types_for(latest)
        descriptor['hide'] = HIDDEN + ['title_field', 'time_field', 'properties', 'count']
    else:
        descriptor['capabilities'] = capabilities_for(latest)
    # The spec sets these and the provider applies them; the config may override them.
    for key, title, kinds in (
        ('title_field', 'Title field', ['string']),
        ('time_field', 'Time field', None),
    ):
        if latest.get(key):
            field: dict[str, Any] = {
                'key': key,
                'type': 'field',
                'title': title,
                'help': f"Leave it to use the spec's ({latest[key]}).",
                'default': latest[key],
                'choices_from': 'probe.fields',
                'step': 'map',
            }
            if kinds:
                field['field_types'] = kinds
            descriptor['fields'].append(field)
    env = [{'name': n, 'secret': n in secrets} for n in spec_env(latest)]
    if env:
        descriptor['env'] = env
    return descriptor


class ProviderWorkspace:
    def __init__(self, settings: Workspace):
        self.root = settings.host_path
        self.server_path = settings.server_path

    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', pid):
            raise WorkspaceError(f'{pid!r} is not a provider id')
        return self.root / pid

    def versions(self, pid: str) -> list[tuple[str, dict[str, Any]]]:
        folder = self._dir(pid)
        found = []
        for spec in folder.glob('*/spec.yaml') if folder.is_dir() else []:
            doc = yaml.safe_load(spec.read_text(encoding='utf-8'))
            if isinstance(doc, dict):
                found.append((spec.parent.name, doc))
        return sorted(found, key=lambda v: version_key(v[0]), reverse=True)

    def providers(self) -> list[dict[str, Any]]:
        out = []
        for folder in sorted(self.root.glob('*/descriptor.yaml')) if self.root.is_dir() else []:
            pid = folder.parent.name
            versions = self.versions(pid)
            if versions:
                out.append(
                    {
                        'id': pid,
                        'title': versions[0][1].get('title'),
                        'template': versions[0][1].get('template'),
                        'versions': [v for v, _ in versions],
                        'descriptor': f'created/{pid}',
                    }
                )
        return out

    def detail(self, pid: str) -> dict[str, Any]:
        versions = self.versions(pid)
        if not versions:
            raise WorkspaceError(f'No provider {pid!r} in the workspace')
        folder = self._dir(pid)
        changelog = folder / 'CHANGELOG.md'
        return {
            'id': pid,
            'descriptor': f'created/{pid}',
            'template': versions[0][1].get('template', 'http-features'),
            'ejected': self.ejections(pid),
            'changelog': changelog.read_text(encoding='utf-8') if changelog.is_file() else '',
            'versions': [
                {
                    'version': v,
                    'data': f'{self.server_path.rstrip("/")}/{pid}/{v}/spec.yaml',
                    'spec': (folder / v / 'spec.yaml').read_text(encoding='utf-8'),
                    'harness': self._harness_summary(folder / v / 'harness.json'),
                }
                for v, _ in versions
            ],
        }

    def spec_text(self, pid: str, version: str) -> str:
        """A saved version's spec, as written (placeholders and all)."""
        if not SEMVER.match(version):
            raise WorkspaceError(f'{version!r} is not a version')
        path = self._dir(pid) / version / 'spec.yaml'
        if not path.is_file():
            raise WorkspaceError(f'{pid} has no version {version}')
        return path.read_text(encoding='utf-8')

    def ejections(self, pid: str) -> list[dict[str, Any]]:
        """The code-built providers made from this provider's specs (PLAN.md §7.6)."""
        path = self._dir(pid) / 'ejected.json'
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def record_ejection(self, pid: str, version: str, code_id: str) -> None:
        """Note that a version was ejected; the spec itself stays as it was."""
        folder = self._dir(pid)
        entries = self.ejections(pid)
        entries.append(
            {
                'version': version,
                'code_id': code_id,
                'descriptor': f'created/{code_id}',
                'date': datetime.now(UTC).strftime('%Y-%m-%d'),
            }
        )
        (folder / 'ejected.json').write_text(
            json.dumps(entries, indent=2) + '\n', encoding='utf-8'
        )
        self._commit(folder, f'Eject {pid} {version} to {code_id}', None)

    @staticmethod
    def _harness_summary(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return {'summary': data.get('summary'), 'ok': data.get('ok')}

    def refresh(self) -> list[str]:
        """Regenerate descriptors that an older generator wrote (they're generated files)."""
        changed = []
        for folder in sorted(self.root.glob('*/descriptor.yaml')) if self.root.is_dir() else []:
            pid = folder.parent.name
            versions = self.versions(pid)
            if not versions:
                continue
            text = self._descriptor_text(pid, versions)
            if folder.read_text(encoding='utf-8') != text:
                folder.write_text(text, encoding='utf-8')
                self._commit(folder.parent, 'Regenerate descriptor.yaml', None)
                changed.append(pid)
        return changed

    def _descriptor_text(self, pid: str, versions: list[tuple[str, dict[str, Any]]]) -> str:
        descriptor = generated_descriptor(pid, versions, self.server_path)
        return (
            '# Generated by pygeoapi Provider Builder from the saved specs. Do not edit.\n'
            + yaml.safe_dump(descriptor, sort_keys=False)
        )

    def save(
        self,
        text: str,
        *,
        version: str | None = None,
        changes: str = '',
        harness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        doc, problems = check_spec_text(text)
        if doc is None or problems:
            raise WorkspaceError('The spec has problems; fix them before saving.', problems)
        pid = str(doc['id'])
        version = version or str(doc.get('version') or '0.1.0')
        if not SEMVER.match(version):
            raise WorkspaceError(f'{version!r} is not a semantic version such as 1.0.0')
        if doc.get('version') and doc['version'] != version:
            raise WorkspaceError(
                f'The spec says version {doc["version"]} but you chose {version}.'
            )
        folder = self._dir(pid)
        target = folder / version
        if target.exists():
            raise WorkspaceError(
                f'{pid} {version} is already saved, and saved versions never change. '
                'Save this as a new version.'
            )
        target.mkdir(parents=True)
        (target / 'spec.yaml').write_text(
            text if text.endswith('\n') else text + '\n', encoding='utf-8'
        )
        if harness is not None:
            (target / 'harness.json').write_text(
                json.dumps(harness, indent=2) + '\n', encoding='utf-8'
            )
        today = datetime.now(UTC).strftime('%Y-%m-%d')
        entry = (
            f'## {version} ({today})\n\n{changes.strip() or "Saved from the template wizard."}\n'
        )
        changelog = folder / 'CHANGELOG.md'
        old = (
            changelog.read_text(encoding='utf-8') if changelog.is_file() else f'# {doc["title"]}\n'
        )
        head, _, rest = old.partition('\n')
        changelog.write_text(f'{head}\n\n{entry}{rest}'.rstrip() + '\n', encoding='utf-8')
        (folder / 'descriptor.yaml').write_text(
            self._descriptor_text(pid, self.versions(pid)), encoding='utf-8'
        )
        self._commit(folder, f'{pid} {version}', f'v{version}')
        return {
            'id': pid,
            'version': version,
            'data': f'{self.server_path.rstrip("/")}/{pid}/{version}/spec.yaml',
            'descriptor': f'created/{pid}',
        }

    @staticmethod
    def _commit(folder: Path, message: str, tag: str | None) -> None:
        git = shutil.which('git')
        if not git:
            return  # versions are still folders; Git history is a convenience

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603 - fixed git arguments
                [git, '-C', str(folder), *args], capture_output=True, text=True, check=False
            )

        if not (folder / '.git').exists():
            run('init', '-q', '-b', 'main')
        identity = []
        if not run('config', 'user.email').stdout.strip():
            identity = [
                '-c',
                'user.name=pygeoapi Provider Builder',
                '-c',
                'user.email=provider-builder@localhost',
            ]
        run('add', '-A')
        run(*identity, 'commit', '-q', '-m', message)
        if tag:
            run(*identity, 'tag', tag)
