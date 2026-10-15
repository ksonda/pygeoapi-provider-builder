"""Read-only database connections for an ejected provider.

Queries run as the Provider Builder's spec engine ran them: each in a read-only
transaction with a statement timeout, always rolled back.

- PostgreSQL: ``default_transaction_read_only`` and ``statement_timeout`` for
  the whole session (libpq ``options``), plus ``SET TRANSACTION READ ONLY`` and
  ``SET LOCAL statement_timeout`` in each transaction.
- SQLite: the file opens ``mode=ro`` with ``PRAGMA query_only``, and a progress
  handler interrupts a statement that runs past the timeout.

Each new PostgreSQL connection resolves the host and checks every address it
resolves to, as the engine did: link-local and cloud-metadata addresses are
refused, and private ones are allowed (databases usually live on private
networks). libpq then connects to the checked address (``hostaddr``), so a DNS
answer can't change in between.

Errors become pygeoapi ``Provider*Error``\\ s with a ``user_msg`` that names no
SQL, path or secret.
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
from decimal import Decimal
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

DEFAULT_PORT = 5432
#: Cloud metadata endpoints outside the link-local ranges.
METADATA = frozenset(ipaddress.ip_address(a) for a in ('fd00:ec2::254', '100.100.100.200'))
#: SQLite progress-handler granularity, in virtual machine instructions.
PROGRESS_STEPS = 10_000

USER_MESSAGES = {
    'connect': "The database couldn't be reached.",
    'auth': 'The database rejected the credentials.',
    'timeout': 'The database query took too long.',
    'readonly': 'The query tried to change data, which a provider may not do.',
    'query': 'The database query failed.',
    'egress': "The database host isn't allowed.",
}


class DatabaseError(Exception):
    """A failed connection or query, with a message that is safe to show."""

    def __init__(self, message: str, *, kind: str):
        super().__init__(message)
        self.kind = kind  # connect | auth | timeout | readonly | query | egress


def provider_error(err: DatabaseError) -> Exception:
    user_msg = USER_MESSAGES.get(err.kind, USER_MESSAGES['query'])
    if err.kind in ('connect', 'auth', 'egress'):
        return ProviderConnectionError(str(err), user_msg=user_msg)  # type: ignore[no-any-return]
    return ProviderQueryError(str(err), user_msg=user_msg)  # type: ignore[no-any-return]


# --- where to connect ------------------------------------------------------------------------


def check_address(host: str, address: str) -> None:
    """Refuse link-local, metadata and reserved addresses (private ones are fine)."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
        address.split('%', 1)[0]
    )
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip in METADATA or ip.is_multicast or ip.is_unspecified:
        raise DatabaseError(
            f'{host} resolves to {ip}, a link-local, metadata or reserved address', kind='egress'
        )
    if ip.is_reserved and not ip.is_loopback:
        raise DatabaseError(f'{host} resolves to {ip}, a reserved address', kind='egress')


def vetted_address(host: str, port: int) -> str:
    """Resolve ``host`` and check every answer; the first address."""
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
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    for address in addresses:
        check_address(host, address)
    return addresses[0]


def sqlite_url(path: Any) -> URL:
    """A read-only SQLite URL for a database file."""
    return make_url(f'sqlite:///file:{quote(str(Path(str(path))))}?mode=ro&uri=true')


def postgresql_url(data: Any) -> tuple[URL, list[str]]:
    """A PostgreSQL URL from ``data`` (a ``postgresql://`` URL or a mapping), and its secrets.

    The mapping takes pygeoapi's PostgreSQL provider keys: host, port, dbname,
    user and password.
    """
    secrets: list[str] = []
    if isinstance(data, Mapping):
        password = '' if data.get('password') is None else str(data['password'])
        if password:
            secrets.append(password)
        url = URL.create(
            'postgresql+psycopg2',
            username=str(data['user']) if data.get('user') else None,
            password=password or None,
            host=str(data.get('host') or ''),
            port=int(data.get('port') or DEFAULT_PORT),
            database=str(data.get('dbname') or ''),
        )
    else:
        dsn = str(data)
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
    if not url.host:
        raise DatabaseError('The connection has no host.', kind='connect')
    return url, secrets


# --- connections -----------------------------------------------------------------------------


class Database:
    """One pooled engine per database; create it once per process and reuse it."""

    def __init__(
        self,
        url: URL,
        *,
        search_path: Sequence[str] = (),
        connect_timeout_s: float = 5,
        statement_timeout_s: float = 10,
        secrets: Sequence[str] = (),
    ):
        self.url = url
        self.dialect = 'sqlite' if url.get_backend_name() == 'sqlite' else 'postgresql'
        self.search_path = tuple(search_path)
        self.connect_timeout_s = float(connect_timeout_s)
        self.statement_timeout_s = float(statement_timeout_s)
        self.secrets = sorted((s for s in secrets if len(s) >= 3), key=len, reverse=True)
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def scrub(self, message: str) -> str:
        for secret in self.secrets:
            message = message.replace(secret, '***')
        return message

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = self._create()
            return self._engine

    def _create(self) -> Engine:
        try:
            if self.dialect == 'sqlite':
                engine = create_engine(self.url)
                event.listen(engine, 'connect', _sqlite_query_only)
                return engine
            options = [
                '-c default_transaction_read_only=on',
                f'-c statement_timeout={int(self.statement_timeout_s * 1000)}',
            ]
            if self.search_path:
                options.append('-c search_path=' + ','.join(self.search_path))
            engine = create_engine(
                self.url,
                connect_args={
                    'connect_timeout': max(1, int(self.connect_timeout_s)),
                    'options': ' '.join(options),
                },
                pool_size=2,
                max_overflow=4,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            event.listen(engine, 'do_connect', self._vet)
            return engine
        except NoSuchModuleError as err:
            raise DatabaseError(f'No database driver for {self.dialect}', kind='connect') from err

    def _vet(self, dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        """Check the address for every new connection; libpq then connects to it."""
        host = str(self.url.host or '')
        cparams['hostaddr'] = vetted_address(host, int(self.url.port or DEFAULT_PORT))

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """A connection inside a read-only transaction that is always rolled back."""
        try:
            with self.engine().connect() as conn:
                try:
                    if self.dialect == 'postgresql':
                        timeout_ms = int(self.statement_timeout_s * 1000)
                        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                        conn.exec_driver_sql(f'SET LOCAL statement_timeout = {timeout_ms}')
                    else:
                        deadline = time.monotonic() + self.statement_timeout_s
                        conn.connection.dbapi_connection.set_progress_handler(  # type: ignore[union-attr]
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

    def classify(self, err: DBAPIError) -> DatabaseError:
        message = self.scrub(str(err.orig or err)).strip()
        first = message.splitlines()[0] if message else type(err).__name__
        lower = message.lower()
        if any(s in lower for s in ('statement timeout', 'canceling statement', 'interrupted')):
            limit = self.statement_timeout_s
            return DatabaseError(f'The query ran longer than {limit:g} s: {first}', kind='timeout')
        if any(s in lower for s in ('read-only', 'readonly', 'query_only')):
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


def database_for(
    data: Any,
    *,
    dialect: str,
    search_path: Sequence[str] = (),
    connect_timeout_s: float = 5,
    statement_timeout_s: float = 10,
) -> Database:
    """The database ``data`` names, one per process: a SQLite file's path, or PostgreSQL's
    connection settings (a URL or a mapping). pygeoapi creates a provider per request."""
    return _database(
        json.dumps(data, sort_keys=True, default=str),
        dialect,
        tuple(search_path),
        float(connect_timeout_s),
        float(statement_timeout_s),
    )


@functools.lru_cache(maxsize=16)
def _database(
    data: str, dialect: str, search_path: tuple[str, ...], connect_s: float, statement_s: float
) -> Database:
    settings = json.loads(data)
    if dialect == 'sqlite':
        url, secrets = sqlite_url(settings), []
    else:
        url, secrets = postgresql_url(settings)
    return Database(
        url,
        search_path=search_path,
        connect_timeout_s=connect_s,
        statement_timeout_s=statement_s,
        secrets=secrets,
    )


def _sqlite_query_only(dbapi_connection: Any, record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA query_only = ON')
    cursor.close()


def rows(
    conn: Connection, sql: str, params: Mapping[str, Any]
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run SQL with bound parameters: its column names and rows."""
    result = conn.exec_driver_sql(sql, dict(params))
    return list(result.keys()), [tuple(r) for r in result.fetchall()]


# --- SQL text ---------------------------------------------------------------------------------


def quote_name(identifier: str) -> str:
    """A quoted SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def escape_like(term: str) -> str:
    """A search term inside a LIKE pattern, with ``\\`` as the escape character."""
    return term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


# --- values ----------------------------------------------------------------------------------


def json_id(value: Any) -> Any:
    """An item id as JSON carries it: integers stay integers, anything else becomes text."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return str(value)


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
    return wkb.loads(bytes(raw[8 + envelope :]))


def decode_geometry(raw: Any, encoding: str) -> dict[str, Any] | None:
    """GeoJSON for one geometry value; None when it's null, empty or unreadable.

    ``encoding`` is native (PostGIS), wkb, gpkg (GeoPackage), wkt or geojson.
    """
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
