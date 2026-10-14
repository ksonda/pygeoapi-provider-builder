"""Configs on targets: J2 (load, list, edit, delete, import), J3 and publishing (PLAN.md §6.4-6.5).

A publish is planned first (the diff and validation a person reviews), then run
as a job: plan again with the ETag check, generate OpenAPI in the sandbox (the
gate), back up, write OpenAPI then the config atomically, reload, verify and log.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

import httpx
import yaml
from pydantic import BaseModel, Field

from . import configedit as ce
from .publish import (
    Backups,
    LogEntry,
    PublishLog,
    Step,
    atomic_write,
    new_id,
    reload,
    verify,
)
from .render import (
    RAW_DESCRIPTOR,
    EnvVar,
    FormState,
    LoadedResource,
    form_from_resource,
    render,
)
from .service import ProbeSummary
from .settings import Target
from .validation import Finding, RuleContext, descriptor_findings, rule_findings, schema_findings

if TYPE_CHECKING:
    from .service import Service


class TargetError(ValueError):
    status = 400


class ConflictError(TargetError):
    """The config changed on disk since it was loaded (HTTP 409)."""

    status = 409

    def __init__(self, message: str, etag: str, diff: str | None):
        super().__init__(message)
        self.etag = etag
        self.diff = diff


class PublishRequest(BaseModel):
    target: str
    action: Literal['add', 'update', 'delete']
    resource_id: str = Field(description='The new id (add) or the existing one')
    form: FormState | None = Field(default=None, description='add and update')
    etag: str | None = Field(default=None, description="The config's ETag when it was loaded")
    probes: list[ProbeSummary | None] = []
    test_secrets: dict[str, str] = Field(default={}, description='Kept in memory for this run')
    acknowledge: list[str] = Field(
        default=[], description='Admin API risks accepted, e.g. admin-secrets (ADR-0015)'
    )


class Note(BaseModel):
    path: str
    message: str


class PublishPlan(BaseModel):
    target: str
    kind: str
    action: Literal['add', 'update', 'delete']
    resource_id: str
    changed: bool
    etag: str
    etag_after: str
    diff: str
    notes: list[Note]
    findings: list[Finding]
    blocking: bool
    env: list[EnvVar]
    warnings: list[str] = []


class ResourceSummary(BaseModel):
    id: str
    type: str | None
    title: str
    providers: list[dict[str, Any]]
    editable: bool
    warnings: list[str] = []


class TargetOps:
    def __init__(self, svc: Service):
        self.svc = svc
        self.settings = svc.settings
        self.log = PublishLog(self.settings.state_dir / 'publish-log.jsonl')
        self._seen: OrderedDict[str, str] = OrderedDict()  # etag -> text, for 409 diffs
        self._lock = threading.Lock()  # one write at a time

    # ------------------------------------------------------------ reading

    def target(self, target_id: str) -> Target:
        try:
            return self.settings.target(target_id)
        except KeyError:
            raise TargetError(f'Unknown target {target_id!r}') from None

    def read(self, target: Target) -> tuple[str, str]:
        if target.kind == 'admin':
            from . import admin

            try:
                text = admin.read(target)
            except (httpx.HTTPError, ValueError) as err:
                raise TargetError(
                    f"Couldn't read the config through the Admin API: {err}"
                ) from err
        else:
            try:
                text = target.base_config.read_bytes().decode('utf-8')
            except FileNotFoundError:
                raise TargetError(f'The config {target.base_config} does not exist.') from None
        tag = ce.etag(text)
        self._seen[tag] = text
        self._seen.move_to_end(tag)
        while len(self._seen) > 32:
            self._seen.popitem(last=False)
        return text, tag

    def config(self, target_id: str) -> dict[str, Any]:
        target = self.target(target_id)
        text, tag = self.read(target)
        return {'name': target.base_config.name, 'text': text, 'etag': tag}

    def resources(self, target_id: str) -> dict[str, Any]:
        text, tag = self.read(self.target(target_id))
        doc = ce.load(text) or {}
        out = []
        for rid, res in ce.resources(doc).items():
            res = res if isinstance(res, dict) else {}
            loaded = form_from_resource(str(rid), res, self.svc.catalog)
            title = res.get('title')
            if isinstance(title, dict):
                title = title.get('en') or next(iter(title.values()), '')
            providers = []
            for p in res.get('providers') or []:
                p = p if isinstance(p, dict) else {}
                d = self.svc.catalog.by_plugin(str(p.get('name')))
                providers.append(
                    {
                        'type': p.get('type'),
                        'name': p.get('name'),
                        'descriptor': d.id if d else None,
                        'default': bool(p.get('default')),
                        'version': self.created_version(p),
                    }
                )
            out.append(
                ResourceSummary(
                    id=str(rid),
                    type=res.get('type'),
                    title=str(title or ''),
                    providers=providers,
                    editable=loaded.editable,
                    warnings=loaded.warnings,
                )
            )
        return {'etag': tag, 'resources': out}

    def resource(self, target_id: str, resource_id: str) -> dict[str, Any]:
        text, tag = self.read(self.target(target_id))
        res = ce.resources(ce.load(text) or {}).get(resource_id)
        if not isinstance(res, dict):
            raise TargetError(f'The config has no resource called {resource_id!r}.')
        loaded = form_from_resource(resource_id, res, self.svc.catalog)
        return {'etag': tag, 'yaml': self.resource_text(text, resource_id), **loaded.model_dump()}

    @staticmethod
    def resource_text(text: str, resource_id: str) -> str:
        """The resource's own lines from the file, comments included, dedented."""
        from . import yamlio

        try:
            p = ce._Patcher(text.lstrip('\ufeff').replace('\r\n', '\n'))
            line, col, end = p.key_span(p.rt['resources'], resource_id)
        except (ce.EditError, KeyError, TypeError):
            res = ce.resources(ce.load(text) or {}).get(resource_id)
            return yamlio.dump({resource_id: res})
        lines = p.lines[line:end]
        return '\n'.join(ln[col:] if ln[:col].strip() == '' else ln for ln in lines) + '\n'

    def created_version(self, pdef: dict[str, Any]) -> str | None:
        """A created provider's version, from its versioned spec path (ADR-0013)."""
        data = pdef.get('data')
        prefix = self.settings.workspace.server_path.rstrip('/') + '/'
        if isinstance(data, str) and data.startswith(prefix):
            parts = data[len(prefix) :].split('/')
            if len(parts) == 3 and parts[2] == 'spec.yaml':
                return f'{parts[0]}@{parts[1]}'
        return None

    # ------------------------------------------------------------ import (J2)

    def import_yaml(self, text: str) -> list[dict[str, Any]]:
        """A pasted snippet -> form states: a whole config, ``id: {...}`` or one resource."""
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as err:
            raise TargetError(f'The snippet is not valid YAML: {err}') from err
        if not isinstance(doc, dict):
            raise TargetError('Paste a resource: a mapping with providers.')
        common: list[str] = []
        if any(not isinstance(k, str) for k in _all_keys(doc)):
            common.append(
                'Some keys are not strings (e.g. yes, on, 1). PyYAML, as pygeoapi uses it, '
                'reads them as booleans or numbers; quote them.'
            )
        if re.search(r"""['"]\$\{\w+(:-[^}]*)?\}""", text):
            common.append(
                'A ${VAR} placeholder is quoted. pygeoapi substitutes it anyway, but the '
                'builder writes placeholders unquoted (docs/pygeoapi-notes.md §2).'
            )
        if 'resources' in doc and isinstance(doc['resources'], dict):
            found = doc['resources']
        elif 'providers' in doc:
            found = {'imported': doc}
        elif all(isinstance(v, dict) and 'providers' in v for v in doc.values()):
            found = doc
        else:
            raise TargetError('No resource with providers was found in the snippet.')
        out = []
        for rid, res in found.items():
            loaded: LoadedResource = form_from_resource(str(rid), res, self.svc.catalog)
            out.append(
                {
                    'resource_id': str(rid),
                    **loaded.model_dump(),
                    'warnings': common + loaded.warnings,
                }
            )
        return out

    # ------------------------------------------------------------ planning

    def plan(self, req: PublishRequest) -> tuple[PublishPlan, str, str, dict[str, Any] | None]:
        """The plan, plus the old and new text and the new resource (None for a delete)."""
        target = self.target(req.target)
        if target.kind == 'export':
            raise TargetError('This target is export-only; use Export instead.')
        text, tag = self.read(target)
        if req.etag and req.etag != tag:
            before = self._seen.get(req.etag)
            raise ConflictError(
                'The config changed on disk since you loaded it. Reload it and redo the change.',
                tag,
                ce.unified_diff(before, text, target.base_config.name) if before else None,
            )
        doc = ce.load(text) or {}
        existing = ce.resources(doc)
        rid = req.resource_id
        warnings: list[str] = []
        if target.kind == 'admin':
            from .admin import WARNING

            warnings.append(WARNING)
        new_res: dict[str, Any] | None = None
        env: list[EnvVar] = []
        changes: dict[str, dict[str, Any] | None]
        if req.action == 'delete':
            if rid not in existing:
                raise TargetError(f'The config has no resource called {rid!r}.')
            changes = {rid: None}
        else:
            if req.form is None:
                raise TargetError('A form is needed to add or update a resource.')
            indent = ce.detect_style(text).mapping_indent
            rendered = render(req.form, self.svc.catalog, mapping_indent=indent)
            env = rendered.env
            new_id = req.form.resource_id
            if req.action == 'add':
                if new_id in existing:
                    raise TargetError(f'The config already has a resource called {new_id!r}.')
                new_res = rendered.resource
                changes = {new_id: new_res}
            else:
                if rid not in existing:
                    raise TargetError(f'The config has no resource called {rid!r}.')
                orig = existing[rid]
                base = render(
                    form_from_resource(rid, orig, self.svc.catalog).form, self.svc.catalog
                ).resource
                new_res = ce.merge3(orig, base, rendered.resource)
                if new_id != rid:
                    if new_id in existing:
                        raise TargetError(f'The config already has a resource called {new_id!r}.')
                    changes = {rid: None, new_id: new_res}
                    warnings.append(
                        f'Renaming moves the resource to the end of the file and changes its URL '
                        f'from /collections/{rid} to /collections/{new_id}.'
                    )
                else:
                    changes = {rid: new_res}
            rid = new_id
        try:
            edit = ce.set_resources(text, changes)
        except ce.EditError as err:
            raise TargetError(str(err)) from err
        findings = self.findings(req, doc, ce.load(edit.text) or {}, rid)
        plan = PublishPlan(
            target=target.id,
            kind=target.kind,
            action=req.action,
            resource_id=rid,
            changed=edit.changed,
            etag=tag,
            etag_after=ce.etag(edit.text),
            diff=ce.unified_diff(text, edit.text, target.base_config.name),
            notes=[Note(path=n.path, message=n.message) for n in edit.notes],
            findings=findings,
            blocking=any(f.severity == 'error' for f in findings),
            env=env,
            warnings=warnings,
        )
        return plan, text, edit.text, new_res

    def findings(
        self, req: PublishRequest, old: dict[str, Any], new: dict[str, Any], rid: str
    ) -> list[Finding]:
        findings: list[Finding] = []
        if req.form is not None and req.action != 'delete':
            form = req.form.model_copy(
                update={
                    'providers': [p for p in req.form.providers if p.descriptor != RAW_DESCRIPTOR]
                }
            )
            findings += descriptor_findings(form, self.svc.catalog)
        before = {(f.code, f.message) for f in schema_findings(old, '')}
        for f in schema_findings(new, rid if req.action != 'delete' else ''):
            if f.pointer is None and (f.code, f.message) in before:
                f = f.model_copy(
                    update={
                        'severity': 'warning',
                        'hint': 'This was already in the config before this change.',
                    }
                )
            findings.append(f)
        if req.action != 'delete':
            probes = req.probes or []
            ctx = RuleContext(
                config=new,
                resource_id=rid,
                probe_fields=[(p.fields if p else None) or {} for p in probes] or None,
                probe_samples=[(p.sample_stats if p else None) or {} for p in probes] or None,
                geoconnex=self.settings.geoconnex,
            )
            findings += rule_findings(ctx)
        return findings

    # ------------------------------------------------------------ publishing

    def publish(self, req: PublishRequest) -> dict[str, Any]:
        with self._lock:
            return self._publish(req)

    def _publish(self, req: PublishRequest) -> dict[str, Any]:
        target = self.target(req.target)
        steps: list[Step] = []

        def result(ok: bool, entry: LogEntry | None = None, **extra: Any) -> dict[str, Any]:
            return {
                'ok': ok,
                'steps': [s.model_dump() for s in steps],
                'entry': entry.model_dump() if entry else None,
                **extra,
            }

        try:
            plan, before, after, new_res = self.plan(req)
        except ConflictError as err:
            steps.append(Step(name='plan', ok=False, message=str(err), detail=err.diff))
            return result(False, conflict=True)
        except TargetError as err:
            steps.append(Step(name='plan', ok=False, message=str(err)))
            return result(False)
        if plan.blocking:
            steps.append(
                Step(
                    name='validate',
                    ok=False,
                    message='Validation found errors; nothing was written.',
                    detail=[f.model_dump() for f in plan.findings if f.severity == 'error'],
                )
            )
            return result(False, plan=plan.model_dump())
        steps.append(Step(name='validate', ok=True, message='No blocking findings.'))
        if not plan.changed:
            steps.append(Step(name='write', ok=True, message='Nothing changed; nothing written.'))
            return result(True, plan=plan.model_dump())
        if target.kind == 'admin':
            from .admin import publish_admin

            return publish_admin(self, target, req, plan, before, after, new_res, steps, result)

        # The gate: pygeoapi's own OpenAPI generation on the whole new config.
        secrets: dict[str, str] = {}
        for p in req.form.providers if req.form else []:
            secrets |= self.svc._secrets(p, req.test_secrets)
        out = self.svc.runner.run(
            {
                'kind': 'openapi',
                'config_yaml': after,
                'document': target.openapi is not None,
                'python_path': self.svc.python_path_for_config(after),
            },
            secrets,
            egress=self.svc.egress_for_config(after, secrets),
            timeout_s=max(self.settings.sandbox.timeout_s, 120),
        )
        if not out.ok:
            from .errors import explain

            e = explain(out.error or {}, {'timeout': self.settings.sandbox.timeout_s}, out.logs)
            steps.append(
                Step(
                    name='openapi',
                    ok=False,
                    message=f'pygeoapi could not generate OpenAPI for the new config: {e.message}',
                    detail={'error': out.error, 'hint': e.hint},
                )
            )
            return result(False, plan=plan.model_dump())
        steps.append(
            Step(
                name='openapi',
                ok=True,
                message=f'OpenAPI generated ({len((out.result or {}).get("paths", []))} paths).',
            )
        )

        backups = Backups(self.settings, target)
        openapi_text = (out.result or {}).get('document')
        try:
            with self._write_guard(target, plan.etag):
                backup = backups.save(target.base_config, before)
                openapi_backup = None
                if target.openapi is not None and target.openapi.exists():
                    openapi_backup = backups.save(
                        target.openapi, target.openapi.read_text(encoding='utf-8')
                    )
                if target.openapi is not None and openapi_text:
                    atomic_write(target.openapi, openapi_text)
                atomic_write(target.base_config, after)
        except ConflictError as err:
            steps.append(Step(name='write', ok=False, message=str(err), detail=err.diff))
            return result(False, conflict=True)
        steps.append(
            Step(name='write', ok=True, message=f'Wrote {target.base_config.name}.', detail=backup)
        )
        entry = LogEntry(
            id=new_id(),
            time=_now_iso(),
            target=target.id,
            action=req.action,
            resource_id=plan.resource_id if req.action != 'delete' else req.resource_id,
            providers=self.provider_refs(new_res),
            status='written',
            config_hash_before=plan.etag,
            config_hash_after=plan.etag_after,
            backup=backup,
            openapi_backup=openapi_backup,
        )
        entry = self.finish(target, entry, req.action, new_res, steps)
        return result(entry.status != 'verify-failed', entry, plan=plan.model_dump())

    def finish(
        self,
        target: Target,
        entry: LogEntry,
        action: str,
        new_res: dict[str, Any] | None,
        steps: list[Step],
    ) -> LogEntry:
        """Reload, verify and log."""
        step = reload(self.settings, target)
        steps.append(step)
        if not step.ok:
            entry.status = 'verify-failed'
            entry.message = step.message
        elif target.reload == 'none':
            entry.status = 'written'
            entry.message = step.message
        else:
            expect: Literal['present', 'absent'] = 'absent' if action == 'delete' else 'present'
            ptype = None
            if new_res and new_res.get('providers'):
                ptype = new_res['providers'][0].get('type')
            v = verify(target, entry.resource_id, expect, ptype)
            steps.append(v)
            entry.status = 'published' if v.ok else 'verify-failed'
            entry.message = v.message
        self.log.append(entry)
        return entry

    def usage(self, match: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
        r"""The collections, in each target's current config, whose provider ``match``\ es.

        ``match`` returns what it knows of the provider's version, or None for other
        providers. The publish log adds the history (ADR-0013).
        """
        found: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for target in self.settings.targets:
            try:
                text, _tag = self.read(target)
            except TargetError as err:
                errors.append({'target': target.id, 'error': str(err)})
                continue
            for rid, res in ce.resources(ce.load(text) or {}).items():
                if not isinstance(res, dict):
                    continue
                title = res.get('title')
                if isinstance(title, dict):
                    title = title.get('en') or next(iter(title.values()), '')
                for pdef in res.get('providers') or []:
                    hit = match(pdef) if isinstance(pdef, dict) else None
                    if hit is not None:
                        found.append(
                            {
                                'target': target.id,
                                'target_title': target.title,
                                'resource_id': str(rid),
                                'title': str(title or ''),
                                **hit,
                            }
                        )
        return {'collections': found, 'errors': errors}

    def provider_refs(self, res: dict[str, Any] | None) -> list[str]:
        out = []
        for p in (res or {}).get('providers') or []:
            if isinstance(p, dict):
                out.append(self.created_version(p) or str(p.get('name')))
        return out

    class _Guard:
        def __init__(self, ops: TargetOps, target: Target, tag: str):
            self.ops, self.target, self.tag = ops, target, tag

        def __enter__(self) -> None:
            text, now_tag = self.ops.read(self.target)
            if now_tag != self.tag:
                raise ConflictError(
                    'The config changed on disk during the publish; nothing was written.',
                    now_tag,
                    ce.unified_diff(self.ops._seen.get(self.tag, ''), text),
                )

        def __exit__(self, *exc: object) -> None:
            return None

    def _write_guard(self, target: Target, tag: str) -> TargetOps._Guard:
        """Re-check the ETag immediately before writing."""
        return TargetOps._Guard(self, target, tag)

    # ------------------------------------------------------------ rollback

    def rollback(self, entry_id: str) -> dict[str, Any]:
        with self._lock:
            return self._rollback(entry_id)

    def _rollback(self, entry_id: str) -> dict[str, Any]:
        steps: list[Step] = []
        entry = self.log.get(entry_id)
        if entry is None:
            raise TargetError(f'No publish {entry_id!r} in the log.')
        if entry.rolled_back_by:
            raise TargetError('That publish was already rolled back.')
        target = self.target(entry.target)
        if target.kind == 'admin':
            from .admin import rollback_admin

            try:
                return rollback_admin(self, target, entry, steps)
            except (ValueError, httpx.HTTPError) as err:
                raise TargetError(str(err)) from err
        if not entry.backup:
            raise TargetError('That publish has no backup to restore.')
        text, tag = self.read(target)
        if tag != entry.config_hash_after:
            raise ConflictError(
                'The config changed after that publish, so restoring its backup would undo '
                'later changes too. Roll back the later publishes first.',
                tag,
                None,
            )
        backups = Backups(self.settings, target)
        restored = backups.read(entry.backup)
        with self._write_guard(target, tag):
            backup = backups.save(target.base_config, text)
            if entry.openapi_backup and target.openapi is not None:
                atomic_write(target.openapi, backups.read(entry.openapi_backup))
            atomic_write(target.base_config, restored)
        steps.append(Step(name='restore', ok=True, message='Restored the backup.', detail=backup))
        old_res = ce.resources(ce.load(restored) or {}).get(entry.resource_id)
        undo = LogEntry(
            id=new_id(),
            time=_now_iso(),
            target=target.id,
            action='rollback',
            resource_id=entry.resource_id,
            providers=self.provider_refs(old_res),
            status='written',
            config_hash_before=tag,
            config_hash_after=ce.etag(restored),
            backup=backup,
            rollback_of=entry.id,
        )
        undo = self.finish(
            target, undo, 'update' if old_res is not None else 'delete', old_res, steps
        )
        return {
            'ok': undo.status != 'verify-failed',
            'steps': [s.model_dump() for s in steps],
            'entry': undo.model_dump(),
        }

    # ------------------------------------------------------------ health

    def health(self, target_id: str, resource_id: str) -> dict[str, Any]:
        target = self.target(target_id)
        text, _ = self.read(target)
        res = ce.resources(ce.load(text) or {}).get(resource_id) or {}
        ptype = None
        if isinstance(res, dict) and res.get('providers'):
            ptype = res['providers'][0].get('type')
        step = verify(target, resource_id, 'present', ptype, timeout_s=0)
        return {'ok': step.ok, 'checks': step.detail, 'message': step.message}


def _now_iso() -> str:
    from .publish import now

    return now().strftime('%Y-%m-%dT%H:%M:%SZ')


def _all_keys(node: Any) -> list[Any]:
    out: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(k)
            out.extend(_all_keys(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_all_keys(v))
    return out
