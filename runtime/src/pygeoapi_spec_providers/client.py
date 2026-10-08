"""HTTP for one spec's source (PLAN.md §7.3, step 2 of the query pipeline).

Auth comes from the spec, whose secrets are ``${VAR}`` placeholders resolved
by ``yaml_load``. Every connection goes through the egress policy. Error
messages name the host and path, never the query string, which may carry a key.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from . import __version__
from .egress import EgressError, Policy, guarded_transport, refused
from .formatter import to_text
from .spec import Spec

MAX_REDIRECTS = 5
MAX_RETRY_AFTER_S = 30.0
CACHE_ENTRIES = 128


class SourceError(Exception):
    """A failed request. The message is safe to show: no secrets, no query strings."""

    def __init__(self, message: str, *, kind: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class Response:
    status: int
    url: httpx.URL
    headers: httpx.Headers
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as err:
            raise SourceError(
                f"{describe(self.url)} didn't return JSON", kind='not_json', status=self.status
            ) from err


def describe(url: httpx.URL) -> str:
    """scheme://host[:port]/path, without the query string."""
    port = f':{url.port}' if url.port else ''
    return f'{url.scheme}://{url.host}{port}{url.path}'


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


class RateLimiter:
    """A token bucket: ``requests`` per ``per_s`` seconds, per process."""

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


def retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get('retry-after')
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class SourceClient:
    def __init__(self, spec: Spec, environ: Mapping[str, str] | None = None):
        source = spec.source
        self.policy = Policy.for_hosts(spec.hosts(), environ)
        self.timeout = float(source['timeout_s'])
        self.retries = dict(source['retries'])
        self.max_bytes = int(source['max_response_bytes'])
        self.cache_ttl = float(source['cache_ttl_s'])
        self.static_query = {str(k): to_text(v) for k, v in source['query'].items()}
        headers = {
            'User-Agent': f'pygeoapi-spec-providers/{__version__}',
            'Accept': 'application/json',
        }
        headers.update({str(k): str(v) for k, v in source['headers'].items()})
        self.auth_query: dict[str, str] = {}
        self.secrets: list[str] = []
        basic: httpx.BasicAuth | None = None
        auth = source.get('auth') or {}
        kind = auth.get('type')
        if kind == 'header':
            headers[str(auth['name'])] = self._secret(auth['value'])
        elif kind == 'query':
            self.auth_query[str(auth['name'])] = self._secret(auth['value'])
        elif kind == 'bearer':
            headers['Authorization'] = f'Bearer {self._secret(auth["value"])}'
        elif kind == 'basic':
            basic = httpx.BasicAuth(str(auth['username']), self._secret(auth['password']))
        self.client = httpx.Client(
            transport=guarded_transport(self.policy),
            headers=headers,
            auth=basic,
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            trust_env=False,
        )
        rate = source.get('rate_limit')
        host = httpx.URL(source['url']).host
        self.limiter = (
            limiter_for(host, int(rate['requests']), float(rate['per_s'])) if rate else None
        )
        self._cache: OrderedDict[str, tuple[float, Response]] = OrderedDict()
        self._lock = threading.Lock()

    def _secret(self, value: Any) -> str:
        # yaml_load re-types substituted values, so a numeric key arrives as an int (notes §2).
        text = str(value)
        if text:
            self.secrets.append(text)
        return text

    def scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, '***')
        return text

    def get(
        self, url: str | httpx.URL, params: Mapping[str, str] | None = None, *, static: bool = True
    ) -> Response:
        """GET a URL of this spec, with its auth.

        ``static`` adds the spec's fixed query parameters.
        """
        merged = {**(self.static_query if static else {}), **(params or {}), **self.auth_query}
        request_url = httpx.URL(url).copy_merge_params(merged)
        key = str(request_url)
        if self.cache_ttl:
            with self._lock:
                hit = self._cache.get(key)
                if hit and hit[0] > time.monotonic():
                    self._cache.move_to_end(key)
                    return hit[1]
        try:
            response = self._with_retries(request_url)
        except SourceError as err:
            raise SourceError(self.scrub(str(err)), kind=err.kind, status=err.status) from err
        if self.cache_ttl and 200 <= response.status < 300:
            with self._lock:
                self._cache[key] = (time.monotonic() + self.cache_ttl, response)
                while len(self._cache) > CACHE_ENTRIES:
                    self._cache.popitem(last=False)
        return response

    def _with_retries(self, url: httpx.URL) -> Response:
        attempts = 1 + int(self.retries['max'])
        on_status = set(self.retries['on_status'])
        backoff = float(self.retries['backoff_s'])
        for attempt in range(attempts):
            last = attempt + 1 == attempts
            try:
                response = self._follow(url)
            except SourceError as err:
                if err.kind in ('connect', 'timeout') and not last:
                    time.sleep(backoff * 2**attempt)
                    continue
                raise
            if response.status in on_status and not last:
                wait = retry_after(response.headers)
                time.sleep(min(MAX_RETRY_AFTER_S, backoff * 2**attempt if wait is None else wait))
                continue
            return response
        raise AssertionError('unreachable')

    def _follow(self, url: httpx.URL) -> Response:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                self.policy.check_url(url)
            except EgressError as err:
                raise SourceError(str(err), kind='egress') from err
            if self.limiter:
                self.limiter.acquire()
            try:
                with self.client.stream('GET', url) as r:
                    if r.is_redirect:
                        location = r.headers.get('location')
                        if not location:
                            raise SourceError(
                                f'{describe(url)} redirected without a location',
                                kind='status',
                                status=r.status_code,
                            )
                        url = url.join(location)
                        continue
                    return Response(r.status_code, url, r.headers, self._read(r, url))
            except httpx.TimeoutException as err:
                raise SourceError(
                    f'{describe(url)} timed out after {self.timeout:g} s', kind='timeout'
                ) from err
            except httpx.TransportError as err:
                why = refused(err)
                if why:
                    raise SourceError(why, kind='egress') from err
                raise SourceError(f"Couldn't reach {url.host}: {err}", kind='connect') from err
        raise SourceError(
            f'{describe(url)} redirected more than {MAX_REDIRECTS} times', kind='status'
        )

    def _read(self, r: httpx.Response, url: httpx.URL) -> bytes:
        too_large = SourceError(
            f'{describe(url)} sent more than {self.max_bytes:,} bytes', kind='too_large'
        )
        declared = r.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > self.max_bytes:
            raise too_large
        chunks, total = [], 0
        for chunk in r.iter_bytes():  # decoded, so a compressed bomb counts at full size
            total += len(chunk)
            if total > self.max_bytes:
                raise too_large
            chunks.append(chunk)
        return b''.join(chunks)
