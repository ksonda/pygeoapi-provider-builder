"""Mode A operations, independent of HTTP (the API layer is ``app``)."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pygeoapi
import yaml
from pydantic import BaseModel

from . import yamlio
from .catalog import Catalog, Descriptor, card, compile_descriptor
from .errors import explain
from .fs import PathError, list_dir, resolve_server_path
from .jobs import JobManager
from .render import EnvVar, FormState, ProviderState, RenderResult, render, render_provider
from .runner import Outcome, Runner, make_runner
from .settings import Settings
from .validation import (
    Finding,
    RuleContext,
    descriptor_findings,
    rule_findings,
    schema_findings,
)


class ProbeSummary(BaseModel):
    """What validation needs from a probe (per provider)."""

    fields: dict[str, Any] | None = None
    sample_stats: dict[str, Any] | None = None


class ValidateResult(BaseModel):
    findings: list[Finding]
    blocking: bool
    yaml: str
    merged_yaml: str | None
    env: list[EnvVar]


class ExportResult(BaseModel):
    fragment_yaml: str
    merged_yaml: str
    merged_filename: str
    env: list[EnvVar]
    instructions: list[str]
    validation: ValidateResult


class Service:
    def __init__(self, settings: Settings, runner: Runner | None = None):
        self.settings = settings
        self.catalog = Catalog(settings.catalogs)
        self.runner = runner or make_runner(settings.sandbox, settings.data_roots)
        self.jobs = JobManager()
        self.availability: dict[str, bool] | None = None
        self.sandbox_versions: dict[str, Any] | None = None
        self._startup_lock = threading.Lock()

    # ----------------------------------------------------------------- startup

    def check_sandbox(self) -> None:
        """Import-check descriptor requirements and read versions inside the sandbox."""
        with self._startup_lock:
            modules = sorted(
                {m for d in self.catalog.descriptors.values() for m in d.raw.get('requires', [])}
            )
            out = self.runner.run({'kind': 'availability', 'modules': modules}, timeout_s=60)
            if out.ok and out.result:
                self.availability = out.result.get('modules', {})
            ver = self.runner.run({'kind': 'version'}, timeout_s=60)
            if ver.ok and ver.result:
                self.sandbox_versions = ver.result

    def health(self) -> dict[str, Any]:
        warnings = list(self.catalog.errors)
        sandbox_pg = (self.sandbox_versions or {}).get('pygeoapi')
        if sandbox_pg and sandbox_pg != pygeoapi.__version__:
            warnings.append(
                f'The sandbox runs pygeoapi {sandbox_pg} but the backend pins '
                f'{pygeoapi.__version__}.'
            )
        if self.runner.name == 'subprocess':
            warnings.append(
                'Probes run in a local subprocess, not the Docker sandbox. '
                'Start Docker for results that match the pygeoapi image.'
            )
        return {
            'status': 'ok',
            'pygeoapi': pygeoapi.__version__,
            'runner': self.runner.name,
            'image': self.settings.sandbox.image,
            'sandbox': self.sandbox_versions,
            'warnings': warnings,
        }

    # ----------------------------------------------------------------- catalog

    def cards(self) -> list[dict[str, Any]]:
        return [self._card(d) for d in self.catalog.descriptors.values()]

    def _card(self, d: Descriptor) -> dict[str, Any]:
        c = card(d)
        requires = c['requires']
        if self.availability is None:
            c['availability'] = {'status': 'unknown', 'missing': []}
        else:
            missing = [m for m in requires if not self.availability.get(m, False)]
            c['availability'] = {'status': 'missing' if missing else 'ready', 'missing': missing}
        return c

    def detail(self, descriptor_id: str) -> dict[str, Any]:
        d = self.catalog.get(descriptor_id)
        return {
            **self._card(d),
            **compile_descriptor(d),
            'examples': d.raw.get('examples', []),
            'probe': d.raw.get('probe'),
            'introspect': d.raw.get('introspect'),
        }

    def list_dir(self, server_path: str) -> dict[str, Any]:
        return list_dir(self.settings, server_path)

    # ------------------------------------------------------------- sandbox work

    def _secrets(self, p: ProviderState, test_secrets: dict[str, str]) -> dict[str, str]:
        wanted = {ref.env for ref in p.secrets.values()}
        return {k: v for k, v in test_secrets.items() if k in wanted}

    def _check_paths(self, pdef: dict[str, Any], d: Descriptor) -> None:
        for f in d.all_fields():
            if f['type'] != 'path':
                continue
            node: Any = pdef
            for part in f['key'].split('.'):
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, str) and node:
                resolve_server_path(self.settings, node)

    def _context(self, d: Descriptor, pdef: dict[str, Any]) -> dict[str, Any]:
        data = pdef.get('data')
        ctx: dict[str, Any] = {
            'descriptor': d.id,
            'table': pdef.get('table'),
            'timeout': self.settings.sandbox.timeout_s,
        }
        if isinstance(data, dict):
            ctx.update(host=data.get('host'), port=data.get('port', 5432))
        elif isinstance(data, str):
            ctx['path'] = data
        return ctx

    def _outcome(self, out: Outcome, d: Descriptor, pdef: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = {
            'ok': out.ok,
            'result': out.result,
            'runner': out.runner,
            'ms': out.ms,
            'logs': out.logs,
        }
        if not out.ok and out.error:
            ctx = self._context(d, pdef) | out.extra
            e = explain(out.error, ctx, out.logs)
            res['error'] = out.error
            res['explanation'] = {'code': e.code, 'message': e.message, 'hint': e.hint}
        return res

    def introspect(
        self, provider: ProviderState, resource_id: str, test_secrets: dict[str, str]
    ) -> dict[str, Any]:
        d = self.catalog.get(provider.descriptor)
        spec = d.raw.get('introspect')
        if not spec:
            return {
                'ok': False,
                'explanation': {
                    'code': 'no-introspection',
                    'message': 'Nothing to look up.',
                    'hint': None,
                },
            }
        pdef = render_provider(provider, d, resource_id or 'resource', {})
        try:
            self._check_paths(pdef, d)
        except PathError as err:
            return {
                'ok': False,
                'explanation': {'code': 'path', 'message': str(err), 'hint': None},
            }
        job = {
            'kind': 'introspect',
            'what': spec['what'],
            'dialect': spec.get('dialect'),
            'values_yaml': yamlio.dump(pdef),
        }
        out = self.runner.run(job, self._secrets(provider, test_secrets))
        return self._outcome(out, d, pdef)

    def probe(self, form: FormState, index: int, test_secrets: dict[str, str]) -> dict[str, Any]:
        p = form.providers[index]
        d = self.catalog.get(p.descriptor)
        rendered = render(form, self.catalog)
        pdef = rendered.resource['providers'][index]
        try:
            self._check_paths(pdef, d)
        except PathError as err:
            return {
                'ok': False,
                'explanation': {'code': 'path', 'message': str(err), 'hint': None},
            }
        probe_spec = d.raw.get('probe', {})
        job = {
            'kind': 'probe',
            'resource_yaml': rendered.yaml,
            'provider_index': index,
            'limit': 10,
            'copy_data': bool(probe_spec.get('copy_data')),
        }
        out = self.runner.run(job, self._secrets(p, test_secrets))
        return self._outcome(out, d, pdef)

    def openapi_check(
        self, form: FormState, target_id: str, test_secrets: dict[str, str]
    ) -> dict[str, Any]:
        merged = self.merged_text(form, target_id)
        secrets: dict[str, str] = {}
        for p in form.providers:
            secrets |= self._secrets(p, test_secrets)
        out = self.runner.run({'kind': 'openapi', 'config_yaml': merged}, secrets)
        res: dict[str, Any] = {
            'ok': out.ok,
            'result': out.result,
            'runner': out.runner,
            'ms': out.ms,
        }
        if not out.ok and out.error:
            d = self.catalog.get(form.providers[0].descriptor) if form.providers else None
            ctx = {'descriptor': d.id if d else None, 'timeout': self.settings.sandbox.timeout_s}
            e = explain(out.error, ctx, out.logs)
            res['error'] = out.error
            res['explanation'] = {'code': e.code, 'message': e.message, 'hint': e.hint}
        return res

    # ------------------------------------------------------ render and validate

    def render(self, form: FormState, target_id: str | None = None) -> RenderResult:
        indent = 4
        if target_id:
            base = self.settings.target(target_id).base_config.read_text(encoding='utf-8')
            indent = yamlio.detect_mapping_indent(base)
        return render(form, self.catalog, mapping_indent=indent)

    def merged_text(self, form: FormState, target_id: str) -> str:
        target = self.settings.target(target_id)
        base = target.base_config.read_text(encoding='utf-8')
        rendered = self.render(form, target_id)
        return yamlio.append_resource(base, form.resource_id, rendered.resource)

    def validate(
        self,
        form: FormState,
        target_id: str,
        probes: list[ProbeSummary | None] | None = None,
        env: dict[str, str] | None = None,
    ) -> ValidateResult:
        findings = descriptor_findings(form, self.catalog)
        rendered = self.render(form, target_id)
        merged: str | None = None
        try:
            merged = self.merged_text(form, target_id)
        except yamlio.SpliceError as err:
            findings.append(
                Finding(
                    layer='pygeoapi-schema',
                    severity='error',
                    code='config.splice',
                    message=str(err),
                    pointer='/resource_id',
                )
            )
        if merged is not None:
            config = yaml.safe_load(merged)
            findings += schema_findings(config, form.resource_id, env)
            probes = probes or []
            ctx = RuleContext(
                config=config,
                resource_id=form.resource_id,
                probe_fields=[(p.fields if p else None) or {} for p in probes] or None,
                probe_samples=[(p.sample_stats if p else None) or {} for p in probes] or None,
                geoconnex=self.settings.geoconnex,
            )
            findings += rule_findings(ctx)
        blocking = any(f.severity == 'error' for f in findings)
        return ValidateResult(
            findings=findings,
            blocking=blocking,
            yaml=rendered.yaml,
            merged_yaml=merged,
            env=rendered.env,
        )

    def export(
        self, form: FormState, target_id: str, probes: list[ProbeSummary | None] | None = None
    ) -> ExportResult:
        target = self.settings.target(target_id)
        validation = self.validate(form, target_id, probes)
        rendered = self.render(form, target_id)
        steps = [
            f'Save the merged config as {target.base_config.name} where your pygeoapi '
            'server reads its config (PYGEOAPI_CONFIG), or add the fragment under '
            'resources: in your own config.'
        ]
        if rendered.env:
            names = ', '.join(e.name for e in rendered.env)
            steps.append(f'Define these environment variables for the pygeoapi server: {names}.')
        steps.append(
            'Regenerate the OpenAPI document and restart pygeoapi. The official '
            'Docker image does both when the container restarts, for example: '
            'docker compose restart pygeoapi.'
        )
        if target.server_url:
            steps.append(
                f'Then open {target.server_url.rstrip("/")}/collections/{form.resource_id}/items'
            )
        return ExportResult(
            fragment_yaml=rendered.yaml,
            merged_yaml=validation.merged_yaml or '',
            merged_filename=target.base_config.name,
            env=rendered.env,
            instructions=steps,
            validation=validation,
        )

    def verify(self, target_id: str, resource_id: str) -> dict[str, Any]:
        """Check the live endpoints on a configured target (its URL is trusted config)."""
        target = self.settings.target(target_id)
        if not target.server_url:
            return {'ok': False, 'checks': [], 'message': 'This target has no server URL.'}
        base = target.server_url.rstrip('/')
        checks = []
        for url in (
            f'{base}/collections/{resource_id}?f=json',
            f'{base}/collections/{resource_id}/items?f=json&limit=1',
        ):
            try:
                r = httpx.get(url, timeout=10)
                detail = None
                ok = r.status_code == 200
                if ok and url.endswith('limit=1'):
                    n = len(r.json().get('features', []))
                    detail = f'{n} feature(s) returned'
                    ok = n > 0
                checks.append({'url': url, 'status': r.status_code, 'ok': ok, 'detail': detail})
            except httpx.HTTPError as err:
                checks.append(
                    {
                        'url': url,
                        'status': None,
                        'ok': False,
                        'detail': f"Couldn't reach the server: {err}",
                    }
                )
        return {'ok': all(c['ok'] for c in checks), 'checks': checks}
