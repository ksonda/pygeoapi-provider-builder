"""J6: package, deliver and version created providers (PLAN.md §7.8, ADR-0022).

A **package** is a folder that installs with pip:

- *template-built*: one saved spec version wrapped as ``<pid>-spec``. The spec
  is a package resource, so a config says ``data: pkg:<module>/spec.yaml``
  (ADR-0005) and the version is the installed package's. The package depends on
  pygeoapi-spec-providers, which isn't on PyPI, so the zip carries its source
  under ``vendor/``.
- *code-built*: the package itself, as its version tag holds it
  (``git archive``).

**Deliveries:**

- a zip, the same bytes each time: sorted names and fixed timestamps
- a push of the provider's Git repository (a branch and its tags) to a remote,
  the route to production through review. For GitHub, the result links to the
  compare page that opens a pull request.
- a test image ``FROM geopython/pygeoapi:0.24.0`` with the package installed.
  ``docker build`` runs the build backends in its own containers, with no
  secrets and no build arguments. The image is then smoke-tested with no
  network: the provider module imports and a packaged spec loads.

**The gate:** only a version whose contract checks passed is delivered. That's
the harness result saved with the version (code-built versions can't be saved
otherwise), or a result recorded later by running the checks on that version.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pygeoapi
import yaml
from pygeoapi_spec_providers import __version__ as RUNTIME_VERSION  # noqa: N812

from .codespace import git
from .images import BASE_IMAGE
from .workspace import PLACEHOLDER, WorkspaceError, generated_descriptor, spec_env

REPO = Path(__file__).resolve().parents[2]
RUNTIME = REPO / 'runtime'
#: A fixed timestamp for zip members, so the same package is the same bytes.
ZIP_TIME = (2020, 1, 1, 0, 0, 0)
GIT_URL = re.compile(r'^(https://|ssh://|git@|file://)')
GITHUB = re.compile(
    r'^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([^/]+)/(.+?)(?:\.git)?/?$'
)
BRANCH = re.compile(r'^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,100}(?<!/)(?<!\.lock)$')
IMAGE_TIMEOUT_S = 900


class DeliveryError(WorkspaceError):
    pass


def module_name(pid: str) -> str:
    """The Python package that carries a template-built provider's spec."""
    name = re.sub(r'[^a-z0-9]+', '_', pid.lower()).strip('_')
    name = name if name and not name[0].isdigit() else f'p_{name}'
    return f'{name}_spec'


def dist_name(pid: str) -> str:
    return module_name(pid).replace('_', '-')


# --- template-built packages -------------------------------------------------------------


def spec_package_files(
    pid: str,
    version: str,
    spec_text: str,
    *,
    changelog: str,
    harness: Mapping[str, Any] | None,
    server_path: str,
) -> dict[str, str]:
    """Every file of a template-built provider's package, by path (vendor/ excluded)."""
    doc = yaml.safe_load(spec_text)
    if not isinstance(doc, dict):
        raise DeliveryError('The saved spec is not a YAML mapping.')
    module, dist = module_name(pid), dist_name(pid)
    ref = f'pkg:{module}/spec.yaml'
    descriptor = generated_descriptor(pid, [(version, doc)], server_path)
    descriptor['version'] = version
    descriptor['requires'] = list(dict.fromkeys([*descriptor['requires'], module]))
    descriptor['notes'] = [
        f'Install {dist} {version} on the pygeoapi server (pip install). '
        f'Its spec is a package resource: data: {ref}.'
    ]
    for field in descriptor['fields']:
        if field['key'] == 'data':
            field.update(
                {
                    'title': 'Provider spec',
                    'help': f'The spec inside the installed {dist} package.',
                    'choices': [ref],
                    'suggest': ref,
                }
            )
    env = spec_env(doc)
    resource = {
        pid: {
            'type': 'collection',
            'title': str(doc.get('title') or pid),
            'description': str(doc.get('description') or doc.get('title') or pid),
            'keywords': [pid],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
            'providers': [
                {'type': descriptor['provider_type'], 'name': descriptor['plugin'], 'data': ref}
            ],
        }
    }
    title = str(doc.get('title') or pid)
    variables = ', '.join(f'`{n}`' for n in env)
    readme = f"""# {' '.join(title.split())}

A pygeoapi {pygeoapi.__version__} provider from pygeoapi Provider Builder: version
{version} of the `{doc.get('template', 'http-features')}` spec `{pid}`, as a package. The
spec engine, pygeoapi-spec-providers, reads it from the installed package:

```yaml
providers:
  - type: {descriptor['provider_type']}
    name: {descriptor['plugin']}
    data: {ref}
```

`examples/pygeoapi-config.snippet.yml` has a whole resource to add under `resources:`.
{f'Set {variables} on the pygeoapi server: the spec reads them as `${{…}}`.' if env else ''}

## Install

pygeoapi-spec-providers isn't on PyPI yet, so its source is in `vendor/`:

```bash
/venv/bin/python3 -m pip install vendor/pygeoapi-spec-providers .
```

`Dockerfile.test` builds a test image that way, `FROM geopython/pygeoapi:{pygeoapi.__version__}`.
`harness.json` is the contract checklist this version passed.
"""
    files = {
        'pyproject.toml': f"""[project]
name = {json.dumps(dist)}
version = {json.dumps(version)}
description = {json.dumps(f'{title}: a pygeoapi provider spec (pygeoapi Provider Builder)')}
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "pygeoapi=={pygeoapi.__version__}",
    "pygeoapi-spec-providers>={RUNTIME_VERSION}",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{module}"]
""",
        'README.md': readme,
        'CHANGELOG.md': changelog or f'# {title}\n\n## {version}\n',
        f'src/{module}/__init__.py': (
            f'"""The {pid} provider spec, version {version}, as a package resource."""\n\n'
            f'__version__ = {version!r}\n'
        ),
        f'src/{module}/spec.yaml': spec_text,
        f'src/{module}/descriptor.yaml': (
            '# The Provider Builder catalog entry for this packaged spec.\n'
            + yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True)
        ),
        'examples/pygeoapi-config.snippet.yml': (
            '# Add under resources: in your pygeoapi config.\n'
            + yaml.safe_dump(resource, sort_keys=False, allow_unicode=True)
        ),
        'Dockerfile.test': f"""# A pygeoapi {pygeoapi.__version__} image with this provider
# installed, the way the official image expects extra packages.
FROM {BASE_IMAGE}
COPY . /opt/{dist}
RUN /venv/bin/python3 -m pip install --no-cache-dir \\
    /opt/{dist}/vendor/pygeoapi-spec-providers /opt/{dist}
""",
        '.gitignore': '__pycache__/\n*.egg-info/\ndist/\nbuild/\n',
    }
    if harness is not None:
        files['harness.json'] = json.dumps(harness, indent=2) + '\n'
    return files


def runtime_files() -> dict[str, bytes]:
    """pygeoapi-spec-providers' source, for ``vendor/pygeoapi-spec-providers``."""
    out: dict[str, bytes] = {}
    for name in ('pyproject.toml', 'README.md'):
        path = RUNTIME / name
        if path.is_file():
            out[name] = path.read_bytes()
    for path in sorted((RUNTIME / 'src').rglob('*')):
        rel = path.relative_to(RUNTIME)
        if path.is_file() and '__pycache__' not in rel.parts and path.suffix != '.pyc':
            out[rel.as_posix()] = path.read_bytes()
    return out


def write_folder(files: Mapping[str, str | bytes], folder: Path) -> Path:
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')
    return folder


def spec_package_tree(files: Mapping[str, str]) -> dict[str, str | bytes]:
    """The package's files plus the vendored runtime, as the zip and the image hold them."""
    tree: dict[str, str | bytes] = dict(files)
    for name, content in runtime_files().items():
        tree[f'vendor/pygeoapi-spec-providers/{name}'] = content
    return tree


# --- zips ---------------------------------------------------------------------------------


def zip_bytes(files: Mapping[str, str | bytes], prefix: str) -> bytes:
    """A zip of ``files`` under ``prefix/``: sorted names and fixed timestamps."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            content = files[name]
            info = zipfile.ZipInfo(f'{prefix}/{name}', date_time=ZIP_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content if isinstance(content, bytes) else content.encode())
    return buffer.getvalue()


def git_files(folder: Path, tag: str) -> dict[str, bytes]:
    """The files a tag holds (``git archive``), without the working tree's changes."""
    proc = subprocess.run(  # noqa: S603 - fixed git arguments
        [shutil.which('git') or 'git', '-C', str(folder), 'archive', '--format=tar', tag],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise DeliveryError(f'No version {tag}: {proc.stderr.decode(errors="replace")[:300]}')
    import tarfile

    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        for member in archive.getmembers():
            if member.isfile():
                extracted = archive.extractfile(member)
                if extracted is not None:
                    out[member.name] = extracted.read()
    return out


# --- Git delivery -------------------------------------------------------------------------


def safe_url(url: str) -> str:
    """The URL without a password, for messages."""
    parts = urlsplit(url)
    if parts.password:
        host = parts.hostname or ''
        netloc = f'{parts.username}:***@{host}' + (f':{parts.port}' if parts.port else '')
        return parts._replace(netloc=netloc).geturl()
    return url


def compare_url(remote: str, branch: str) -> str | None:
    """GitHub's page that opens a pull request for ``branch``, when the remote is GitHub's."""
    m = GITHUB.match(remote)
    return f'https://github.com/{m.group(1)}/{m.group(2)}/compare/{branch}?expand=1' if m else None


def push(folder: Path, remote: str, branch: str) -> dict[str, Any]:
    """Push the repository's current commit as ``branch``, with its version tags."""
    if not GIT_URL.match(remote):
        raise DeliveryError('Give an https://, ssh://, git@ or file:// URL.')
    if urlsplit(remote).password:
        raise DeliveryError(
            "Don't put a password or token in the URL: use Git's credential helper or SSH."
        )
    if not BRANCH.match(branch):
        raise DeliveryError(f'{branch!r} is not a branch name.')
    head = git(folder, 'rev-parse', 'HEAD')
    if head.returncode != 0:
        raise DeliveryError('This provider has no commits to push.')
    for args in (
        ['push', '--', remote, f'HEAD:refs/heads/{branch}'],
        ['push', '--tags', '--', remote],
    ):
        proc = git(folder, *args)
        if proc.returncode != 0:
            message = proc.stderr.strip().replace(remote, safe_url(remote))[:500]
            raise DeliveryError(f'git push failed: {message}')
    tags = git(folder, 'tag', '--points-at', 'HEAD').stdout.split()
    return {
        'remote': safe_url(remote),
        'branch': branch,
        'commit': head.stdout.strip(),
        'tags': tags,
        'compare_url': compare_url(remote, branch),
    }


# --- test images --------------------------------------------------------------------------


def image_tag(kind: str, pid: str, version: str) -> str:
    safe = re.sub(r'[^a-z0-9._-]+', '-', version.lower())
    return f'provider-builder/{kind}-{pid}:{safe}'


def docker() -> str:
    found = shutil.which('docker')
    if not found:
        raise DeliveryError('Building a test image needs Docker, which is not available.')
    return found


def build_image(
    tree: Mapping[str, str | bytes],
    tag: str,
    labels: Mapping[str, str],
    *,
    smoke: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """``docker build`` the tree (its Dockerfile.test), then run ``smoke`` in it, offline."""
    binary = docker()
    with tempfile.TemporaryDirectory(prefix='pb-image-') as tmp:
        context = write_folder(tree, Path(tmp))
        cmd = [binary, 'build', '--file', str(context / 'Dockerfile.test'), '--tag', tag]
        for key, value in labels.items():
            cmd += ['--label', f'{key}={value}']
        try:
            built = subprocess.run(  # noqa: S603 - docker with fixed arguments; no secrets
                [*cmd, str(context)],
                capture_output=True,
                text=True,
                timeout=IMAGE_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            raise DeliveryError(f'The image build took longer than {IMAGE_TIMEOUT_S} s.') from err
    log = (built.stdout + built.stderr)[-4000:]
    if built.returncode != 0:
        return {'ok': False, 'tag': tag, 'stage': 'build', 'log': log}
    run = [binary, 'run', '--rm', '--network', 'none', '--read-only',
           '--tmpfs', '/tmp',  # noqa: S108 - the container's own scratch space
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '10001:0',
           '--entrypoint', '/venv/bin/python3']  # fmt: skip
    for key, value in (env or {}).items():
        run += ['-e', f'{key}={value}']
    checked = subprocess.run(  # noqa: S603 - docker with fixed arguments
        [*run, tag, '-c', smoke], capture_output=True, text=True, timeout=120, check=False
    )
    inspect = subprocess.run(  # noqa: S603 - docker with fixed arguments
        [binary, 'image', 'inspect', '--format', '{{.Id}} {{.Size}}', tag],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    image_id, _, size = inspect.stdout.strip().partition(' ')
    return {
        'ok': checked.returncode == 0,
        'tag': tag,
        'id': image_id,
        'size': int(size) if size.isdigit() else None,
        'stage': 'smoke' if checked.returncode else 'done',
        'smoke': (checked.stdout + checked.stderr)[-2000:],
        'log': log,
    }


def run_command(tag: str, resource_id: str, env_names: list[str]) -> str:
    """How to try the image: a config that holds the example resource, mounted at /config."""
    env = ''.join(f' -e {name}=…' for name in env_names)
    return (
        f'docker run --rm -p 5000:80 -v "$PWD/config:/config"'
        f' -e PYGEOAPI_CONFIG=/config/pygeoapi.config.yml{env} {tag}'
        f'  # then open http://localhost:5000/collections/{resource_id}'
    )


def env_in(text: str) -> list[str]:
    return sorted(set(PLACEHOLDER.findall(text)))
