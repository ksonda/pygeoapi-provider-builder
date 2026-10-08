"""tools/drift_check.py finds the keys providers read and flags descriptor drift (ADR-0010)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('drift_check', REPO / 'tools' / 'drift_check.py')
assert spec and spec.loader
drift_check = importlib.util.module_from_spec(spec)
sys.modules['drift_check'] = drift_check
spec.loader.exec_module(drift_check)

SOURCE = """
class P:
    def __init__(self, provider_def):
        self.table = provider_def['table']
        geometry = provider_def.get('geometry', {})
        self.x = geometry['x_field']
        if 'layer' in provider_def:
            pass
        opts = self.options or {}
        opts.pop('timeout', None)
        self.title = provider_def.get('title') or provider_def.get('name_field')
"""


def test_scan_follows_aliases_and_idioms() -> None:
    keys = drift_check.scan(SOURCE)
    assert {
        'table',
        'geometry',
        'geometry.x_field',
        'layer',
        'options.timeout',
        'title',
        'name_field',
    } <= set(keys)
    assert keys['table'] == {4}


def test_core_catalog_has_no_drift(capsys: pytest.CaptureFixture[str]) -> None:
    assert drift_check.check(REPO / 'catalog', []) == 0
    assert 'DRIFT' not in capsys.readouterr().out


def test_invented_and_missing_keys_are_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(REPO / 'catalog', tmp_path / 'catalog')
    path = tmp_path / 'catalog' / 'core' / 'csv.yaml'
    raw = yaml.safe_load(path.read_text())
    raw['fields'] = [f for f in raw['fields'] if f['key'] != 'geometry.y_field']
    raw['fields'].append({'key': 'delimiter', 'type': 'string'})
    path.write_text(yaml.safe_dump(raw))

    assert drift_check.check(tmp_path / 'catalog', ['CSV']) == 1
    out = capsys.readouterr().out
    missing = 'missing from descriptor: geometry.y_field  (read at pygeoapi/provider/csv_.py:62)'
    assert missing in out
    assert 'not read by the code:    delimiter' in out
