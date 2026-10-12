"""The Admin API target: guarded and optional (ADR-0015, docs/pygeoapi-notes.md §10).

Guardrails:
- One write per publish, and only to a target that restarts workers after a
  write (``reload: watch`` or ``command``): Admin API writes start from the
  config loaded at import, so a second write in the same worker silently undoes
  the first.
- Secret-bearing providers need ``acknowledge: [admin-secrets]``: with
  ``server.admin: true`` the generated OpenAPI embeds the resolved config,
  secrets included (``pygeoapi/openapi.py:819``).
- The plan warns that the Admin API rewrites the file and drops comments.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from . import configedit as ce
from . import yamlio
from .publish import Backups, LogEntry, Step, new_id
from .settings import Target

if TYPE_CHECKING:
    from .targets import PublishPlan, PublishRequest, TargetOps

ACK_SECRETS = 'admin-secrets'
WARNING = (
    'The Admin API rewrites the whole config file with PyYAML: comments, quoting and '
    'anchors are lost (pygeoapi/util.py:224). The file target keeps them.'
)


def base_url(target: Target) -> str:
    if target.admin_url:
        return target.admin_url.rstrip('/')
    if not target.server_url:
        raise ValueError('An admin target needs server_url or admin_url.')
    return target.server_url.rstrip('/') + '/admin'


def read(target: Target) -> str:
    """The raw config (placeholders kept) as YAML, so it can be planned like a file."""
    r = httpx.get(f'{base_url(target)}/config', params={'f': 'json'}, timeout=15)
    r.raise_for_status()
    return yamlio.dump(r.json())


def _json(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            default=lambda v: (
                v.isoformat().replace('+00:00', 'Z') if isinstance(v, dt.date) else str(v)
            ),
        )
    )


def has_secrets(ops: TargetOps, config_text: str) -> list[str]:
    """Resources whose providers carry ``${VAR}`` values or secret fields."""
    found = []
    for rid, res in ce.resources(ce.load(config_text) or {}).items():
        for p in (res or {}).get('providers') or []:
            if not isinstance(p, dict):
                continue
            d = ops.svc.catalog.by_plugin(str(p.get('name')))
            secret_keys = [f['key'] for f in d.all_fields() if f['type'] == 'secret'] if d else []
            if '${' in json.dumps(_json(p)) or any(_get(p, k) for k in secret_keys):
                found.append(str(rid))
                break
    return found


def _get(node: Any, dotted: str) -> Any:
    for part in dotted.split('.'):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def guard(ops: TargetOps, target: Target, req: PublishRequest, after: str) -> list[str]:
    errors = []
    if target.reload not in ('watch', 'command'):
        errors.append(
            'An Admin API target must restart workers after each write (reload: watch or '
            'command). Otherwise a second write silently undoes the first and new resources '
            "aren't served (docs/pygeoapi-notes.md §10)."
        )
    if req.form is not None and req.form.resource_id != req.resource_id and req.action == 'update':
        errors.append('Renaming needs two Admin API writes; rename on a file target instead.')
    secrets = has_secrets(ops, after)
    if secrets and ACK_SECRETS not in req.acknowledge:
        errors.append(
            f'Resources with secrets ({", ".join(secrets)}) on a server with the Admin API: '
            'its OpenAPI document embeds the resolved config, secrets included, and serves it '
            'publicly at /openapi (pygeoapi/openapi.py:819). Acknowledge this to continue.'
        )
    return errors


def _write(target: Target, action: str, rid: str, res: dict[str, Any] | None) -> httpx.Response:
    base = f'{base_url(target)}/config/resources'
    if action == 'add':
        return httpx.post(base, json={rid: _json(res)}, timeout=60)
    if action == 'update':
        return httpx.put(f'{base}/{rid}', json=_json(res), timeout=60)
    return httpx.delete(f'{base}/{rid}', timeout=60)


def publish_admin(
    ops: TargetOps,
    target: Target,
    req: PublishRequest,
    plan: PublishPlan,
    before: str,
    after: str,
    new_res: dict[str, Any] | None,
    steps: list[Step],
    result: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    errors = guard(ops, target, req, after)
    if errors:
        steps.append(Step(name='guard', ok=False, message=' '.join(errors)))
        return result(False, plan=plan.model_dump())
    steps.append(Step(name='guard', ok=True, message=WARNING))
    backup = Backups(ops.settings, target).save(target.base_config, before)
    rid = plan.resource_id
    try:
        r = _write(target, req.action, rid, new_res)
    except httpx.HTTPError as err:
        steps.append(Step(name='write', ok=False, message=f"Couldn't reach the Admin API: {err}"))
        return result(False, plan=plan.model_dump())
    if r.status_code not in (200, 201, 204):
        steps.append(
            Step(
                name='write',
                ok=False,
                message=f'The Admin API refused the write ({r.status_code}).',
                detail=r.text[:1000],
            )
        )
        return result(False, plan=plan.model_dump())
    steps.append(
        Step(name='write', ok=True, message=f'Admin API {r.request.method} {r.status_code}')
    )
    entry = LogEntry(
        id=new_id(),
        time=dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        target=target.id,
        action=req.action,
        resource_id=rid,
        providers=ops.provider_refs(new_res),
        status='written',
        config_hash_before=plan.etag,
        config_hash_after=plan.etag_after,
        backup=backup,
    )
    entry = ops.finish(target, entry, req.action, new_res, steps)
    steps.append(_reread(target, rid, req.action != 'delete'))
    return result(entry.status == 'published' and steps[-1].ok, entry, plan=plan.model_dump())


def _reread(target: Target, rid: str, present: bool) -> Step:
    """Re-read ``GET /admin/config/resources/{id}`` after the reload (ADR-0015)."""
    try:
        r = httpx.get(
            f'{base_url(target)}/config/resources/{rid}', params={'f': 'json'}, timeout=15
        )
    except httpx.HTTPError as err:
        return Step(name='admin-read', ok=False, message=f"Couldn't re-read the resource: {err}")
    ok = (r.status_code == 200) == present
    return Step(
        name='admin-read',
        ok=ok,
        message='The Admin API reports the change.' if ok else 'The Admin API disagrees.',
        detail=r.status_code,
    )


def rollback_admin(
    ops: TargetOps, target: Target, entry: LogEntry, steps: list[Step]
) -> dict[str, Any]:
    """``PUT`` the previous definition, ``DELETE`` a new one or ``POST`` a deleted one."""
    if not entry.backup:
        raise ValueError('That publish has no backup to restore.')
    previous = ce.resources(ce.load(Backups(ops.settings, target).read(entry.backup)) or {})
    old = previous.get(entry.resource_id)
    action = {'add': 'delete', 'update': 'update', 'delete': 'add'}.get(entry.action)
    if action is None:
        raise ValueError('Roll back the original publish, not a rollback.')
    text = read(target)
    r = _write(target, action, entry.resource_id, old)
    ok = r.status_code in (200, 201, 204)
    steps.append(
        Step(name='restore', ok=ok, message=f'Admin API {r.request.method} {r.status_code}')
    )
    undo = LogEntry(
        id=new_id(),
        time=dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        target=target.id,
        action='rollback',
        resource_id=entry.resource_id,
        providers=ops.provider_refs(old),
        status='written' if ok else 'failed',
        config_hash_before=ce.etag(text),
        rollback_of=entry.id,
        message=None if ok else r.text[:500],
    )
    if not ok:
        ops.log.append(undo)
        return {'ok': False, 'steps': [s.model_dump() for s in steps], 'entry': undo.model_dump()}
    undo = ops.finish(target, undo, 'update' if old is not None else 'delete', old, steps)
    return {
        'ok': undo.status == 'published',
        'steps': [s.model_dump() for s in steps],
        'entry': undo.model_dump(),
    }
