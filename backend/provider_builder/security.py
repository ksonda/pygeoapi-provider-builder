"""Request guards for a tool bound to 127.0.0.1 (ADR-0012).

- ``Host`` must be in the allowlist, which defeats DNS rebinding.
- State-changing requests need ``X-Provider-Builder: 1`` and, when the
  browser sends one, an allowed ``Origin``. The custom header forces a CORS
  preflight for cross-site requests, and we never grant one.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_HEADER = 'x-provider-builder'
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


class GuardMiddleware:
    def __init__(self, app: ASGIApp, allowed_hosts: list[str], allowed_origins: list[str]):
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.allowed_origins = {o.rstrip('/').lower() for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        problem = self.check(request)
        if problem:
            response: Response = JSONResponse(
                {'type': 'about:blank', 'title': 'Forbidden', 'status': 403, 'detail': problem},
                status_code=403,
                media_type='application/problem+json',
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def check(self, request: Request) -> str | None:
        host = (request.headers.get('host') or '').lower()
        hostname = host.rsplit(':', 1)[0] if not host.startswith('[') else host.split(']')[0][1:]
        if hostname not in self.allowed_hosts:
            return f'Host {host!r} is not allowed'
        if request.method in SAFE_METHODS:
            return None
        if request.headers.get(CSRF_HEADER) != '1':
            return f'Missing {CSRF_HEADER} header'
        origin = request.headers.get('origin')
        # Same-origin is always fine: Host was checked against the allowlist above.
        same_origin = f'{request.url.scheme}://{host}'
        if origin and origin.rstrip('/').lower() not in {*self.allowed_origins, same_origin}:
            return f'Origin {origin!r} is not allowed'
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return 'Cross-site requests are not allowed'
        return None
