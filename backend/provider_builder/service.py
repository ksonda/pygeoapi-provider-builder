"""Mode A operations, independent of HTTP (the API layer is ``app``)."""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import httpx
import pygeoapi
import yaml
from pydantic import BaseModel
from pygeoapi_spec_providers.spec import Problem

from . import egress, yamlio
from .catalog import Catalog, Descriptor, card, compile_descriptor
from .errors import explain
from .fs import PathError, list_dir, resolve_server_path
from .jobs import JobManager
from .render import (
    RAW_DESCRIPTOR,
    EnvVar,
    FormState,
    ProviderState,
    RenderResult,
    render,
    render_provider,
)
from .runner import Outcome, Runner, make_runner
from .settings import Settings
from .suggest import suggest
from .validation import (
    Finding,
    RuleContext,
    descriptor_findings,
    rule_findings,
    schema_findings,
)
from .workspace import (
    ProviderWorkspace,
    WorkspaceError,
    capabilities_for,
    check_spec_text,
    plugin_for,
    provider_type_for,
    spec_env,
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
        self.workspace = ProviderWorkspace(settings.workspace)
        self.workspace.refresh()  # descriptors are generated: keep them current
        self.catalog = Catalog(settings.catalogs, settings.workspace.host_path)
        self.runner = runner or make_runner(
            settings.sandbox, settings.data_roots, settings.workspace
        )
        self.jobs = JobManager()
        from .targets import TargetOps  # it imports this module

        self.targets = TargetOps(self)
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
                'Probes run in a local subprocess, not the Docker sandbox, so there is no '
                'network isolation. Start Docker and run make images.'
            )
        return {
            'status': 'ok',
            'pygeoapi': pygeoapi.__version__,
            'runner': self.runner.name,
            'image': self.settings.sandbox.resolved_image(),
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
        if p.descriptor == RAW_DESCRIPTOR:
            wanted |= set(re.findall(r'\$\{(\w+)', json.dumps(p.extra, default=str)))
        elif p.descriptor in self.catalog.descriptors:
            wanted |= {v['name'] for v in self.catalog.get(p.descriptor).raw.get('env', [])}
        return {k: v for k, v in test_secrets.items() if k in wanted}

    def _spec_doc(self, pdef: dict[str, Any]) -> dict[str, Any] | None:
        """The spec a created provider's ``data`` points at, as raw data (placeholders kept)."""
        data = pdef.get('data')
        if isinstance(data, dict):
            return data
        if not isinstance(data, str):
            return None
        ws = self.settings.workspace
        prefix = ws.server_path.rstrip('/') + '/'
        if not data.startswith(prefix):
            return None
        path = (ws.host_path / data[len(prefix) :]).resolve()
        if not path.is_relative_to(ws.host_path.resolve()) or not path.is_file():
            return None
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
        return doc if isinstance(doc, dict) else None

    def egress_for(
        self, pdef: dict[str, Any], d: Descriptor | None, secrets: dict[str, str]
    ) -> set[egress.HostPort]:
        if d is None:
            return set()
        rule = d.raw.get('egress')
        spec = self._spec_doc(pdef) if rule and rule.get('kind') == 'spec' else None
        return egress.for_provider(pdef, rule, secrets, spec)

    def egress_for_config(self, text: str, secrets: dict[str, str]) -> set[egress.HostPort]:
        """Every provider in a config: ``openapi generate`` loads them all (notes §6.2)."""
        config = yaml.safe_load(text) or {}
        found: set[egress.HostPort] = set()
        for resource in (config.get('resources') or {}).values():
            for pdef in (resource or {}).get('providers') or []:
                if isinstance(pdef, dict) and isinstance(pdef.get('name'), str):
                    found |= self.egress_for(pdef, self.catalog.by_plugin(pdef['name']), secrets)
        return found

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
        secrets = self._secrets(provider, test_secrets)
        out = self.runner.run(job, secrets, egress=self.egress_for(pdef, d, secrets))
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
        secrets = self._secrets(p, test_secrets)
        out = self.runner.run(job, secrets, egress=self.egress_for(pdef, d, secrets))
        return self._outcome(out, d, pdef)

    def openapi_check(
        self, form: FormState, target_id: str, test_secrets: dict[str, str]
    ) -> dict[str, Any]:
        merged = self.merged_text(form, target_id)
        secrets: dict[str, str] = {}
        for p in form.providers:
            secrets |= self._secrets(p, test_secrets)
        out = self.runner.run(
            {'kind': 'openapi', 'config_yaml': merged},
            secrets,
            egress=self.egress_for_config(merged, secrets),
        )
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
            # A restarted container keeps its old environment; only a new one rereads env files.
            steps.append(
                'Regenerate the OpenAPI document and restart pygeoapi. The official '
                'Docker image regenerates it whenever a container starts. With docker '
                'compose, recreate the container so it reads the new variables: '
                'docker compose up -d --no-deps --force-recreate pygeoapi.'
            )
        else:
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

    # ------------------------------------------------------------------- Mode B

    def reload_catalog(self) -> None:
        self.catalog = Catalog(self.settings.catalogs, self.settings.workspace.host_path)

    def sample(self, source: dict[str, Any], test_secrets: dict[str, str]) -> dict[str, Any]:
        """Fetch one response in the sandbox and suggest a mapping (PLAN.md §7.4 steps 1-2)."""
        from pygeoapi_spec_providers.spec import validate_source

        problems = validate_source(source)
        if problems:
            return {'ok': False, 'problems': [vars(p) for p in problems]}
        names = set(spec_env({'source': source}))
        secrets = {k: v for k, v in test_secrets.items() if k in names}
        out = self.runner.run(
            {'kind': 'sample', 'source_yaml': yamlio.dump(source)},
            secrets,
            egress=egress.urls([source.get('url')]),
        )
        res = self._mode_b_outcome(out)
        if out.ok and out.result and 'json' in out.result:
            url = str(source['url'])
            query = '&'.join(f'{k}={v}' for k, v in (source.get('query') or {}).items())
            res['suggestions'] = suggest(
                out.result['json'],
                out.result.get('headers', {}),
                f'{url}?{query}' if query else url,
            )
        return res

    def sample_sql(
        self, source: dict[str, Any], query: str, test_secrets: dict[str, str]
    ) -> dict[str, Any]:
        """Run a query's first rows in the sandbox and suggest a mapping (J4, SQL template)."""
        from pygeoapi_spec_providers.spec import validate_sql_source

        from .suggest import suggest_sql

        problems = validate_sql_source(source, query)
        if problems:
            return {'ok': False, 'problems': [vars(p) for p in problems]}
        try:
            self._check_spec_paths({'template': 'sql-features', 'source': source})
        except WorkspaceError as err:
            return {'ok': False, 'problems': [vars(p) for p in err.problems]}
        names = set(spec_env({'source': source}))
        secrets = {k: v for k, v in test_secrets.items() if k in names}
        out = self.runner.run(
            {'kind': 'sql_sample', 'source_yaml': yamlio.dump(source), 'query': query},
            secrets,
            egress=egress.sql_source(egress.fill(source, secrets)),
        )
        res = self._mode_b_outcome(out)
        if out.ok and out.result:
            res['suggestions'] = suggest_sql(out.result)
        return res

    @staticmethod
    def spec_text(spec: str | dict[str, Any]) -> str:
        """Spec YAML; an object is written like a config, with placeholders as plain values."""
        return spec if isinstance(spec, str) else yamlio.dump(spec, mapping_indent=2)

    def spec_check(self, spec: str | dict[str, Any]) -> dict[str, Any]:
        text = self.spec_text(spec)
        doc, problems = check_spec_text(text)
        result: dict[str, Any] = {
            'ok': doc is not None and not problems,
            'problems': [vars(p) for p in problems],
            'text': text,
        }
        if doc is not None:
            result.update(
                id=doc.get('id'),
                version=doc.get('version'),
                env=spec_env(doc),
                hosts=sorted(str(h) for h in egress.spec(doc)),
            )
        return result

    def _check_spec_paths(self, doc: dict[str, Any]) -> None:
        """A SQLite spec's file must be inside a data root: the sandbox mounts only those."""
        source = doc.get('source') or {}
        if doc.get('template') == 'sql-features' and source.get('dialect') == 'sqlite':
            path = source.get('path')
            if isinstance(path, str) and '${' not in path:
                try:
                    resolve_server_path(self.settings, path)
                except PathError as err:
                    raise WorkspaceError(
                        str(err), [Problem('/source/path', str(err), self._roots_hint())]
                    ) from err

    def _roots_hint(self) -> str:
        roots = ', '.join(r.server_path for r in self.settings.data_roots) or 'none'
        return f'Put the database file in a data root ({roots}).'

    def _spec_job(
        self, spec: str | dict[str, Any], test_secrets: dict[str, str]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], set[egress.HostPort]]:
        text = self.spec_text(spec)
        doc, problems = check_spec_text(text)
        if doc is None or problems:
            raise WorkspaceError('The spec has problems.', problems)
        self._check_spec_paths(doc)
        names = set(spec_env(doc))
        secrets = {k: v for k, v in test_secrets.items() if k in names}
        rid = str(doc['id'])
        provider = {
            'type': provider_type_for(doc),
            'name': plugin_for(doc),
            'data': '@files@/spec.yaml',
        }
        resource = {
            rid: {
                'type': 'collection',
                'title': doc['title'],
                'description': doc.get('description') or doc['title'],
                'keywords': [rid],
                'extents': {'spatial': {'bbox': [-180, -90, 180, 90]}},
                'providers': [provider],
            }
        }
        job = {'resource_yaml': yamlio.dump(resource), 'files': {'spec.yaml': text}}
        return doc, job, secrets, egress.spec(egress.fill(doc, secrets))

    def spec_preview(
        self, text: str | dict[str, Any], test_secrets: dict[str, str]
    ) -> dict[str, Any]:
        try:
            _doc, job, secrets, targets = self._spec_job(text, test_secrets)
        except WorkspaceError as err:
            return {'ok': False, 'problems': [vars(p) for p in err.problems]}
        out = self.runner.run({**job, 'kind': 'probe', 'limit': 10}, secrets, egress=targets)
        return self._mode_b_outcome(out)

    def spec_harness(
        self,
        text: str | dict[str, Any],
        test_secrets: dict[str, str],
        levels: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            doc, job, secrets, targets = self._spec_job(text, test_secrets)
        except WorkspaceError as err:
            return {'ok': False, 'problems': [vars(p) for p in err.problems]}
        capabilities = capabilities_for(doc)
        out = self.runner.run(
            {
                **job,
                'kind': 'harness',
                'levels': levels or ['L1', 'L2', 'L3'],
                'capabilities': capabilities,
            },
            secrets,
            egress=targets,
            timeout_s=max(self.settings.sandbox.timeout_s, 180),
        )
        return self._mode_b_outcome(out)

    def _mode_b_outcome(self, out: Outcome) -> dict[str, Any]:
        res: dict[str, Any] = {
            'ok': out.ok,
            'result': out.result,
            'runner': out.runner,
            'ms': out.ms,
            'logs': out.logs,
            'egress': out.extra.get('egress', []),
        }
        if not out.ok and out.error:
            e = explain(
                out.error, {'timeout': self.settings.sandbox.timeout_s} | out.extra, out.logs
            )
            res['error'] = out.error
            res['explanation'] = {'code': e.code, 'message': e.message, 'hint': e.hint}
        return res

    def workspace_save(
        self,
        spec: str | dict[str, Any],
        version: str | None,
        changes: str,
        harness: dict[str, Any] | None,
    ) -> dict[str, Any]:
        text = self.spec_text(spec)
        saved = self.workspace.save(text, version=version, changes=changes, harness=harness)
        self.reload_catalog()
        return saved
