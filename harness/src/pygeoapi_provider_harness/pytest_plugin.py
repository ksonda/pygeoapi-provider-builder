"""pytest plugin: the provider contract as a fixture (PLAN.md §7.7).

Installed with the package, so a provider's own tests can run the same checks
as the Provider Builder::

    def test_contract(provider_contract):
        provider_contract(
            {'type': 'feature', 'name': 'my_pkg.provider.MyProvider',
             'data': 'tests/fixtures/x.json'},
            capabilities={'bbox': True, 'properties': True},
        )

Failures fail the test with every failing check, its explanation and a hint.
``levels`` defaults to L1 and L2; L3 starts gunicorn and needs it installed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from .checks import Checklist, run_contract

ContractFn = Callable[..., Checklist]


def describe_failures(checklist: Checklist) -> str:
    lines = ['The provider breaks the pygeoapi 0.24 contract:']
    for check in checklist.results:
        if check.status == 'fail':
            hint = f' Hint: {check.hint}' if check.hint else ''
            lines.append(f'  {check.id} ({check.title}): {check.message}{hint}')
    return '\n'.join(lines)


@pytest.fixture
def provider_contract() -> ContractFn:
    def check(
        provider: Mapping[str, Any],
        *,
        collection: str = 'contract',
        capabilities: Mapping[str, bool] | None = None,
        levels: tuple[str, ...] = ('L1', 'L2'),
        resource: Mapping[str, Any] | None = None,
    ) -> Checklist:
        body = {
            'type': 'collection',
            'title': 'Contract check',
            'description': 'A provider under test',
            'keywords': ['test'],
            'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
            **(resource or {}),
            'providers': [dict(provider)],
        }
        out = run_contract(collection, body, 0, capabilities, levels)
        if not out.ok():
            pytest.fail(describe_failures(out), pytrace=False)
        return out

    return check
