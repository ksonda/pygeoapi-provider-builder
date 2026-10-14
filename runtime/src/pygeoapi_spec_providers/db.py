"""Database connections for SQL specs (ADR-0007, PLAN.md §7.2).

Every query runs in a read-only transaction with a statement timeout:

- PostgreSQL: ``SET TRANSACTION READ ONLY`` and ``SET LOCAL statement_timeout``
  in each transaction, on top of the same two settings for the whole session
  (libpq ``options``). The host is resolved and vetted here (never link-local
  or a cloud-metadata address), and libpq connects to the vetted address
  (``hostaddr``), so a DNS answer can't change in between. Private addresses
  are allowed: databases usually live on private networks, and in the sandbox
  the egress gateway decides.
- SQLite: the file is opened ``mode=ro`` with ``PRAGMA query_only``, and a
  progress handler interrupts a statement that runs past the timeout.

Nothing is ever committed. Errors become pygeoapi ``Provider*Error``\\ s with a
``user_msg`` that names no SQL, path or secret.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import socket
import struct
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pygeoapi.provider.base import ProviderConnectionError, ProviderQueryError
from shapely import wkb, wkt
from shapely.errors import ShapelyError
from shapely.geometry import mapping, shape
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError, NoSuchModuleError, SQLAlchemyError

from .egress import METADATA, EgressError

DEFAULT_PORT = 5432
#: SQLite progress-handler granularity, in virtual machine instructions.
PROGRESS_STEPS = 10_000


class DatabaseError(Exception):
    """A failed connection or query, with a message that is safe to show."""

    def __init__(self, message: str, *, kind: str):
        super().__init__(message)
        self.kind = kind  # connect | auth | timeout | readonly | query | egress


USER_MESSAGES = {
    'connect': "The database couldn't be reached.",
    'auth': 'The database rejected the credentials.',
    'timeout': 'The database query took too long.',
    'readonly': 'The query tried to change data, which a provider may not do.',
    'query': 'The database query failed.',
    'egress': "The database host isn't allowed.",
}


def provider_error(err: DatabaseError) -> Exception:
    user_msg = USER_MESSAGES.get(err.kind, USER_MESSAGES['query'])
    if err.kind in ('connect', 'auth', 'egress'):
        return ProviderConnectionError(str(err), user_msg=user_msg)  # type: ignore[no-any-return]
    return ProviderQueryError(str(err), user_msg=user_msg)  # type: ignore[no-any-return]


def check_address(host: str, address: str) -> None:
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip in METADATA or ip.is_multicast or ip.is_unspecified:
        raise EgressError(f'{host} resolves to {ip}, a link-local, metadata or reserved address')
    if ip.is_reserved and not ip.is_loopback:
        raise EgressError(f'{host} resolves to {ip}, a reserved address')


def vetted_address(host: str, port: int) -> str:
    """Resolve ``host`` and vet every answer; return the first address."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as err:
            raise DatabaseError(
                f"Couldn't resolve {host}: {err.strerror}", kind='connect'
            ) from err
        addresses = list(dict.fromkeys(str(i[4][0]) for i in infos))
    try:
        for address in addresses:
            check_address(host, address)
    except EgressError as err:
        raise DatabaseError(str(err), kind='egress') from err
    return addresses[0]


# --- connections ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """Where and how to connect, from a spec's ``source`` block (placeholders resolved)."""

    dialect: str
    url: URL
    host: str | None = None
    port: int = DEFAULT_PORT
    search_path: tuple[str, ...] = ()
    connect_timeout_s: float = 5
    statement_timeout_s: float = 10
    secrets: tuple[str, ...] = field(default=(), compare=False)


def _text(value: Any) -> str:
    # yaml_load re-types substituted values: a numeric password arrives as an int (notes §2).
    return '' if value is None else str(value)


def target_for(source: Mapping[str, Any]) -> Target:
    dialect = source['dialect']
    connect_timeout = float(source.get('connect_timeout_s', 5))
    statement_timeout = float(source.get('statement_timeout_s', 10))
    if dialect == 'sqlite':
        path = Path(str(source['path']))
        url = make_url(f'sqlite:///file:{quote(str(path))}?mode=ro&uri=true')
        return Target(
            'sqlite', url, connect_timeout_s=connect_timeout, statement_timeout_s=statement_timeout
        )
    secrets: list[str] = []
    if source.get('dsn') is not None:
        dsn = _text(source['dsn'])
        secrets.append(dsn)
        try:
            url = make_url(dsn)
        except Exception as err:  # noqa: BLE001 - any malformed URL
            raise DatabaseError(
                'The connection string is not a valid URL.', kind='connect'
            ) from err
        if url.get_backend_name() not in ('postgresql', 'postgres'):
            raise DatabaseError(
                'The connection string must start with postgresql://', kind='connect'
            )
        url = url.set(drivername='postgresql+psycopg2')
        if url.password:
            secrets.append(str(url.password))
    else:
        c = source['connection']
        password = _text(c.get('password')) or None
        if password:
            secrets.append(password)
        url = URL.create(
            'postgresql+psycopg2',
            username=_text(c.get('user')) or None,
            password=password,
            host=_text(c['host']),
            port=int(c.get('port') or DEFAULT_PORT),
            database=_text(c['dbname']),
        )
    if not url.host:
        raise DatabaseError('The connection has no host.', kind='connect')
    return Target(
        'postgresql',
        url,
        host=str(url.host),
        port=int(url.port or DEFAULT_PORT),
        search_path=tuple(source.get('search_path') or ()),
        connect_timeout_s=connect_timeout,
        statement_timeout_s=statement_timeout,
        secrets=tuple(s for s in secrets if s),
    )


class Database:
    """One pooled engine per target and process; pygeoapi builds providers per request."""

    def __init__(self, target: Target):
        self.target = target
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def scrub(self, text: str) -> str:
        for secret in sorted(self.target.secrets, key=len, reverse=True):
            if len(secret) >= 3:
                text = text.replace(secret, '***')
        return text

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = self._create()
            return self._engine

    def _create(self) -> Engine:
        t = self.target
        try:
            if t.dialect == 'sqlite':
                engine = create_engine(t.url)
                event.listen(engine, 'connect', _sqlite_on_connect)
                return engine
            options = [
                '-c default_transaction_read_only=on',
                f'-c statement_timeout={int(t.statement_timeout_s * 1000)}',
            ]
            if t.search_path:
                options.append('-c search_path=' + ','.join(t.search_path))
            args: dict[str, Any] = {
                'connect_timeout': max(1, int(t.connect_timeout_s)),
                'options': ' '.join(options),
                'application_name': 'pygeoapi-spec-providers',
            }
            engine = create_engine(
                t.url,
                connect_args=args,
                pool_size=2,
                max_overflow=4,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            event.listen(engine, 'do_connect', self._vet)
            return engine
        except NoSuchModuleError as err:
            raise DatabaseError(f'No database driver for {t.dialect}', kind='connect') from err

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """A connection inside a read-only transaction that is always rolled back."""
        t = self.target
        engine = self.engine()
        try:
            with engine.connect() as conn:
                try:
                    if t.dialect == 'postgresql':
                        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                        conn.exec_driver_sql(
                            f'SET LOCAL statement_timeout = {int(t.statement_timeout_s * 1000)}'
                        )
                    else:
                        raw = conn.connection.dbapi_connection
                        deadline = time.monotonic() + t.statement_timeout_s
                        raw.set_progress_handler(  # type: ignore[union-attr]
                            lambda: int(time.monotonic() > deadline), PROGRESS_STEPS
                        )
                    yield conn
                finally:
                    conn.rollback()
        except DatabaseError:
            raise
        except DBAPIError as err:
            raise self.classify(err) from err
        except SQLAlchemyError as err:
            raise DatabaseError(self.scrub(str(err).splitlines()[0]), kind='query') from err

    def _vet(self, dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        """Vet the address for every new connection; libpq then connects to it (``hostaddr``)."""
        assert self.target.host is not None
        cparams['hostaddr'] = vetted_address(self.target.host, self.target.port)

    def classify(self, err: DBAPIError) -> DatabaseError:
        original = err.orig
        text = self.scrub(str(original or err)).strip()
        first = text.splitlines()[0] if text else type(err).__name__
        lower = text.lower()
        if (
            'statement timeout' in lower
            or 'canceling statement' in lower
            or 'interrupted' in lower
        ):
            limit = self.target.statement_timeout_s
            return DatabaseError(f'The query ran longer than {limit:g} s: {first}', kind='timeout')
        if 'read-only' in lower or 'readonly' in lower or 'query_only' in lower:
            return DatabaseError(f'The query tried to write: {first}', kind='readonly')
        if 'password authentication' in lower or 'authentication failed' in lower:
            return DatabaseError(f'The database rejected the credentials: {first}', kind='auth')
        if err.connection_invalidated or any(
            s in lower
            for s in (
                'could not connect',
                'connection refused',
                'could not translate host',
                'timeout expired',
                'unable to open database',
                'no route to host',
                'server closed the connection',
            )
        ):
            return DatabaseError(f"Couldn't connect to the database: {first}", kind='connect')
        return DatabaseError(f'The query failed: {first}', kind='query')

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


def _sqlite_on_connect(dbapi_connection: Any, record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA query_only = ON')
    cursor.close()


@functools.lru_cache(maxsize=32)
def database_for(target: Target) -> Database:
    return Database(target)


# --- running queries -----------------------------------------------------------------------


@dataclass(frozen=True)
class Dialect:
    """Identifier quoting and parameter markers for the drivers we use."""

    name: str

    def quote(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def param(self, name: str) -> str:
        return f'%({name})s' if self.name == 'postgresql' else f':{name}'

    @property
    def like(self) -> str:
        return 'ILIKE' if self.name == 'postgresql' else 'LIKE'

    def text(self, expression: str) -> str:
        return f'CAST({expression} AS TEXT)'


def rows(
    conn: Connection, sql: str, params: Mapping[str, Any]
) -> tuple[list[str], list[tuple[Any, ...]]]:
    result = conn.exec_driver_sql(sql, dict(params))
    columns = list(result.keys())
    return columns, [tuple(r) for r in result.fetchall()]


# --- geometry ------------------------------------------------------------------------------


def _wkb(raw: Any) -> Any:
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, str):  # psycopg2 returns PostGIS geometry as hex (E)WKB
        return wkb.loads(raw, hex=True)
    if isinstance(raw, bytes | bytearray):
        return wkb.loads(bytes(raw))
    raise ValueError('not WKB')


GPKG_ENVELOPE = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _gpkg(raw: Any) -> Any:
    """A GeoPackage geometry blob: a ``GP`` header, an optional envelope, then WKB."""
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if not isinstance(raw, bytes | bytearray) or len(raw) < 8 or raw[:2] != b'GP':
        raise ValueError('not a GeoPackage geometry')
    flags = raw[3]
    if flags & 0b0001_0000:  # the empty-geometry flag
        raise ValueError('empty')
    envelope = GPKG_ENVELOPE.get((flags >> 1) & 0b111)
    if envelope is None:
        raise ValueError('bad GeoPackage envelope code')
    struct.unpack('<i' if flags & 1 else '>i', raw[4:8])  # srs_id: the spec declares the CRS
    return wkb.loads(bytes(raw[8 + envelope :]))


def decode_geometry(raw: Any, encoding: str) -> dict[str, Any] | None:
    """GeoJSON for one geometry value; None when it's null, empty or unreadable."""
    if raw is None or raw == '':
        return None
    try:
        if encoding in ('native', 'wkb'):
            geom = _wkb(raw)
        elif encoding == 'gpkg':
            geom = _gpkg(raw)
        elif encoding == 'wkt':
            geom = wkt.loads(str(raw))
        elif encoding == 'geojson':
            geom = shape(json.loads(raw) if isinstance(raw, str | bytes) else raw)
        else:
            return None
        if geom.is_empty:
            return None
        result: dict[str, Any] = json.loads(json.dumps(mapping(geom)))
        return result
    except (ShapelyError, ValueError, TypeError, KeyError, AttributeError, struct.error):
        return None


def srid_of(crs: str | None) -> int:
    """The EPSG code of an OGC CRS URI (CRS84 is 4326 with lon/lat order, as PostGIS has it)."""
    if not crs or crs.rstrip('/').endswith('CRS84'):
        return 4326
    tail = crs.rstrip('/').rsplit('/', 1)[-1]
    return int(tail) if tail.isdigit() else 4326


def bounds(values: Sequence[float]) -> tuple[float, float, float, float]:
    b = list(values)
    return (b[0], b[1], b[3], b[4]) if len(b) == 6 else (b[0], b[1], b[2], b[3])
