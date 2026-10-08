// The J4 steps (PLAN.md §7.4): source, sample, map, paging and filters, check, save.
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, unwrap, waitForJob } from '../api/client'
import type {
  CheckStatus,
  GeometrySpec,
  HarnessData,
  ModeBResult,
  Pagination,
  PaginationStyle,
  ProbeData,
  PropertyType,
  SampleData,
  SavedProvider,
  SpecCheck,
  Suggestions,
} from '../api/types'
import { ExplainedError, SampleTable, Spinner, YamlView } from '../components/common'
import { JsonTree } from '../components/JsonTree'
import { MapView } from '../components/MapView'
import { buildSpec, secretsOf, sourceOf, useCreate, type AuthType, type KeyValue, type PropertyDraft } from './state'

export interface CreateStepProps {
  errors: Record<string, string>
  showErrors: boolean
}

const ENV_RE = /^[A-Z][A-Z0-9_]*$/

function FieldError({ id, errors, show }: { id: string; errors: Record<string, string>; show: boolean }) {
  if (!show || !errors[id]) return null
  return (
    <p className="field-error" id={`${id}-error`}>
      {errors[id]}
    </p>
  )
}

function described(id: string, errors: Record<string, string>, show: boolean, help?: string) {
  const ids = [help ? `${id}-help` : null, show && errors[id] ? `${id}-error` : null].filter(Boolean)
  return { 'aria-describedby': ids.length ? ids.join(' ') : undefined, 'aria-invalid': show && errors[id] ? true : undefined }
}

function Problems({ problems }: { problems?: { pointer: string; message: string; hint?: string | null }[] }) {
  if (!problems?.length) return null
  return (
    <div className="notice error" role="alert">
      <p>
        <strong>The spec has {problems.length === 1 ? 'a problem' : `${problems.length} problems`}:</strong>
      </p>
      <ul>
        {problems.map((p) => (
          <li key={`${p.pointer}:${p.message}`}>
            <code>{p.pointer || '/'}</code> {p.message}
            {p.hint && <span className="muted"> {p.hint}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function KeyValueList({ label, items, onChange, help }: { label: string; items: KeyValue[]; onChange: (items: KeyValue[]) => void; help?: string }) {
  const id = label.toLowerCase().replace(/[^a-z]+/g, '-')
  // Focus the new row's name once it has rendered (a timeout can fire before React commits it).
  const focusRow = useRef<number | null>(null)
  useEffect(() => {
    if (focusRow.current !== null && items.length > focusRow.current) {
      document.getElementById(`${id}-name-${focusRow.current}`)?.focus()
      focusRow.current = null
    }
  }, [items.length, id])
  return (
    <fieldset className="kv">
      <legend>{label}</legend>
      {help && <p className="muted small">{help}</p>}
      {items.map((kv, i) => (
        <div className="kv-row" key={i}>
          <label>
            Name
            <input id={`${id}-name-${i}`} value={kv.key} onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
          </label>
          <label>
            Value
            <input value={kv.value} onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
          </label>
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))} aria-label={`Remove ${label.toLowerCase()} ${kv.key || i + 1}`}>
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => {
          focusRow.current = items.length
          onChange([...items, { key: '', value: '' }])
        }}
      >
        Add a {label.toLowerCase().replace(/s$/, '')}
      </button>
    </fieldset>
  )
}

// ------------------------------------------------------------------- source

const AUTH_TYPES: [AuthType, string][] = [
  ['none', 'None'],
  ['header', 'API key in a header'],
  ['query', 'API key in the query string'],
  ['bearer', 'Bearer token'],
  ['basic', 'User name and password (basic)'],
]

export function SourceStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useCreate()
  const [busy, setBusy] = useState(false)
  const auth = state.auth
  const setAuth = (patch: Partial<typeof auth>) => dispatch({ type: 'source', patch: { auth: { ...auth, ...patch } } })

  const fetchSample = async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/samples', { body: { source: sourceOf(state) as unknown as Record<string, unknown>, test_secrets: secretsOf(state) } }))
      dispatch({ type: 'sample', result: await waitForJob<ModeBResult<SampleData>>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'sample', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
  }

  const sample = state.sample
  const status = sample?.ok ? sample.result?.status : undefined
  return (
    <>
      <p>Point the template at a JSON API. The sample request runs in the sandbox, through the same egress rules the provider will use.</p>
      <fieldset>
        <legend>Template</legend>
        <label className="radio">
          <input type="radio" name="template" checked readOnly /> HTTP JSON API → OGC API - Features
        </label>
        <p className="muted small">SQL and time-series templates arrive in Phase 4.</p>
      </fieldset>
      <label htmlFor="url">
        API URL <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="url-help">
        The address that returns the records, without query parameters. https unless the server allowlists the host.
      </p>
      <input id="url" type="url" required value={state.url} placeholder="https://api.example.gov/v2/wells" onChange={(e) => dispatch({ type: 'source', patch: { url: e.target.value } })} {...described('url', errors, showErrors, 'help')} />
      <FieldError id="url" errors={errors} show={showErrors} />
      <KeyValueList label="Query parameters" items={state.query} onChange={(query) => dispatch({ type: 'source', patch: { query } })} help="Fixed parameters sent with every request, such as format=json. Paging parameters are set later." />
      <KeyValueList label="Headers" items={state.headers} onChange={(headers) => dispatch({ type: 'source', patch: { headers } })} help="Fixed headers. Never put credentials here: use Authentication." />
      <fieldset>
        <legend>Authentication</legend>
        <label htmlFor="auth-type">How the API checks who you are</label>
        <select id="auth-type" value={auth.type} onChange={(e) => setAuth({ type: e.target.value as AuthType })}>
          {AUTH_TYPES.map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </select>
        {(auth.type === 'header' || auth.type === 'query') && (
          <>
            <label htmlFor="auth-name">{auth.type === 'header' ? 'Header name' : 'Parameter name'}</label>
            <input id="auth-name" value={auth.name} onChange={(e) => setAuth({ name: e.target.value })} />
          </>
        )}
        {auth.type === 'basic' && (
          <>
            <label htmlFor="auth-user">User name</label>
            <input id="auth-user" value={auth.username} onChange={(e) => setAuth({ username: e.target.value })} />
          </>
        )}
        {auth.type !== 'none' && (
          <>
            <label htmlFor="auth-env">Environment variable for the secret</label>
            <p className="muted small" id="auth-env-help">
              The spec holds <code>${'{'}
              {auth.env || 'NAME'}
              {'}'}</code>, never the secret. The pygeoapi server must define this variable.
            </p>
            <input id="auth-env" value={auth.env} onChange={(e) => setAuth({ env: e.target.value.toUpperCase() })} {...described('auth-env', errors, showErrors, 'help')} />
            <FieldError id="auth-env" errors={errors} show={showErrors} />
            <label htmlFor="auth-secret">{auth.type === 'basic' ? 'Password' : 'Key or token'} to test with</label>
            <p className="muted small" id="auth-secret-help">
              Kept in this page's memory only, and sent to the sandbox by variable name.
            </p>
            <input id="auth-secret" type="password" autoComplete="off" value={state.testSecret} onChange={(e) => dispatch({ type: 'testSecret', value: e.target.value })} aria-describedby="auth-secret-help" />
          </>
        )}
      </fieldset>
      <p>
        <button type="button" className="primary" onClick={() => void fetchSample()} disabled={busy || !state.url}>
          Fetch a sample
        </button>
      </p>
      {busy && <Spinner label="Fetching a sample in the sandbox…" />}
      <FieldError id="sample" errors={errors} show={showErrors} />
      {sample && !busy && !sample.ok && (sample.problems?.length ? <Problems problems={sample.problems} /> : <ExplainedError explanation={sample.explanation} error={sample.error} logs={sample.logs} />)}
      {sample?.ok && sample.result && !busy && (
        <div className={`notice ${status && status < 300 ? 'ok' : 'error'}`} role="status">
          <p>
            <strong>HTTP {status}</strong> from {sample.result.url} · {(sample.result.bytes / 1024).toFixed(1)} KB in {sample.result.ms} ms
            {sample.egress?.length ? ` · through the egress gateway to ${sample.egress.join(', ')}` : ''}
          </p>
          {status && status >= 400 && <p>The API refused the request. Check the URL and the authentication, then fetch again.</p>}
          {status && status < 300 && !sample.suggestions?.records && <p>The response has no list of records. Check the URL.</p>}
          {status && status < 300 && sample.suggestions?.records && (
            <p>
              Found {sample.suggestions.record_count} record(s) at <code>{sample.suggestions.records}</code>.
            </p>
          )}
        </div>
      )}
    </>
  )
}

// ------------------------------------------------------------------- sample

export function SampleStep() {
  const { state, dispatch } = useCreate()
  const [error, setError] = useState<string | null>(null)
  const s = state.suggestions
  const result = state.sample?.result

  const pick = async (records: string) => {
    setError(null)
    try {
      const out = unwrap(
        await api.POST('/api/specs/suggest', {
          body: { json: result?.json ?? null, headers: result?.headers ?? {}, url: state.url, records },
        }),
      ) as unknown as Suggestions
      dispatch({ type: 'suggestions', suggestions: out })
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <>
      <p>
        These are the records the provider will publish. To choose another list, select it in the tree (Enter or Space with the keyboard).
      </p>
      {s && (
        <div className="notice" role="status">
          <p>
            Records: <code>{state.records}</code> · {s.record_count} in this sample
          </p>
          {[...s.notes, ...(s.pagination?.hints ?? [])].map((n) => (
            <p key={n} className="small">
              {n}
            </p>
          ))}
        </div>
      )}
      {error && <p className="notice error">{error}</p>}
      <div className="two-col">
        <div className="tree-wrap">
          <h3 id="tree-heading">Response</h3>
          <JsonTree data={result?.json ?? null} label="The sampled response" selected={state.records} onPick={(p) => void pick(p)} />
        </div>
        <div>
          <h3>Fields in each record</h3>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Fields in each record">
            <table className="compact">
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Type</th>
                  <th scope="col">Unique</th>
                  <th scope="col">Examples</th>
                </tr>
              </thead>
              <tbody>
                {(s?.columns ?? []).map((c) => (
                  <tr key={c.path}>
                    <td className="mono">{c.name}</td>
                    <td>
                      {c.type}
                      {c.format ? ` (${c.format})` : ''}
                    </td>
                    <td>{c.unique ? 'yes' : ''}</td>
                    <td className="muted">{c.examples.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ').slice(0, 80)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  )
}

// ------------------------------------------------------------------- map

const TYPES: PropertyType[] = ['string', 'integer', 'number', 'boolean', 'datetime']
const TIME_FORMATS = ['iso8601', 'date', 'epoch_s', 'epoch_ms']

function refOf(path: string): string {
  return /^\$\.[A-Za-z_][A-Za-z0-9_]*$/.test(path) ? path.slice(2) : path
}

export function MapStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useCreate()
  const [busy, setBusy] = useState(false)
  const columns = state.suggestions?.columns ?? []
  const numeric = columns.filter((c) => c.type === 'number' || c.type === 'integer')
  const geometry = state.geometry
  const setGeometry = (g: GeometrySpec) => dispatch({ type: 'mapping', patch: { geometry: g } })
  const setProp = (i: number, patch: Partial<PropertyDraft>) =>
    dispatch({ type: 'mapping', patch: { properties: state.properties.map((p, j) => (j === i ? { ...p, ...patch } : p)) } })
  const included = state.properties.filter((p) => p.include)

  const preview = useCallback(async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/specs/preview', { body: { spec: buildSpec(state) as unknown as Record<string, unknown>, test_secrets: secretsOf(state) } }))
      dispatch({ type: 'preview', result: await waitForJob<ModeBResult<ProbeData>>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'preview', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
  }, [state, dispatch])

  const r = state.preview?.ok ? state.preview.result : null
  return (
    <>
      <p>Choose the id, the geometry and the properties. Preview runs the spec in the sandbox, the way pygeoapi will.</p>
      <label htmlFor="item-id">
        Item id <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="item-id-help">
        Must be unique: it becomes /items/&#123;id&#125;.
      </p>
      <select id="item-id" value={state.itemId} onChange={(e) => dispatch({ type: 'mapping', patch: { itemId: e.target.value } })} {...described('item-id', errors, showErrors, 'help')}>
        <option value="">Choose…</option>
        {columns
          .filter((c) => c.type !== 'object')
          .map((c) => (
            <option key={c.path} value={refOf(c.path)}>
              {c.name}
              {c.unique ? ' (unique in the sample)' : ''}
            </option>
          ))}
      </select>
      <FieldError id="item-id" errors={errors} show={showErrors} />
      <fieldset>
        <legend>Geometry</legend>
        <label htmlFor="geometry-type">Where each item's location comes from</label>
        <select
          id="geometry-type"
          value={geometry.type}
          onChange={(e) => {
            const type = e.target.value as GeometrySpec['type']
            if (type === 'none') setGeometry({ type })
            else if (type === 'point') setGeometry({ type, x: numeric[0] ? refOf(numeric[0].path) : '', y: numeric[1] ? refOf(numeric[1].path) : '' })
            else setGeometry({ type, field: columns[0] ? refOf(columns[0].path) : '' })
          }}
        >
          <option value="point">Longitude and latitude fields</option>
          <option value="geojson">A GeoJSON geometry field</option>
          <option value="wkt">A WKT text field</option>
          <option value="none">No geometry</option>
        </select>
        {geometry.type === 'point' && (
          <div className="inline-fields">
            {(['x', 'y'] as const).map((axis) => (
              <label key={axis}>
                {axis === 'x' ? 'Longitude (x)' : 'Latitude (y)'}
                <select value={geometry[axis]} onChange={(e) => setGeometry({ ...geometry, [axis]: e.target.value })}>
                  <option value="">Choose…</option>
                  {numeric.map((c) => (
                    <option key={c.path} value={refOf(c.path)}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        {(geometry.type === 'geojson' || geometry.type === 'wkt') && (
          <label>
            Field
            <select value={geometry.field} onChange={(e) => setGeometry({ ...geometry, field: e.target.value })}>
              {columns.map((c) => (
                <option key={c.path} value={refOf(c.path)}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </fieldset>
      <h3 id="props-heading">Properties</h3>
      <FieldError id="properties" errors={errors} show={showErrors} />
      <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="props-heading">
        <table className="compact props">
          <thead>
            <tr>
              <th scope="col">Publish</th>
              <th scope="col">Name</th>
              <th scope="col">Type</th>
              <th scope="col">From</th>
              <th scope="col">Time format</th>
            </tr>
          </thead>
          <tbody>
            {state.properties.map((p, i) => (
              <tr key={p.from ?? p.name}>
                <td>
                  <input type="checkbox" checked={p.include} aria-label={`Publish ${p.name}`} onChange={(e) => setProp(i, { include: e.target.checked })} />
                </td>
                <td>
                  <input value={p.name} aria-label={`Name of ${p.from ?? p.name}`} onChange={(e) => setProp(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '_') })} />
                </td>
                <td>
                  <select value={p.type} aria-label={`Type of ${p.name}`} onChange={(e) => setProp(i, { type: e.target.value as PropertyType, format: undefined })}>
                    {TYPES.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                </td>
                <td className="mono small">{p.from ?? p.name}</td>
                <td>
                  {p.type === 'datetime' ? (
                    <select value={p.format ?? 'iso8601'} aria-label={`Time format of ${p.name}`} onChange={(e) => setProp(i, { format: e.target.value === 'iso8601' ? undefined : e.target.value })}>
                      {TIME_FORMATS.map((f) => (
                        <option key={f}>{f}</option>
                      ))}
                    </select>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inline-fields">
        <label>
          Time field
          <select value={state.timeField} onChange={(e) => dispatch({ type: 'mapping', patch: { timeField: e.target.value } })}>
            <option value="">None</option>
            {included
              .filter((p) => p.type === 'datetime')
              .map((p) => (
                <option key={p.name}>{p.name}</option>
              ))}
          </select>
        </label>
        <label>
          Title field
          <select value={state.titleField} onChange={(e) => dispatch({ type: 'mapping', patch: { titleField: e.target.value } })}>
            <option value="">None</option>
            {included
              .filter((p) => p.type === 'string')
              .map((p) => (
                <option key={p.name}>{p.name}</option>
              ))}
          </select>
        </label>
      </div>
      <p>
        <button type="button" className="primary" onClick={() => void preview()} disabled={busy}>
          Preview
        </button>
      </p>
      <FieldError id="preview" errors={errors} show={showErrors} />
      {busy && <Spinner label="Running the spec in the sandbox…" />}
      {state.preview && !state.preview.ok && !busy && (state.preview.problems?.length ? <Problems problems={state.preview.problems} /> : <ExplainedError explanation={state.preview.explanation} error={state.preview.error} logs={state.preview.logs} />)}
      {r?.sample && !busy && (
        <>
          <p className="notice ok" role="status">
            pygeoapi loaded the spec and returned {r.sample.features.length} item(s){typeof r.sample.numberMatched === 'number' ? ` of ${r.sample.numberMatched}` : ''}.
          </p>
          <div className="two-col">
            <SampleTable sample={r.sample} />
            <MapView features={r.sample} bbox={r.extent?.bbox} label="Map of the preview items" />
          </div>
        </>
      )}
    </>
  )
}

// ------------------------------------------------------------------- paging and filters

const STYLES: [PaginationStyle, string][] = [
  ['none', 'One request returns everything'],
  ['offset', 'Offset and limit parameters'],
  ['page', 'Page numbers'],
  ['next_link', 'A link to the next page'],
  ['cursor', 'A cursor token'],
]

function TextField({ label, value, onChange, help }: { label: string; value: string | number | undefined; onChange: (v: string) => void; help?: string }) {
  return (
    <label>
      {label}
      {help && <span className="muted small"> {help}</span>}
      <input value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
    </label>
  )
}

export function PagingStep() {
  const { state, dispatch } = useCreate()
  const p = state.pagination
  const f = state.filters
  const setP = (patch: Partial<Pagination>) => dispatch({ type: 'paging', patch: { pagination: { ...p, ...patch } } })
  const setF = (key: keyof typeof f, value: unknown) => {
    const rest = Object.fromEntries(Object.entries(f).filter(([k]) => k !== key))
    dispatch({ type: 'paging', patch: { filters: value === undefined ? rest : { ...rest, [key]: value } } })
  }
  const props = state.properties.filter((x) => x.include)
  return (
    <>
      <h3>Paging</h3>
      <label htmlFor="paging-style">How the API splits records into pages</label>
      <select id="paging-style" value={p.style} onChange={(e) => setP({ style: e.target.value as PaginationStyle })}>
        {STYLES.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
      <div className="inline-fields">
        {p.style === 'offset' && (
          <>
            <TextField label="Offset parameter" value={p.offset_param} onChange={(v) => setP({ offset_param: v })} />
            <TextField label="Limit parameter" value={p.limit_param} onChange={(v) => setP({ limit_param: v })} />
          </>
        )}
        {p.style === 'page' && (
          <>
            <TextField label="Page parameter" value={p.page?.page_param} onChange={(v) => setP({ page: { ...p.page, page_param: v } })} />
            <TextField label="Page size parameter" value={p.page?.size_param} onChange={(v) => setP({ page: { page_param: p.page?.page_param ?? 'page', ...p.page, size_param: v || undefined } })} />
            <TextField label="First page" value={p.page?.page_start ?? 1} onChange={(v) => setP({ page: { page_param: p.page?.page_param ?? 'page', ...p.page, page_start: Number(v) || 0 } })} />
          </>
        )}
        {p.style === 'next_link' && <TextField label="Next link" help="(a JSONPath, or header:Link)" value={p.next_link?.next} onChange={(v) => setP({ next_link: { next: v } })} />}
        {p.style === 'cursor' && (
          <>
            <TextField label="Cursor parameter" value={p.cursor?.cursor_param} onChange={(v) => setP({ cursor: { cursor_path: p.cursor?.cursor_path ?? '$.next', cursor_param: v } })} />
            <TextField label="Cursor in the response" help="(JSONPath)" value={p.cursor?.cursor_path} onChange={(v) => setP({ cursor: { cursor_param: p.cursor?.cursor_param ?? 'cursor', cursor_path: v } })} />
          </>
        )}
        {(p.style === 'offset' || p.style === 'page') && <TextField label="Largest page the API allows" value={p.max_page_size ?? 100} onChange={(v) => setP({ max_page_size: Number(v) || 100 })} />}
        <TextField label="Total number of records" help="(JSONPath, optional)" value={p.total} onChange={(v) => setP({ total: v || undefined })} />
      </div>
      <h3>Filters</h3>
      <p>
        Filters the API can't apply run here instead, scanning up to <strong>{state.limits.max_records_scanned.toLocaleString()} records</strong> ({state.limits.max_pages} pages) per request.
        Tell the template which filters the API supports, so they're applied there.
      </p>
      <fieldset>
        <legend>Filters the API applies</legend>
        <label className="check">
          <input type="checkbox" checked={Boolean(f.bbox)} onChange={(e) => setF('bbox', e.target.checked ? { param: 'bbox', format: '{minx},{miny},{maxx},{maxy}' } : undefined)} /> Bounding box
        </label>
        {f.bbox && (
          <div className="inline-fields">
            <TextField label="Parameter" value={f.bbox.param} onChange={(v) => setF('bbox', { ...f.bbox, param: v })} />
            <TextField label="Format" value={f.bbox.format} onChange={(v) => setF('bbox', { ...f.bbox, format: v })} />
          </div>
        )}
        <label className="check">
          <input type="checkbox" checked={Boolean(f.datetime)} onChange={(e) => setF('datetime', e.target.checked ? { start_param: 'from', end_param: 'to', format: 'iso8601' } : undefined)} /> Date and time range
        </label>
        {f.datetime && (
          <div className="inline-fields">
            <TextField label="Start parameter" value={f.datetime.start_param} onChange={(v) => setF('datetime', { ...f.datetime, start_param: v })} />
            <TextField label="End parameter" value={f.datetime.end_param} onChange={(v) => setF('datetime', { ...f.datetime, end_param: v })} />
          </div>
        )}
        <label className="check">
          <input type="checkbox" checked={Boolean(f.q)} onChange={(e) => setF('q', e.target.checked ? { param: 'q' } : undefined)} /> Free-text search
        </label>
        {f.q && <TextField label="Search parameter" value={f.q.param} onChange={(v) => setF('q', { param: v })} />}
        <label className="check">
          <input type="checkbox" checked={Boolean(f.properties)} onChange={(e) => setF('properties', e.target.checked ? {} : undefined)} /> Some properties
        </label>
        {f.properties && (
          <div className="inline-fields">
            {props.map((x) => (
              <TextField
                key={x.name}
                label={`${x.name} parameter`}
                value={f.properties?.[x.name]}
                onChange={(v) => {
                  const rest = Object.fromEntries(Object.entries(f.properties ?? {}).filter(([k]) => k !== x.name))
                  setF('properties', v ? { ...rest, [x.name]: v } : rest)
                }}
              />
            ))}
          </div>
        )}
      </fieldset>
      <h3>Limits</h3>
      <div className="inline-fields">
        <TextField label="Most records scanned per request" value={state.limits.max_records_scanned} onChange={(v) => dispatch({ type: 'paging', patch: { limits: { ...state.limits, max_records_scanned: Number(v) || 1 } } })} />
        <TextField label="Most pages per request" value={state.limits.max_pages} onChange={(v) => dispatch({ type: 'paging', patch: { limits: { ...state.limits, max_pages: Number(v) || 1 } } })} />
      </div>
    </>
  )
}

// ------------------------------------------------------------------- check

const STATUS_TEXT: Record<CheckStatus, string> = { pass: 'Pass', fail: 'Fail', warn: 'Warning', skip: 'Skipped', unsupported: 'Not supported' }
const LEVELS: [string, string][] = [
  ['L1', 'The provider itself'],
  ['L2', "pygeoapi's API, in-process"],
  ['L3', 'A live pygeoapi server'],
]

export function CheckStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useCreate()
  const [busy, setBusy] = useState(false)
  const run = useCallback(async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/specs/harness', { body: { spec: buildSpec(state) as unknown as Record<string, unknown>, test_secrets: secretsOf(state), levels: ['L1', 'L2', 'L3'] } }))
      dispatch({ type: 'harness', result: await waitForJob<ModeBResult<HarnessData>>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'harness', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
  }, [state, dispatch])

  useEffect(() => {
    if (state.harness) return
    const t = setTimeout(() => void run(), 0)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const h = state.harness
  const data = h?.ok ? h.result : null
  return (
    <>
      <p>The contract checks run in the sandbox: the provider itself, pygeoapi's API in-process, then a live pygeoapi server.</p>
      {busy && <Spinner label="Running the contract checks…" />}
      <FieldError id="harness" errors={errors} show={showErrors} />
      {h && !h.ok && !busy && (h.problems?.length ? <Problems problems={h.problems} /> : <ExplainedError explanation={h.explanation} error={h.error} logs={h.logs} />)}
      {data && !busy && (
        <>
          <p className={`notice ${data.ok ? 'ok' : 'error'}`} role="status">
            <strong>{data.ok ? 'Every check passed.' : 'Some checks failed.'}</strong> {data.summary.pass} passed, {data.summary.fail} failed, {data.summary.warn} warning(s),{' '}
            {data.summary.skip + data.summary.unsupported} skipped or not supported · {(data.ms / 1000).toFixed(1)} s
          </p>
          {LEVELS.map(([level, title]) => {
            const checks = data.checks.filter((c) => c.level === level)
            if (!checks.length) return null
            return (
              <section key={level} aria-labelledby={`level-${level}`}>
                <h3 id={`level-${level}`}>
                  {level}: {title}
                </h3>
                <ul className="checklist">
                  {checks.map((c) => (
                    <li key={c.id} className={`check-${c.status}`}>
                      <span className="check-status">{STATUS_TEXT[c.status]}</span> <strong>{c.title}</strong>
                      <span className="muted"> {c.message}</span>
                      {c.hint && <p className="small">{c.hint}</p>}
                    </li>
                  ))}
                </ul>
              </section>
            )
          })}
        </>
      )}
      <p>
        <button type="button" onClick={() => void run()} disabled={busy}>
          Run the checks again
        </button>
      </p>
    </>
  )
}

// ------------------------------------------------------------------- save

export function SaveStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useCreate()
  const [check, setCheck] = useState<SpecCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const meta = state.meta
  const spec = buildSpec(state)
  const specKey = JSON.stringify(spec)

  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      api
        .POST('/api/specs/validate', { body: { spec: JSON.parse(specKey) as Record<string, unknown> } })
        .then((r) => live && setCheck(unwrap(r) as unknown as SpecCheck))
        .catch((e: unknown) => live && setError(String(e)))
    }, 300)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [specKey])

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.POST('/api/workspace', {
        body: { spec: spec as unknown as Record<string, unknown>, version: meta.version, changes: meta.changes, harness: (state.harness?.result as unknown as Record<string, unknown>) ?? null },
      })
      if (res.error) {
        const detail = res.error as { detail?: string; problems?: SpecCheck['problems'] }
        setError(detail.detail ?? 'Saving failed.')
        if (detail.problems?.length) setCheck((c) => (c ? { ...c, ok: false, problems: detail.problems ?? [] } : c))
      } else {
        dispatch({ type: 'saved', saved: res.data as unknown as SavedProvider })
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <label htmlFor="title">
        Title <span aria-hidden="true">*</span>
      </label>
      <input id="title" value={meta.title} onChange={(e) => dispatch({ type: 'meta', patch: { title: e.target.value } })} {...described('title', errors, showErrors)} />
      <FieldError id="title" errors={errors} show={showErrors} />
      <label htmlFor="provider-id">
        Provider id <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="provider-id-help">
        Lower-case letters, digits and hyphens. It names the workspace folder and the catalog entry.
      </p>
      <input id="provider-id" value={meta.id} onChange={(e) => dispatch({ type: 'meta', patch: { id: e.target.value }, idEdited: true })} {...described('provider-id', errors, showErrors, 'help')} />
      <FieldError id="provider-id" errors={errors} show={showErrors} />
      <label htmlFor="description">Description</label>
      <textarea id="description" rows={2} value={meta.description} onChange={(e) => dispatch({ type: 'meta', patch: { description: e.target.value } })} />
      <div className="inline-fields">
        <label>
          Version
          <input id="version" value={meta.version} onChange={(e) => dispatch({ type: 'meta', patch: { version: e.target.value } })} {...described('version', errors, showErrors)} />
        </label>
        <label>
          What changed
          <input value={meta.changes} placeholder="First version." onChange={(e) => dispatch({ type: 'meta', patch: { changes: e.target.value } })} />
        </label>
      </div>
      <FieldError id="version" errors={errors} show={showErrors} />
      {check && !check.ok && <Problems problems={check.problems} />}
      {check && (
        <>
          <h3>The spec</h3>
          <p className="muted small">
            Hosts it may reach: {check.hosts?.join(', ') || 'none'} · environment variables: {check.env?.length ? check.env.join(', ') : 'none'}
          </p>
          <YamlView text={check.text} label="The provider spec, as YAML" />
        </>
      )}
      {error && <p className="notice error">{error}</p>}
      {!state.saved && (
        <p>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !check?.ok}>
            Save version {meta.version}
          </button>
        </p>
      )}
      {state.saved && (
        <div className="notice ok" role="status">
          <p>
            <strong>
              Saved {state.saved.id} {state.saved.version}.
            </strong>{' '}
            The spec is at <code>{state.saved.data}</code> as the pygeoapi server sees it, and Mode A's catalog lists it.
          </p>
          <p>
            <a className="button primary" href={`#/configure/new?descriptor=${encodeURIComponent(state.saved.descriptor)}`}>
              Publish a collection with this provider →
            </a>
          </p>
        </div>
      )}
    </>
  )
}

export { ENV_RE }
