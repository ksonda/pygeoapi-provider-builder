"""The provider_contract fixture, as a provider package's own tests would use it."""

from __future__ import annotations

import pytest

pytest_plugins = ['pytester']


def test_the_fixture_passes_and_fails(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_contract="""
        def test_good(provider_contract):
            out = provider_contract(
                {'type': 'feature', 'name': 'broken_providers.Good', 'data': 'x'})
            assert out.summary()['fail'] == 0

        def test_string_fields(provider_contract):
            provider_contract(
                {'type': 'feature', 'name': 'broken_providers.StringFields', 'data': 'x'})
    """
    )
    result = pytester.runpytest('-p', 'no:cacheprovider')
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        [
            '*The provider breaks the pygeoapi 0.24 contract:*',
            '*L1.fields (Fields are dicts with a type): These fields are not dicts with a type*',
        ]
    )
