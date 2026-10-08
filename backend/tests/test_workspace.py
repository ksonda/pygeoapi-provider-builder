"""The workspace of created providers: versions, the generated descriptor, Git history."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from provider_builder.catalog import Catalog
from provider_builder.settings import Workspace
from provider_builder.workspace import ProviderWorkspace, WorkspaceError, version_key

REPO = Path(__file__).resolve().parents[2]

SPEC = """spec_version: 1
template: http-features
id: wells
title: Wells
version: {version}
source:
  url: https://api.example.org/wells
  auth:
    type: header
    name: X-API-Key
    value: ${{WELLS_KEY}}
records: $.items
item_id:
  from: id
geometry:
  type: point
  x: lon
  y: lat
properties:
  - name: id
    type: string
  - name: name
    type: string
  - name: seen
    type: datetime
title_field: name
time_field: seen
"""


@pytest.fixture
def ws(tmp_path: Path) -> ProviderWorkspace:
    return ProviderWorkspace(Workspace(host_path=tmp_path / 'providers', server_path='/specs'))


def test_versions_sort_by_semver() -> None:
    versions = ['1.10.0', '1.9.0', '2.0.0-rc.1', '2.0.0', '0.1.0']
    assert sorted(versions, key=version_key, reverse=True) == [
        '2.0.0',
        '2.0.0-rc.1',
        '1.10.0',
        '1.9.0',
        '0.1.0',
    ]


def test_save_writes_a_version_a_changelog_and_a_descriptor(ws: ProviderWorkspace) -> None:
    saved = ws.save(SPEC.format(version='1.0.0'), changes='First.')
    assert saved == {
        'id': 'wells',
        'version': '1.0.0',
        'data': '/specs/wells/1.0.0/spec.yaml',
        'descriptor': 'created/wells',
    }
    ws.save(SPEC.format(version='1.1.0'), changes='Second.')
    folder = ws.root / 'wells'
    changelog = (folder / 'CHANGELOG.md').read_text()
    assert changelog.index('## 1.1.0') < changelog.index('## 1.0.0')
    descriptor = yaml.safe_load((folder / 'descriptor.yaml').read_text())
    data = descriptor['fields'][0]
    assert data['choices'] == ['/specs/wells/1.1.0/spec.yaml', '/specs/wells/1.0.0/spec.yaml']
    assert data['suggest'] == '/specs/wells/1.1.0/spec.yaml' and 'default' not in data
    defaults = {f['key']: f['default'] for f in descriptor['fields'][1:]}
    assert defaults == {'title_field': 'name', 'time_field': 'seen'}
    assert descriptor['env'] == [{'name': 'WELLS_KEY', 'secret': True}]
    assert descriptor['capabilities']['datetime'] is True
    catalog = Catalog([REPO / 'catalog'], ws.root)
    assert catalog.errors == []
    assert catalog.get('created/wells').raw['origin'] == 'created'


def test_saved_versions_never_change(ws: ProviderWorkspace) -> None:
    ws.save(SPEC.format(version='1.0.0'))
    with pytest.raises(WorkspaceError, match='saved versions never change'):
        ws.save(SPEC.format(version='1.0.0').replace('title: Wells', 'title: Other'))
    with pytest.raises(WorkspaceError, match='not a semantic version'):
        ws.save(SPEC.format(version='1.0.0'), version='1.0')
    with pytest.raises(WorkspaceError, match='says version 1.0.0 but you chose 2.0.0'):
        ws.save(SPEC.format(version='1.0.0'), version='2.0.0')


def test_problems_block_a_save(ws: ProviderWorkspace) -> None:
    with pytest.raises(WorkspaceError) as err:
        ws.save(SPEC.format(version='1.0.0').replace('${WELLS_KEY}', 'literal-secret'))
    assert err.value.problems[0].message == 'Secrets must come from an environment variable.'
    assert not (ws.root / 'wells').exists()


@pytest.mark.skipif(not shutil.which('git'), reason='needs git')
def test_each_save_is_a_tagged_commit_and_refresh_keeps_descriptors_current(
    ws: ProviderWorkspace,
) -> None:
    ws.save(SPEC.format(version='1.0.0'))
    folder = ws.root / 'wells'

    def git(*args: str) -> str:
        return subprocess.run(
            ['git', '-C', str(folder), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    assert git('tag') == 'v1.0.0'
    assert ws.refresh() == []
    descriptor = folder / 'descriptor.yaml'
    # As an older generator would have written and committed it:
    descriptor.write_text(descriptor.read_text().replace('suggest:', 'default:'))
    git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qam', 'old generator')
    assert ws.refresh() == ['wells']
    assert 'suggest:' in descriptor.read_text()
    assert git('log', '--format=%s') == 'Regenerate descriptor.yaml\nold generator\nwells 1.0.0'
