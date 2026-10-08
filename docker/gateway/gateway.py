"""Egress gateway for one sandbox run (PLAN.md §8.2, ADR-0008).

Forwards TCP from the run's internal network to one declared host, on the
declared ports only. The gateway resolves the host itself and vets every
address: link-local, metadata, multicast, unspecified and reserved addresses
are always refused, and other non-public addresses are refused unless the
port is marked private (the Provider Builder's admin allowlist, such as
postgis:5432 in the dev stack). TLS passes through untouched, so certificates
are verified end to end by the client in the sandbox.

Usage: python3 gateway.py HOST PORT[:private] [PORT[:private] ...]

It binds to its own address on the internal network only, then prints JSON
lines: one "ready" event, then one per connection. Standard library only.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import socket
import sys
import time

METADATA = {ipaddress.ip_address('fd00:ec2::254'), ipaddress.ip_address('100.100.100.200')}
CONNECT_TIMEOUT_S = 10
IDLE_TIMEOUT_S = 300


def log(**event: object) -> None:
    print(json.dumps({'t': round(time.time(), 3), **event}), flush=True)


def vet(address: str, private: bool) -> str | None:
    """Why an address is refused, or None."""
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip in METADATA or ip.is_multicast or ip.is_unspecified:
        return 'link-local, metadata or multicast address'
    if ip.is_reserved and not ip.is_loopback:
        return 'reserved address'
    if not ip.is_global and not private:
        return 'non-public address, not on the allowlist'
    return None


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT_S):
            writer.write(data)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


def handler(host: str, port: int, private: bool):  # noqa: ANN201
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as err:
            log(event='refused', host=host, port=port, reason=f"can't resolve: {err.strerror}")
            writer.close()
            return
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        for address in addresses:
            reason = vet(address, private)
            if reason:  # one bad answer refuses the whole connection
                log(event='refused', host=host, port=port, address=address, reason=reason)
                writer.close()
                return
        for address in addresses:
            try:
                up_reader, up_writer = await asyncio.wait_for(
                    asyncio.open_connection(address, port), CONNECT_TIMEOUT_S
                )
            except (OSError, TimeoutError):
                continue
            log(event='connect', host=host, port=port, address=address)
            await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
            return
        log(event='unreachable', host=host, port=port, addresses=addresses)
        writer.close()

    return handle


async def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    host = argv[0]
    bind = socket.gethostbyname(socket.gethostname())  # only the internal network is attached yet
    servers = []
    for spec in argv[1:]:
        text, _, flag = spec.partition(':')
        port = int(text)
        server = await asyncio.start_server(handler(host, port, flag == 'private'), bind, port)
        servers.append(server)
    log(event='ready', host=host, bind=bind, ports=argv[1:])
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
