"""FastAPI application (PLAN.md §10)."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .catalog import CatalogError
from .fs import PathError
from .render import FormState, ProviderState, RenderResult
from .security import GuardMiddleware
from .service import ExportResult, ProbeSummary, Service, ValidateResult
from .settings import Settings, load_settings
from .targets import ConflictError, PublishPlan, PublishRequest, TargetError
from .workspace import WorkspaceError
from .yamlio import SpliceError

FRONTEND_DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'


class TestSecrets(BaseModel):
    """Secret values for one test run. Kept in memory for that request only."""

    test_secrets: dict[str, str] = Field(default={}, description='ENV_NAME -> value')


class IntrospectRequest(TestSecrets):
    resource_id: str = 'resource'
    provider: ProviderState


class ProbeRequest(TestSecrets):
    form: FormState
    provider_index: int = 0


class RenderRequest(BaseModel):
    form: FormState
    target: str | None = None


class ValidateRequest(BaseModel):
    form: FormState
    target: str
    probes: list[ProbeSummary | None] = []
    env: dict[str, str] = Field(default={}, description='Known non-secret variable values')


class OpenAPICheckRequest(TestSecrets):
    form: FormState
    target: str


class ExportRequest(BaseModel):
    form: FormState
    target: str
    probes: list[ProbeSummary | None] = []


class VerifyRequest(BaseModel):
    resource_id: str


class SampleRequest(TestSecrets):
    source: dict[str, Any] = Field(description="A spec's source block (url, query, headers, auth)")


class SqlSampleRequest(TestSecrets):
    source: dict[str, Any] = Field(
        description="A sql-features spec's source block (dialect, connection or dsn, or path)"
    )
    query: str = Field(description='One read-only SELECT')


class ObservationSampleRequest(TestSecrets):
    observations: dict[str, Any] = Field(
        description="A timeseries-edr spec's observations block (from: http or sql)"
    )
    location_id: str
    parameter: str = Field(description="The source's name for the parameter ({parameter})")
    window: str = Field(default='P1D', description='How far back from now, as a duration')


class SpecRequest(TestSecrets):
    spec: str | dict[str, Any] = Field(
        description='The spec, as YAML text or an object, with ${VAR} placeholders for secrets'
    )


class SpecCheckRequest(BaseModel):
    spec: str | dict[str, Any]


class SuggestRequest(BaseModel):
    json_body: Any = Field(alias='json', description='A sampled response body')
    headers: dict[str, str] = {}
    url: str = ''
    records: str | None = Field(default=None, description='JSONPath of the record array')


class HarnessRequest(SpecRequest):
    levels: list[Literal['L1', 'L2', 'L3']] = ['L1', 'L2', 'L3']


class SaveRequest(BaseModel):
    spec: str | dict[str, Any]
    version: str | None = None
    changes: str = ''
    harness: dict[str, Any] | None = Field(default=None, description='The checklist to keep')


class ImportRequest(BaseModel):
    yaml: str = Field(description='A config, a resources mapping or one resource')


class JobCreated(BaseModel):
    job_id: str


class JobInfo(BaseModel):
    id: str
    kind: str
    status: str
    created: float
    finished: float | None
    result: Any = None
    error: str | None = None


def create_app(
    settings: Settings | None = None, service: Service | None = None, check_sandbox: bool = True
) -> FastAPI:
    settings = settings or (service.settings if service else load_settings())
    svc = service or Service(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if check_sandbox:
            threading.Thread(target=svc.check_sandbox, daemon=True).start()
        yield
        svc.jobs.shutdown()

    app = FastAPI(
        title='pygeoapi Provider Builder',
        version=__version__,
        lifespan=lifespan,
        openapi_url='/api/openapi.json',
        docs_url='/api/docs',
        redoc_url=None,
    )
    app.state.service = svc
    origins = settings.allowed_origins
    app.add_middleware(
        GuardMiddleware, allowed_hosts=settings.allowed_hosts, allowed_origins=origins
    )

    @app.exception_handler(CatalogError)
    @app.exception_handler(PathError)
    @app.exception_handler(SpliceError)
    async def _bad_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            {'type': 'about:blank', 'title': 'Bad request', 'status': 400, 'detail': str(exc)},
            status_code=400,
            media_type='application/problem+json',
        )

    @app.exception_handler(TargetError)
    async def _target_error(request: Request, exc: TargetError) -> JSONResponse:
        body: dict[str, Any] = {
            'type': 'about:blank',
            'title': 'Conflict' if isinstance(exc, ConflictError) else 'Bad request',
            'status': exc.status,
            'detail': str(exc),
        }
        if isinstance(exc, ConflictError):
            body.update(etag=exc.etag, diff=exc.diff)
        return JSONResponse(body, status_code=exc.status, media_type='application/problem+json')

    def _target(target_id: str) -> None:
        try:
            settings.target(target_id)
        except KeyError:
            raise HTTPException(404, f'Unknown target {target_id!r}') from None

    # ------------------------------------------------------------------ meta

    @app.get('/api/health', tags=['meta'])
    def health() -> dict[str, Any]:
        return svc.health()

    @app.get('/api/settings', tags=['meta'])
    def public_settings() -> dict[str, Any]:
        return {
            'data_roots': [
                {'name': r.name, 'server_path': r.server_path} for r in settings.data_roots
            ],
            'targets': [
                {
                    'id': t.id,
                    'title': t.title,
                    'kind': t.kind,
                    'server_url': t.server_url,
                    'base_config': t.base_config.name,
                }
                for t in settings.targets
            ],
            'geoconnex': settings.geoconnex,
            'runner': svc.runner.name,
        }

    # --------------------------------------------------------------- catalog

    @app.get('/api/catalog', tags=['catalog'])
    def catalog() -> list[dict[str, Any]]:
        return svc.cards()

    @app.get('/api/catalog/{descriptor_id:path}', tags=['catalog'])
    def catalog_detail(descriptor_id: str) -> dict[str, Any]:
        return svc.detail(descriptor_id)

    @app.get('/api/fs', tags=['mode-a'])
    def fs(path: str) -> dict[str, Any]:
        return svc.list_dir(path)

    # ---------------------------------------------------------------- mode A

    @app.post('/api/render', tags=['mode-a'])
    def render_endpoint(req: RenderRequest) -> RenderResult:
        if req.target:
            _target(req.target)
        return svc.render(req.form, req.target)

    @app.post('/api/validate', tags=['mode-a'])
    def validate(req: ValidateRequest) -> ValidateResult:
        _target(req.target)
        return svc.validate(req.form, req.target, req.probes, req.env)

    @app.post('/api/introspect', status_code=202, tags=['mode-a'])
    def introspect(req: IntrospectRequest) -> JobCreated:
        svc.catalog.get(req.provider.descriptor)
        job = svc.jobs.submit(
            'introspect', lambda: svc.introspect(req.provider, req.resource_id, req.test_secrets)
        )
        return JobCreated(job_id=job.id)

    @app.post('/api/probe', status_code=202, tags=['mode-a'])
    def probe(req: ProbeRequest) -> JobCreated:
        if not 0 <= req.provider_index < len(req.form.providers):
            raise HTTPException(400, 'provider_index out of range')
        job = svc.jobs.submit(
            'probe', lambda: svc.probe(req.form, req.provider_index, req.test_secrets)
        )
        return JobCreated(job_id=job.id)

    @app.post('/api/openapi-check', status_code=202, tags=['mode-a'])
    def openapi_check(req: OpenAPICheckRequest) -> JobCreated:
        _target(req.target)
        job = svc.jobs.submit(
            'openapi', lambda: svc.openapi_check(req.form, req.target, req.test_secrets)
        )
        return JobCreated(job_id=job.id)

    @app.post('/api/export', tags=['publish'])
    def export(req: ExportRequest) -> ExportResult:
        _target(req.target)
        return svc.export(req.form, req.target, req.probes)

    @app.post('/api/targets/{target_id}/verify', tags=['publish'])
    def verify(target_id: str, req: VerifyRequest) -> dict[str, Any]:
        _target(target_id)
        return svc.verify(target_id, req.resource_id)

    # ------------------------------------------------------- configs on targets (J2, J3)

    @app.get('/api/targets', tags=['targets'])
    def targets() -> list[dict[str, Any]]:
        return [
            {
                'id': t.id,
                'title': t.title,
                'kind': t.kind,
                'server_url': t.server_url,
                'config': t.base_config.name,
                'reload': t.reload,
                'writes_openapi': t.openapi is not None,
            }
            for t in settings.targets
        ]

    @app.get('/api/targets/{target_id}/config', tags=['targets'])
    def target_config(target_id: str) -> dict[str, Any]:
        return svc.targets.config(target_id)

    @app.get('/api/targets/{target_id}/resources', tags=['targets'])
    def target_resources(target_id: str) -> dict[str, Any]:
        return svc.targets.resources(target_id)

    @app.get('/api/targets/{target_id}/resources/{resource_id:path}', tags=['targets'])
    def target_resource(target_id: str, resource_id: str) -> dict[str, Any]:
        return svc.targets.resource(target_id, resource_id)

    @app.get('/api/targets/{target_id}/health/{resource_id:path}', tags=['targets'])
    def target_health(target_id: str, resource_id: str) -> dict[str, Any]:
        return svc.targets.health(target_id, resource_id)

    @app.post('/api/import', tags=['targets'])
    def import_yaml(req: ImportRequest) -> list[dict[str, Any]]:
        return svc.targets.import_yaml(req.yaml)

    # ---------------------------------------------------------------- publish

    @app.post('/api/publish/plan', tags=['publish'])
    def publish_plan(req: PublishRequest) -> PublishPlan:
        return svc.targets.plan(req)[0]

    @app.post('/api/publish', status_code=202, tags=['publish'])
    def publish(req: PublishRequest) -> JobCreated:
        svc.targets.target(req.target)
        job = svc.jobs.submit('publish', lambda: svc.targets.publish(req))
        return JobCreated(job_id=job.id)

    @app.post('/api/publish/{entry_id}/rollback', status_code=202, tags=['publish'])
    def rollback(entry_id: str) -> JobCreated:
        entry = svc.targets.log.get(entry_id)
        if entry is None:
            raise HTTPException(404, f'No publish {entry_id!r} in the log')
        job = svc.jobs.submit('rollback', lambda: svc.targets.rollback(entry_id))
        return JobCreated(job_id=job.id)

    @app.get('/api/publish/log', tags=['publish'])
    def publish_log(target: str | None = None, resource_id: str | None = None) -> list[Any]:
        entries = svc.targets.log.entries()
        return [
            e.model_dump()
            for e in reversed(entries)
            if (target is None or e.target == target)
            and (resource_id is None or e.resource_id == resource_id)
        ]

    # ------------------------------------------------------------------- jobs

    @app.exception_handler(WorkspaceError)
    async def _workspace_error(request: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(
            {
                'title': 'Workspace error',
                'status': 400,
                'detail': str(exc),
                'problems': [vars(p) for p in exc.problems],
            },
            status_code=400,
            media_type='application/problem+json',
        )

    @app.post('/api/samples', status_code=202, tags=['mode-b'])
    def samples(req: SampleRequest) -> JobCreated:
        job = svc.jobs.submit('sample', lambda: svc.sample(req.source, req.test_secrets))
        return JobCreated(job_id=job.id)

    @app.post('/api/samples/sql', status_code=202, tags=['mode-b'])
    def samples_sql(req: SqlSampleRequest) -> JobCreated:
        job = svc.jobs.submit(
            'sql-sample', lambda: svc.sample_sql(req.source, req.query, req.test_secrets)
        )
        return JobCreated(job_id=job.id)

    @app.post('/api/samples/observations', status_code=202, tags=['mode-b'])
    def samples_observations(req: ObservationSampleRequest) -> JobCreated:
        job = svc.jobs.submit(
            'observation-sample',
            lambda: svc.sample_observations(
                req.observations, req.location_id, req.parameter, req.window, req.test_secrets
            ),
        )
        return JobCreated(job_id=job.id)

    @app.post('/api/specs/suggest', tags=['mode-b'])
    def spec_suggest(req: SuggestRequest) -> dict[str, Any]:
        from pygeoapi_spec_providers.jsonpaths import PathError

        from .suggest import suggest

        try:
            return suggest(req.json_body, req.headers, req.url, req.records)
        except PathError as err:
            raise HTTPException(400, str(err)) from err

    @app.post('/api/specs/validate', tags=['mode-b'])
    def spec_validate(req: SpecCheckRequest) -> dict[str, Any]:
        return svc.spec_check(req.spec)

    @app.post('/api/specs/preview', status_code=202, tags=['mode-b'])
    def spec_preview(req: SpecRequest) -> JobCreated:
        job = svc.jobs.submit('spec-preview', lambda: svc.spec_preview(req.spec, req.test_secrets))
        return JobCreated(job_id=job.id)

    @app.post('/api/specs/harness', status_code=202, tags=['mode-b'])
    def spec_harness(req: HarnessRequest) -> JobCreated:
        job = svc.jobs.submit(
            'harness', lambda: svc.spec_harness(req.spec, req.test_secrets, list(req.levels))
        )
        return JobCreated(job_id=job.id)

    @app.get('/api/workspace', tags=['mode-b'])
    def workspace_list() -> list[dict[str, Any]]:
        return svc.workspace.providers()

    @app.get('/api/workspace/{provider_id}', tags=['mode-b'])
    def workspace_detail(provider_id: str) -> dict[str, Any]:
        try:
            return svc.workspace.detail(provider_id)
        except WorkspaceError as err:
            raise HTTPException(404, str(err)) from err

    @app.post('/api/workspace', tags=['mode-b'])
    def workspace_save(req: SaveRequest) -> dict[str, Any]:
        return svc.workspace_save(req.spec, req.version, req.changes, req.harness)

    @app.get('/api/jobs/{job_id}', tags=['jobs'])
    def job(job_id: str) -> JobInfo:
        j = svc.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, 'Unknown job')
        return JobInfo(**j.public())

    @app.get('/api/jobs/{job_id}/events', tags=['jobs'])
    async def job_events(job_id: str) -> StreamingResponse:
        if svc.jobs.get(job_id) is None:
            raise HTTPException(404, 'Unknown job')

        async def stream() -> AsyncIterator[str]:
            last = None
            while True:
                j = svc.jobs.get(job_id)
                if j is None:
                    return
                if j.status != last:
                    last = j.status
                    yield f'event: status\ndata: {json.dumps(j.public(), default=str)}\n\n'
                if j.status in ('done', 'failed'):
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type='text/event-stream')

    # --------------------------------------------------------------- frontend

    if FRONTEND_DIST.is_dir():

        @app.get('/{path:path}', include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith('api/'):
                raise HTTPException(404)
            candidate = (FRONTEND_DIST / path).resolve()
            if path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / 'index.html')

    return app
