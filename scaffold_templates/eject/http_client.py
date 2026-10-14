"""HTTP for an ejected provider: auth, retries, redirects, a size cap and safe errors.

These are the rules the Provider Builder's spec engine applied, written for
``requests`` (which pygeoapi already depends on):

- Every URL, redirects and next links included, must be on a host the
  provider declares (``hosts``).
- Connection failures, timeouts and the ``retry_on`` statuses are retried with
  exponential backoff, honouring ``Retry-After`` up to 30 s.
- A response larger than ``max_bytes`` after decompression is refused.
- Error messages name the host and path, never the query string, which may
  carry a key; the secrets given to the client are scrubbed from them too.

One thing the engine did that this doesn't: it resolved each host itself and
refused link-local and cloud-metadata addresses. Run the provider where its
network reaches only the hosts it needs.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from pygeoapi.provider.base import ProviderConnectionError, ProviderQueryError

MAX_REDIRECTS = 5
MAX_RETRY_AFTER_S = 30.0
CACHE_ENTRIES = 128
CHUNK_BYTES = 65_536

USER_MESSAGES = {
    'timeout': 'The data source timed out.',
    'connect': "The data source couldn't be reached.",
    'egress': "The data source isn't allowed by this server's egress rules.",
    'too_large': 'The data source sent a response larger than allowed.',
    'not_json': "The data source didn't return JSON.",
    'api_error': 'The data source reported an error.',
}


class SourceError(Exception):
    """A failed request. The message is safe to show: no secrets, no query strings."""

    def __init__(self, message: str, *, kind: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def user_message(err: SourceError) -> str:
    if err.kind == 'status':
        if err.status in (401, 403):
            return 'The data source rejected the credentials.'
        if err.status == 429:
            return 'The data source is limiting requests; try again later.'
        return f'The data source returned HTTP {err.status}.'
    return USER_MESSAGES.get(err.kind, 'The data source failed.')


def provider_error(err: SourceError) -> Exception:
    """The pygeoapi error for a failed request, with a ``user_msg`` (and a 502 or 500)."""
    connection = err.kind in ('egress', 'connect', 'timeout') or err.status in (401, 403)
    cls = ProviderConnectionError if connection else ProviderQueryError
    return cls(str(err), user_msg=user_message(err))  # type: ignore[no-any-return]


def describe(url: str) -> str:
    """scheme://host[:port]/path, without the query string."""
    parts = urlsplit(url)
    port = f':{parts.port}' if parts.port else ''
    return f'{parts.scheme}://{parts.hostname}{port}{parts.path}'


def with_params(url: str, params: Mapping[str, str]) -> str:
    """The URL with ``params`` merged into its query string; a key in both takes the new value."""
    parts = urlsplit(url)
    query: dict[str, list[str]] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.setdefault(key, []).append(value)
    for key, value in params.items():
        query[key] = [value]
    pairs = [(key, value) for key, values in query.items() for value in values]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def link_header_next(value: str | None) -> str | None:
    """The ``rel="next"`` target of an RFC 8288 Link header."""
    if not value:
        return None
    for part in value.split(','):
        target, _, params = part.partition(';')
        target = target.strip()
        rels = [
            p.split('=', 1)[1].strip().strip('"')
            for p in params.split(';')
            if '=' in p and p.split('=', 1)[0].strip().lower() == 'rel'
        ]
        if (
            target.startswith('<')
            and target.endswith('>')
            and any('next' in r.split() for r in rels)
        ):
            return target[1:-1]
    return None


@dataclass(frozen=True)
class Response:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as err:
            raise SourceError(
                f"{describe(self.url)} didn't return JSON", kind='not_json', status=self.status
            ) from err


def status_error(response: Response) -> SourceError:
    status, where = response.status, describe(response.url)
    if status in (401, 403):
        message = f'{where} rejected the credentials (HTTP {status})'
    elif status == 404:
        message = f'{where} was not found (HTTP 404)'
    elif status == 429:
        message = f'{where} is rate limiting requests (HTTP 429)'
    elif status >= 500:
        message = f'{where} had a server error (HTTP {status})'
    else:
        message = f'{where} returned HTTP {status}'
    return SourceError(message, kind='status', status=status)


def retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get('retry-after')
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class RateLimiter:
    """A token bucket: ``requests`` per ``per_s`` seconds, shared by the whole process."""

    def __init__(self, requests: int, per_s: float):
        self.capacity = float(requests)
        self.rate = requests / per_s
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.rate)
                self.tokens = 1.0
                self.updated = time.monotonic()
            self.tokens -= 1


_LIMITERS: dict[tuple[str, int, float], RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def limiter_for(host: str, requests: int, per_s: float) -> RateLimiter:
    key = (host, requests, per_s)
    with _LIMITERS_LOCK:
        if key not in _LIMITERS:
            _LIMITERS[key] = RateLimiter(requests, per_s)
        return _LIMITERS[key]


class Client:
    """GETs on the declared hosts. Create one per process and reuse it: pygeoapi
    creates a provider per request, and the cache and rate limit live here."""

    def __init__(
        self,
        hosts: Iterable[str],
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        auth_query: Mapping[str, str] | None = None,
        basic_auth: tuple[str, str] | None = None,
        timeout_s: float = 20,
        retries: int = 2,
        backoff_s: float = 1,
        retry_on: Iterable[int] = (429, 502, 503, 504),
        max_bytes: int = 20_000_000,
        cache_ttl_s: float = 0,
        rate_limit: RateLimiter | None = None,
        secrets: Iterable[str] = (),
    ):
        self.hosts = frozenset(h.lower() for h in hosts)
        self.headers = {'Accept': 'application/json', **(headers or {})}
        self.query = dict(query or {})
        self.auth_query = dict(auth_query or {})
        self.basic_auth = basic_auth
        self.timeout_s = float(timeout_s)
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)
        self.retry_on = frozenset(retry_on)
        self.max_bytes = int(max_bytes)
        self.cache_ttl_s = float(cache_ttl_s)
        self.rate_limit = rate_limit
        self.secrets = [s for s in secrets if s]
        self._cache: OrderedDict[str, tuple[float, Response]] = OrderedDict()
        self._lock = threading.Lock()
        self._local = threading.local()

    def session(self) -> requests.Session:
        """One session per thread: requests doesn't promise a shared one is thread-safe."""
        session: requests.Session | None = getattr(self._local, 'session', None)
        if session is None:
            session = requests.Session()
            session.trust_env = False  # no proxies or .netrc credentials from the environment
            session.headers.update(self.headers)
            session.auth = self.basic_auth
            self._local.session = session
        return session

    def scrub(self, message: str) -> str:
        for secret in self.secrets:
            message = message.replace(secret, '***')
        return message

    def check(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme not in ('http', 'https'):
            raise SourceError(f'{parts.scheme} URLs are not allowed', kind='egress')
        if host not in self.hosts:
            raise SourceError(f'{host} is not a host this provider declares', kind='egress')

    def get(
        self, url: str, params: Mapping[str, str] | None = None, *, static: bool = True
    ) -> Response:
        """GET a URL with the provider's auth; ``static`` adds its fixed query parameters."""
        merged = {**(self.query if static else {}), **(params or {}), **self.auth_query}
        target = with_params(url, merged)
        if self.cache_ttl_s:
            with self._lock:
                hit = self._cache.get(target)
                if hit and hit[0] > time.monotonic():
                    self._cache.move_to_end(target)
                    return hit[1]
        try:
            response = self._with_retries(target)
        except SourceError as err:
            raise SourceError(self.scrub(str(err)), kind=err.kind, status=err.status) from err
        if self.cache_ttl_s and 200 <= response.status < 300:
            with self._lock:
                self._cache[target] = (time.monotonic() + self.cache_ttl_s, response)
                while len(self._cache) > CACHE_ENTRIES:
                    self._cache.popitem(last=False)
        return response

    def _with_retries(self, url: str) -> Response:
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                response = self._follow(url)
            except SourceError as err:
                if err.kind in ('connect', 'timeout') and not last:
                    time.sleep(self.backoff_s * 2**attempt)
                    continue
                raise
            if response.status in self.retry_on and not last:
                wait = retry_after(response.headers)
                backoff = self.backoff_s * 2**attempt
                time.sleep(min(MAX_RETRY_AFTER_S, backoff if wait is None else wait))
                continue
            return response
        raise AssertionError('unreachable')

    def _follow(self, url: str) -> Response:
        for _ in range(MAX_REDIRECTS + 1):
            self.check(url)
            if self.rate_limit:
                self.rate_limit.acquire()
            try:
                with self.session().get(
                    url, timeout=self.timeout_s, allow_redirects=False, stream=True
                ) as r:
                    if r.is_redirect:
                        location = r.headers.get('location')
                        if not location:
                            raise SourceError(
                                f'{describe(url)} redirected without a location',
                                kind='status',
                                status=r.status_code,
                            )
                        url = urljoin(url, location)
                        continue
                    return Response(r.status_code, url, r.headers, self._read(r, url))
            except requests.Timeout as err:
                raise SourceError(
                    f'{describe(url)} timed out after {self.timeout_s:g} s', kind='timeout'
                ) from err
            except requests.RequestException as err:
                # requests' own message includes the query string: name the error only.
                host = urlsplit(url).hostname
                raise SourceError(
                    f"Couldn't reach {host}: {type(err).__name__}", kind='connect'
                ) from err
        raise SourceError(
            f'{describe(url)} redirected more than {MAX_REDIRECTS} times', kind='status'
        )

    def _read(self, r: requests.Response, url: str) -> bytes:
        too_large = SourceError(
            f'{describe(url)} sent more than {self.max_bytes:,} bytes', kind='too_large'
        )
        declared = r.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > self.max_bytes:
            raise too_large
        chunks, total = [], 0
        for chunk in r.iter_content(CHUNK_BYTES):  # decoded, so a compressed bomb counts in full
            total += len(chunk)
            if total > self.max_bytes:
                raise too_large
            chunks.append(chunk)
        return b''.join(chunks)
