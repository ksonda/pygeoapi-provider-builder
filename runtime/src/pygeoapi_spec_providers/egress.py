"""Where a spec's HTTP source may connect (PROMPT.md §7, PLAN.md §9).

- Only the hosts the spec declares (its source and item URLs).
- Never link-local or cloud-metadata addresses.
- Other non-public addresses (private ranges, loopback) only for hosts named
  in ``PYGEOAPI_SPEC_PRIVATE_HOSTS`` (comma-separated, ``*`` for every declared
  host). Plain ``http`` is allowed only for those hosts too.

The check runs inside the HTTP connection itself: ``GuardedBackend`` resolves
the name, vets every address and connects to a vetted one, so a DNS answer
can't change between the check and the connection. TLS still verifies the
certificate against the hostname.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx

ENV_PRIVATE_HOSTS = 'PYGEOAPI_SPEC_PRIVATE_HOSTS'
REFUSED = 'egress refused: '
#: Cloud metadata endpoints outside the link-local ranges.
METADATA = frozenset(ipaddress.ip_address(a) for a in ('fd00:ec2::254', '100.100.100.200'))

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressError(Exception):
    """A request the policy doesn't allow."""


@dataclass(frozen=True)
class Policy:
    hosts: frozenset[str]
    private: frozenset[str]

    @classmethod
    def for_hosts(cls, hosts: Iterable[str], environ: Mapping[str, str] | None = None) -> Policy:
        env = os.environ if environ is None else environ
        private = {
            h.strip().lower() for h in env.get(ENV_PRIVATE_HOSTS, '').split(',') if h.strip()
        }
        return cls(frozenset(h.lower() for h in hosts), frozenset(private))

    def may_be_private(self, host: str) -> bool:
        return '*' in self.private or host.lower() in self.private

    def check_url(self, url: httpx.URL) -> None:
        host = url.host.lower()
        if url.scheme not in ('http', 'https'):
            raise EgressError(f'{url.scheme} URLs are not allowed')
        if host not in self.hosts:
            raise EgressError(f'{host} is not a host this spec declares')
        if url.scheme == 'http' and not self.may_be_private(host):
            raise EgressError(f'plain http to {host} is not allowed; use https')

    def check_address(self, host: str, address: str) -> None:
        ip: IPAddress = ipaddress.ip_address(address.split('%', 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.is_link_local or ip in METADATA or ip.is_multicast or ip.is_unspecified:
            raise EgressError(
                f'{host} resolves to {ip}, a link-local, metadata or reserved address'
            )
        if ip.is_reserved and not ip.is_loopback:  # ::1 sits in the reserved ::/8
            raise EgressError(
                f'{host} resolves to {ip}, a link-local, metadata or reserved address'
            )
        if not ip.is_global and not self.may_be_private(host):
            raise EgressError(
                f'{host} resolves to a non-public address ({ip}); '
                f'allow it with {ENV_PRIVATE_HOSTS}={host}'
            )


def resolve(host: str, port: int) -> list[str]:
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise httpcore.ConnectError(f"Couldn't resolve {host}: {err.strerror}") from err
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


class GuardedBackend(httpcore.NetworkBackend):
    """Resolve, vet every address, then connect to a vetted address."""

    def __init__(self, policy: Policy, inner: httpcore.NetworkBackend | None = None):
        self._policy = policy
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = resolve(host, port)
        try:
            for address in addresses:
                self._policy.check_address(host, address)
        except EgressError as err:
            raise httpcore.ConnectError(REFUSED + str(err)) from err
        last: Exception | None = None
        for address in addresses:
            try:
                return self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as err:
                last = err
        raise last or httpcore.ConnectError(f'No address for {host}')

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.ConnectError(REFUSED + 'unix sockets are not allowed')

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def guarded_transport(policy: Policy) -> httpx.HTTPTransport:
    """An httpx transport whose connections all go through ``GuardedBackend``.

    httpx 0.28 doesn't take a network backend, so this swaps the one inside the
    transport's httpcore pool. tests/test_egress.py proves it took effect.
    """
    transport = httpx.HTTPTransport(retries=0)
    pool = getattr(transport, '_pool', None)
    if not isinstance(pool, httpcore.ConnectionPool) or not hasattr(pool, '_network_backend'):
        raise RuntimeError('Unsupported httpx version: no connection pool to guard')
    pool._network_backend = GuardedBackend(policy, pool._network_backend)
    return transport


def refused(err: BaseException) -> str | None:
    """The policy message, when an httpx error came from ``GuardedBackend``."""
    text = str(err)
    return text[len(REFUSED) :] if text.startswith(REFUSED) else None
