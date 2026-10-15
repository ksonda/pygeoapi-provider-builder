"""Ejected code is safe on an open network (ADR-0021): the engine's egress rules, ported.

The helper modules ship as they are in every ejected package, so they're loaded
here from ``scaffold_templates/eject/``. The cases mirror runtime/tests/test_egress.py.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

EJECT = Path(__file__).resolve().parents[2] / 'scaffold_templates' / 'eject'


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f'ejected_{name}', EJECT / f'{name}.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while it loads
    spec.loader.exec_module(module)
    return module


http = load('http_client')
db = load('database')


def policy(hosts: list[str], private: str = '') -> Any:
    return http.Policy(hosts, environ={http.ENV_PRIVATE_HOSTS: private} if private else {})


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
    with pytest.raises(http.EgressRefused, match='link-local, metadata or reserved'):
        policy(['x'], '*').check_address('x', address)
    with pytest.raises(db.DatabaseError, match='resolves to') as err:
        db.check_address('x', address)
    assert err.value.kind == 'egress'


@pytest.mark.parametrize('address', ['10.1.2.3', '172.17.0.2', '192.168.1.1', '127.0.0.1', '::1'])
def test_private_addresses_need_the_allowlist(address: str) -> None:
    with pytest.raises(http.EgressRefused, match='non-public'):
        policy(['api.example.org']).check_address('api.example.org', address)
    policy(['db'], 'db, other').check_address('db', address)
    db.check_address('db', address)  # databases usually live on private networks


def test_urls_must_be_declared_and_https() -> None:
    public = policy(['api.example.org'])
    public.check_url('https://api.example.org/x')
    for url, message in (
        ('https://evil.example.net/x', 'not a host this provider declares'),
        ('http://api.example.org/x', 'plain http'),
        ('ftp://api.example.org/x', 'not allowed'),
    ):
        with pytest.raises(http.SourceError, match=message) as err:
            public.check_url(url)
        assert err.value.kind == 'egress'
    policy(['mockapi'], 'mockapi').check_url('http://mockapi:8000/x')


def answer(*addresses: str) -> Any:
    def getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port)) for a in addresses]

    return getaddrinfo


def refused(client: Any, url: str) -> Any:
    with pytest.raises(http.SourceError) as err:
        client.get(url)
    assert err.value.kind == 'egress'
    assert http.provider_error(err.value).user_msg == (
        "The data source isn't allowed by this server's egress rules."
    )
    return err.value


def test_the_connection_guard_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared host that resolves to loopback is refused inside the connection itself."""
    monkeypatch.delenv(http.ENV_PRIVATE_HOSTS, raising=False)
    client = http.Client(['127.0.0.1'], retries=0, timeout_s=2)
    assert 'non-public address (127.0.0.1)' in str(refused(client, 'https://127.0.0.1:9/'))


def test_dns_rebinding_is_caught_at_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(http.ENV_PRIVATE_HOSTS, raising=False)
    monkeypatch.setattr(socket, 'getaddrinfo', answer('169.254.169.254'))
    client = http.Client(['api.example.org'], retries=0, timeout_s=2)
    assert 'resolves to 169.254.169.254' in str(refused(client, 'https://api.example.org/'))


def test_a_mixed_dns_answer_is_refused_as_a_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(http.ENV_PRIVATE_HOSTS, raising=False)
    monkeypatch.setattr(socket, 'getaddrinfo', answer('93.184.215.14', '10.0.0.5'))
    client = http.Client(['api.example.org'], retries=0, timeout_s=2)
    assert 'non-public address (10.0.0.5)' in str(refused(client, 'https://api.example.org/'))


def test_an_allowed_private_host_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the allowlist, the guard passes and the request reaches the network."""
    monkeypatch.setenv(http.ENV_PRIVATE_HOSTS, '127.0.0.1')
    client = http.Client(['127.0.0.1'], retries=0, timeout_s=2)
    with pytest.raises(http.SourceError) as err:
        client.get('http://127.0.0.1:9/')  # nothing listens on the discard port
    assert err.value.kind == 'connect'


def test_a_database_host_is_vetted_for_every_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, 'getaddrinfo', answer('169.254.169.254'))
    url, secrets = db.postgresql_url(
        {'host': 'db.example.org', 'dbname': 'gis', 'user': 'reader', 'password': 'pw1234'}
    )
    database = db.Database(url, secrets=secrets)
    with pytest.raises(db.DatabaseError) as err, database.connect():
        pass
    assert err.value.kind == 'egress' and 'pw1234' not in str(err.value)
    assert db.provider_error(err.value).user_msg == "The database host isn't allowed."
    monkeypatch.setattr(socket, 'getaddrinfo', answer('10.0.0.7'))
    assert db.vetted_address('db.example.org', 5432) == '10.0.0.7'
