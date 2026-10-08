"""The egress policy and the guarded transport (PROMPT.md §7)."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from pygeoapi_spec_providers import egress
from pygeoapi_spec_providers.egress import EgressError, Policy

PUBLIC = Policy.for_hosts(['api.example.org'], environ={})


@pytest.mark.parametrize(
    'address',
    [
        '169.254.169.254',
        'fe80::1',
        'fd00:ec2::254',
        '100.100.100.200',
        '::ffff:169.254.169.254',
        '0.0.0.0',
        '224.0.0.1',
        '240.0.0.1',
    ],
)
def test_link_local_metadata_and_reserved_are_always_refused(address: str) -> None:
    policy = Policy.for_hosts(['x'], environ={egress.ENV_PRIVATE_HOSTS: '*'})
    with pytest.raises(EgressError, match='link-local, metadata or reserved'):
        policy.check_address('x', address)


@pytest.mark.parametrize('address', ['10.1.2.3', '172.17.0.2', '192.168.1.1', '127.0.0.1', '::1'])
def test_private_addresses_need_the_allowlist(address: str) -> None:
    with pytest.raises(EgressError, match='non-public'):
        PUBLIC.check_address('api.example.org', address)
    allowed = Policy.for_hosts(['db'], environ={egress.ENV_PRIVATE_HOSTS: 'db, other'})
    allowed.check_address('db', address)


def test_public_addresses_pass() -> None:
    PUBLIC.check_address('api.example.org', '93.184.215.14')


def test_urls_must_be_declared_and_https() -> None:
    PUBLIC.check_url(httpx.URL('https://api.example.org/x'))
    with pytest.raises(EgressError, match='not a host this spec declares'):
        PUBLIC.check_url(httpx.URL('https://evil.example.net/x'))
    with pytest.raises(EgressError, match='plain http'):
        PUBLIC.check_url(httpx.URL('http://api.example.org/x'))
    with pytest.raises(EgressError, match='not allowed'):
        PUBLIC.check_url(httpx.URL('ftp://api.example.org/x'))


def guarded_get(policy: Policy, url: str) -> Exception:
    client = httpx.Client(transport=egress.guarded_transport(policy), trust_env=False, timeout=5)
    with pytest.raises(httpx.ConnectError) as err:
        client.get(url)
    return err.value


def test_the_transport_guard_is_live() -> None:
    """A declared host that resolves to loopback is refused inside the connection itself."""
    policy = Policy.for_hosts(['127.0.0.1'], environ={})
    err = guarded_get(policy, 'https://127.0.0.1:9/')
    assert egress.refused(err) is not None
    assert 'non-public address (127.0.0.1)' in str(err)


def test_dns_rebinding_is_caught_at_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    def rebound(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', port))]

    monkeypatch.setattr(socket, 'getaddrinfo', rebound)
    err = guarded_get(PUBLIC, 'https://api.example.org/')
    assert 'resolves to 169.254.169.254' in str(err)


def test_a_mixed_dns_answer_is_refused_as_a_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    def mixed(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.215.14', port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', port)),
        ]

    monkeypatch.setattr(socket, 'getaddrinfo', mixed)
    err = guarded_get(PUBLIC, 'https://api.example.org/')
    assert 'non-public address (10.0.0.5)' in str(err)


def test_unix_sockets_are_refused() -> None:
    with pytest.raises(Exception, match='unix sockets are not allowed'):
        egress.GuardedBackend(PUBLIC).connect_unix_socket('/var/run/docker.sock')
