"""Code-built providers: the J5 code workspace (PLAN.md §7.5, §7.8).

Each one is a pip-installable package in its own Git repository under the
workspace (``workspace/providers/<id>/``), scaffolded by ``scaffold``. The
backend reads and writes its text files, lints and formats them with ruff over
stdin, and never imports or runs them: the contract checks run in the sandbox,
with the package's ``src`` on the path from the read-only workspace mount.

A version is a Git tag. Saving one bumps the version in ``pyproject.toml``, the
package's ``__version__`` and its descriptor, adds a CHANGELOG entry, commits
and tags ``v<version>``.
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

from .scaffold import ScaffoldError, ScaffoldRequest, ruff, write
from .settings import Workspace
from .workspace import SEMVER, WorkspaceError, version_key

#: Files the browser may open and save (text only; binary fixtures go through Git).
TEXT_SUFFIXES = frozenset(
    {'.py', '.yaml', '.yml', '.toml', '.md', '.json', '.txt', '.cfg', '.ini', '.csv', '.geojson'}
)
TEXT_NAMES = frozenset({'.gitignore', 'Dockerfile.test', 'LICENSE'})
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 500
PLACEHOLDER = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)')


def git(folder: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    binary = shutil.which('git')
    if not binary:
        raise WorkspaceError('git is not installed')
    return subprocess.run(  # noqa: S603 - fixed git arguments
        [binary, '-C', str(folder), *args], capture_output=True, text=True, check=check, timeout=60
    )


def commit(folder: Path, message: str, tag: str | None = None) -> None:
    if not (folder / '.git').exists():
        git(folder, 'init', '-q', '-b', 'main')
    identity: list[str] = []
    if not git(folder, 'config', 'user.email').stdout.strip():
        identity = [
            '-c',
            'user.name=pygeoapi Provider Builder',
            '-c',
            'user.email=provider-builder@localhost',
        ]
    git(folder, 'add', '-A')
    git(folder, *identity, 'commit', '-q', '-m', message)
    if tag:
        git(folder, *identity, 'tag', tag)


class CodeWorkspace:
    def __init__(self, settings: Workspace):
        self.root = settings.host_path
        self.server_path = settings.server_path.rstrip('/')

    # --- finding things

    def folder(self, pid: str) -> Path:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', pid):
            raise WorkspaceError(f'{pid!r} is not a provider id')
        folder = self.root / pid
        if not (folder / '.provider-builder.json').is_file():
            raise WorkspaceError(f'No code-built provider {pid!r} in the workspace')
        return folder

    def answers(self, pid: str) -> dict[str, Any]:
        data = json.loads((self.folder(pid) / '.provider-builder.json').read_text('utf-8'))
        return data if isinstance(data, dict) else {}

    def package(self, pid: str) -> str:
        return str(self.answers(pid)['package'])

    def descriptor_path(self, pid: str) -> Path:
        return self.folder(pid) / 'src' / self.package(pid) / 'descriptor.yaml'

    def descriptor(self, pid: str) -> dict[str, Any]:
        doc = yaml.safe_load(self.descriptor_path(pid).read_text('utf-8'))
        return doc if isinstance(doc, dict) else {}

    def python_path(self, pid: str) -> str:
        """The package's src folder, as the sandbox (and the dev stack) see it."""
        return f'{self.server_path}/{pid}/src'

    def providers(self) -> list[dict[str, Any]]:
        out = []
        for marker in sorted(self.root.glob('*/.provider-builder.json')):
            pid = marker.parent.name
            try:
                answers = self.answers(pid)
            except (WorkspaceError, ValueError):
                continue
            out.append(
                {
                    'id': pid,
                    'title': answers.get('title'),
                    'provider_type': answers.get('provider_type'),
                    'versions': self.versions(pid),
                    'descriptor': f'created/{pid}',
                }
            )
        return out

    def versions(self, pid: str) -> list[str]:
        tags = git(self.folder(pid), 'tag', '--list', 'v*').stdout.split()
        versions = [t[1:] for t in tags if SEMVER.match(t[1:])]
        return sorted(versions, key=version_key, reverse=True)

    def files(self, pid: str) -> list[str]:
        folder = self.folder(pid)
        out = []
        for path in sorted(folder.rglob('*')):
            rel = path.relative_to(folder)
            if path.is_file() and '.git' not in rel.parts and self._editable(rel):
                if any(p in ('__pycache__', '.pytest_cache', '.ruff_cache') for p in rel.parts):
                    continue
                out.append(rel.as_posix())
            if len(out) >= MAX_FILES:
                break
        return out

    def detail(self, pid: str) -> dict[str, Any]:
        folder = self.folder(pid)
        answers = self.answers(pid)
        status = git(folder, 'status', '--porcelain').stdout.splitlines()
        return {
            'id': pid,
            'title': answers.get('title'),
            'provider_type': answers.get('provider_type'),
            'package': answers.get('package'),
            'class_name': answers.get('class_name'),
            'plugin': f'{answers.get("package")}.provider.{answers.get("class_name")}',
            'descriptor': f'created/{pid}',
            'python_path': self.python_path(pid),
            'files': self.files(pid),
            'changed': sorted({line[3:].strip() for line in status if len(line) > 3}),
            'versions': self.versions(pid),
            'remote': git(folder, 'remote', 'get-url', 'origin').stdout.strip() or None,
        }

    # --- files

    @staticmethod
    def _editable(rel: Path) -> bool:
        return rel.suffix.lower() in TEXT_SUFFIXES or rel.name in TEXT_NAMES

    def path(self, pid: str, rel: str) -> Path:
        folder = self.folder(pid).resolve()
        candidate = Path(rel)
        if candidate.is_absolute() or '..' in candidate.parts or not rel:
            raise WorkspaceError(f'{rel!r} is not a path inside the package')
        if '.git' in candidate.parts:
            raise WorkspaceError('Files under .git are not editable here')
        resolved = (folder / candidate).resolve()
        if not resolved.is_relative_to(folder):  # a symlink that leaves the package
            raise WorkspaceError(f'{rel!r} leaves the package folder')
        if not self._editable(candidate):
            raise WorkspaceError(f'{rel!r} is not a text file the workspace edits')
        return resolved

    def read(self, pid: str, rel: str) -> str:
        path = self.path(pid, rel)
        if not path.is_file():
            raise WorkspaceError(f'No file {rel!r}')
        return path.read_text('utf-8')

    def write_file(self, pid: str, rel: str, content: str) -> dict[str, Any]:
        if len(content.encode('utf-8')) > MAX_FILE_BYTES:
            raise WorkspaceError(f'{rel} is larger than {MAX_FILE_BYTES:,} bytes')
        path = self.path(pid, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return {'path': rel, 'bytes': len(content.encode('utf-8'))}

    # --- ruff

    def lint(self, pid: str, rel: str, content: str) -> list[dict[str, Any]]:
        config = self.folder(pid) / 'pyproject.toml'
        out = ruff(['check', '--output-format', 'json', '--exit-zero'], content, rel, config)
        diagnostics = []
        for d in json.loads(out or '[]'):
            diagnostics.append(
                {
                    'code': d.get('code'),
                    'message': d.get('message'),
                    'line': (d.get('location') or {}).get('row'),
                    'column': (d.get('location') or {}).get('column'),
                    'end_line': (d.get('end_location') or {}).get('row'),
                    'fixable': bool(d.get('fix')),
                }
            )
        return diagnostics

    def format(self, pid: str, rel: str, content: str) -> str:
        return ruff(['format'], content, rel, self.folder(pid) / 'pyproject.toml')

    # --- creating and versions

    def create(self, req: ScaffoldRequest) -> dict[str, Any]:
        if (self.root / req.id).exists():
            raise WorkspaceError(f'{req.id} already exists in the workspace')
        try:
            folder = write(req, self.root)
        except ScaffoldError as err:
            raise WorkspaceError(str(err)) from err
        commit(folder, f'Scaffold {req.id} (pygeoapi Provider Builder)')
        return self.detail(req.id)

    def example_resource(self, pid: str) -> str:
        """The package's example config resource: what the contract checks run against."""
        return self.read(pid, 'examples/pygeoapi-config.snippet.yml')

    def env_names(self, pid: str) -> list[str]:
        return sorted(set(PLACEHOLDER.findall(self.example_resource(pid))))

    def save_version(
        self, pid: str, version: str, changes: str, harness: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not SEMVER.match(version):
            raise WorkspaceError(f'{version!r} is not a semantic version such as 1.0.0')
        if version in self.versions(pid):
            raise WorkspaceError(f'{pid} {version} is already saved; save a new version.')
        if not harness or not harness.get('ok'):
            raise WorkspaceError(
                'Run the contract checks, and pass them, before saving a version.'
            )
        folder = self.folder(pid)
        pyproject = folder / 'pyproject.toml'
        text = pyproject.read_text('utf-8')
        text, n = re.subn(r'(?m)^version = "[^"]*"', f'version = "{version}"', text, count=1)
        if not n:
            raise WorkspaceError('pyproject.toml has no version = "…" line to update')
        pyproject.write_text(text, encoding='utf-8')
        init = folder / 'src' / self.package(pid) / '__init__.py'
        if init.is_file():
            init.write_text(
                re.sub(
                    r'(?m)^__version__ = .*$',
                    f"__version__ = '{version}'",
                    init.read_text('utf-8'),
                ),
                encoding='utf-8',
            )
        descriptor = self.descriptor_path(pid)
        descriptor.write_text(
            re.sub(r'(?m)^version: .*$', f'version: {version}', descriptor.read_text('utf-8')),
            encoding='utf-8',
        )
        changelog = folder / 'CHANGELOG.md'
        today = datetime.now(UTC).strftime('%Y-%m-%d')
        note = changes.strip() or 'A new version.'
        old = changelog.read_text('utf-8') if changelog.is_file() else f'# {pid}\n'
        if '\n## Unreleased\n' in old:  # the scaffold's entry becomes this version's
            text = old.replace('\n## Unreleased\n', f'\n## {version} ({today})\n\n- {note}\n', 1)
        else:
            head, _, rest = old.partition('\n')
            text = f'{head}\n\n## {version} ({today})\n\n- {note}\n{rest}'
        changelog.write_text(text.rstrip() + '\n', encoding='utf-8')
        results = folder / 'harness.json'
        results.write_text(json.dumps(harness, indent=2) + '\n', encoding='utf-8')
        commit(folder, f'{pid} {version}', f'v{version}')
        return {'id': pid, 'version': version, 'descriptor': f'created/{pid}'}

    # --- sync

    def pull(self, pid: str) -> dict[str, Any]:
        folder = self.folder(pid)
        if not git(folder, 'remote', 'get-url', 'origin').stdout.strip():
            raise WorkspaceError('This provider has no Git remote to pull from.')
        proc = git(folder, 'pull', '--ff-only', '--tags', 'origin')
        if proc.returncode != 0:
            raise WorkspaceError(f'git pull failed: {proc.stderr.strip()[:500]}')
        return self.detail(pid)

    def import_git(self, url: str, pid: str) -> dict[str, Any]:
        """Clone a code-built provider (one made by the Provider Builder) into the workspace."""
        if not re.match(r'^(https://|ssh://|git@|file://)', url):
            raise WorkspaceError('Give an https://, ssh:// or git@ URL.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', pid):
            raise WorkspaceError(f'{pid!r} is not a provider id')
        target = self.root / pid
        if target.exists():
            raise WorkspaceError(f'{pid} already exists in the workspace')
        self.root.mkdir(parents=True, exist_ok=True)
        binary = shutil.which('git') or 'git'
        proc = subprocess.run(  # noqa: S603 - fixed arguments; the URL is a single argument
            [binary, 'clone', '--quiet', '--', url, str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise WorkspaceError(f'git clone failed: {proc.stderr.strip()[:500]}')
        if not (target / '.provider-builder.json').is_file():
            shutil.rmtree(target, ignore_errors=True)
            raise WorkspaceError(
                "That repository isn't a Provider Builder package (no .provider-builder.json)."
            )
        return self.detail(pid)

    def import_folder(self, source: Path, pid: str) -> dict[str, Any]:
        """Copy a package folder (with a .provider-builder.json) into the workspace."""
        source = source.resolve()
        if not (source / '.provider-builder.json').is_file():
            raise WorkspaceError(f'{source} has no .provider-builder.json')
        target = self.root / pid
        if target.exists():
            raise WorkspaceError(f'{pid} already exists in the workspace')
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns('.venv', '__pycache__', '*.pyc', 'dist', 'build'),
            symlinks=False,
        )
        if not (target / '.git').exists():
            commit(target, f'Import {pid} from a folder')
        return self.detail(pid)
