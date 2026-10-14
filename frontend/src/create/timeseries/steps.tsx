// The J4 steps for the timeseries-edr template: locations, parameters,
// observations, time and queries, check, save (PLAN.md §7.2, §7.4). Every sample
// runs in the sandbox through the spec engine's own clients and guard.
import { useCallback, useState } from 'react'

import { api, unwrap, waitForJob } from '../../api/client'
import type {
  EdrQueryType,
  HarnessData,
  ModeBResult,
  ObservationSampleResult,
  ProbeData,
  SampleData,
  SqlEncoding,
  SqlGeometry,
  SqlIdType,
  SqlSampleResult,
  Suggestions,
} from '../../api/types'
import { CodeEditor } from '../../components/CodeEditor'
import { SampleTable, Spinner } from '../../components/common'
import { JsonTree } from '../../components/JsonTree'
import { MapView } from '../../components/MapView'
import { FieldError, Failure, HarnessPanel, SavePanel, described, type CreateStepProps } from '../shared'
import { SqlConnectionFields, connectionSource } from '../sql/connection'
import { KeyValueList } from '../steps'
import {
  buildTsSpec,
  fromHttpSuggestions,
  locationSecrets,
  locationsSource,
  locationsSpec,
  observationsBlock,
  observationSecrets,
  secretsOf,
  useTs,
  type AuthKind,
  type HttpDraft,
  type ParameterDraft,
  type PropertyDraft,
} from './state'

const TIME_FORMATS = ['iso8601', 'date', 'epoch_s', 'epoch_ms']
const QUERY_TYPES: [EdrQueryType, string][] = [
  ['locations', 'locations: list the stations, and one station’s series'],
  ['position', 'position: the series at the station nearest a point'],
  ['area', 'area: the series of the stations inside a polygon'],
  ['cube', 'cube: the series of the stations inside a bounding box'],
]

function refOf(path: string): string {
  return /^\$\.[A-Za-z_][A-Za-z0-9_]*$/.test(path) ? path.slice(2) : path
}

// ------------------------------------------------------------------- HTTP source fields

function HttpSourceFields({
  value: d,
  onChange,
  prefix,
  urlLabel,
  urlHelp,
  queryHelp,
  errors,
  showErrors,
}: {
  value: HttpDraft
  onChange: (patch: Partial<HttpDraft>) => void
  prefix: string
  urlLabel: string
  urlHelp: string
  queryHelp: string
  errors: Record<string, string>
  showErrors: boolean
}) {
  const id = (name: string) => `${prefix}${name}`
  return (
    <>
      <label htmlFor={id('url')}>
        {urlLabel} <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id={`${id('url')}-help`}>
        {urlHelp}
      </p>
      <input id={id('url')} type="url" value={d.url} onChange={(e) => onChange({ url: e.target.value })} {...described(id('url'), errors, showErrors, 'help')} />
      <FieldError id={id('url')} errors={errors} show={showErrors} />
      <KeyValueList label={prefix === 'obs-' ? 'Request parameters' : 'Query parameters'} items={d.query} onChange={(query) => onChange({ query })} help={queryHelp} />
      <fieldset>
        <legend>Authentication</legend>
        <label htmlFor={id('auth-type')}>How the API checks who you are</label>
        <select id={id('auth-type')} value={d.authType} onChange={(e) => onChange({ authType: e.target.value as AuthKind })}>
          <option value="none">None</option>
          <option value="header">API key in a header</option>
          <option value="query">API key in the query string</option>
          <option value="bearer">Bearer token</option>
        </select>
        {(d.authType === 'header' || d.authType === 'query') && (
          <>
            <label htmlFor={id('auth-name')}>{d.authType === 'header' ? 'Header name' : 'Parameter name'}</label>
            <input id={id('auth-name')} value={d.authName} onChange={(e) => onChange({ authName: e.target.value })} />
          </>
        )}
        {d.authType !== 'none' && (
          <>
            <label htmlFor={id('auth-env')}>Environment variable for the key</label>
            <input id={id('auth-env')} value={d.authEnv} onChange={(e) => onChange({ authEnv: e.target.value.toUpperCase() })} />
            <label htmlFor={id('auth-secret')}>Key to test with</label>
            <input id={id('auth-secret')} type="password" autoComplete="off" value={d.testSecret} onChange={(e) => onChange({ testSecret: e.target.value })} />
          </>
        )}
      </fieldset>
    </>
  )
}

function useJob<T>() {
  const [busy, setBusy] = useState(false)
  const run = useCallback(async (start: () => Promise<{ job_id: string }>, done: (r: T) => void, fail: (message: string) => void) => {
    setBusy(true)
    try {
      const job = await start()
      done(await waitForJob<T>(job.job_id))
    } catch (e) {
      fail(String(e))
    } finally {
      setBusy(false)
    }
  }, [])
  return { busy, run }
}

const failed = (message: string) => ({ ok: false, explanation: { code: 'client', message } }) as const

// ------------------------------------------------------------------- locations

export function TsLocationsStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  const set = (patch: Parameters<typeof dispatch>[0] extends infer A ? (A extends { type: 'set'; patch: infer P } ? P : never) : never) => dispatch({ type: 'set', patch })
  const sample = useJob<ModeBResult<SampleData>>()
  const sqlSample = useJob<SqlSampleResult>()
  const preview = useJob<ModeBResult<ProbeData>>()

  const fetchHttp = () =>
    void sample.run(
      async () => unwrap(await api.POST('/api/samples', { body: { source: locationsSource(state) as unknown as Record<string, unknown>, test_secrets: locationSecrets(state) } })),
      (r) => set({ locHttpSample: r, ...(r.suggestions ? fromHttpSuggestions(r.suggestions as Suggestions) : {}) }),
      (m) => set({ locHttpSample: failed(m) }),
    )
  const runSql = () =>
    void sqlSample.run(
      async () => unwrap(await api.POST('/api/samples/sql', { body: { source: connectionSource(state.locSql) as unknown as Record<string, unknown>, query: state.locQuery, test_secrets: locationSecrets(state) } })),
      (r) =>
        set({
          locSqlSample: r,
          ...(r.suggestions
            ? {
                locId: r.suggestions.item_id?.from ?? '',
                locIdType: r.suggestions.item_id?.type ?? 'string',
                locSqlGeometry: r.suggestions.geometry,
                locName: r.suggestions.title_field ?? '',
                locProperties: r.suggestions.properties.map((p) => ({ ...p, include: true })),
                locPreview: null,
              }
            : {}),
        }),
      (m) => set({ locSqlSample: failed(m) }),
    )
  const runPreview = () =>
    void preview.run(
      async () => unwrap(await api.POST('/api/specs/preview', { body: { spec: locationsSpec(state), test_secrets: locationSecrets(state) } })),
      (r) => {
        const first = r.ok ? r.result?.sample?.features?.[0]?.id : undefined
        set({ locPreview: r, ...(first !== undefined && !state.obsLocation ? { obsLocation: String(first) } : {}) })
      },
      (m) => set({ locPreview: failed(m) }),
    )

  const http = state.locKind === 'http'
  const httpColumns = (state.locHttpSample?.suggestions as Suggestions | undefined)?.columns ?? []
  const sqlColumns = state.locSqlSample?.suggestions?.columns ?? []
  const names = http ? httpColumns.map((c) => refOf(c.path)) : sqlColumns.map((c) => c.name)
  const sampled = http ? state.locHttpSample : state.locSqlSample
  const busy = sample.busy || sqlSample.busy
  const setProp = (i: number, patch: Partial<PropertyDraft>) => set({ locProperties: state.locProperties.map((p, j) => (j === i ? { ...p, ...patch } : p)), locPreview: null })
  const fc = state.locPreview?.ok ? state.locPreview.result?.sample : undefined
  return (
    <>
      <p>Where are the monitoring locations (stations, gages, wells)? Each one gets its own time series.</p>
      <p className="muted small">
        Template: time series → OGC API - EDR. <a href="#/create/new">Choose another template</a>
      </p>
      <fieldset>
        <legend>The locations come from</legend>
        <label className="radio">
          <input type="radio" name="loc-kind" checked={http} onChange={() => set({ locKind: 'http', locPreview: null })} /> An HTTP JSON API
        </label>
        <label className="radio">
          <input type="radio" name="loc-kind" checked={!http} onChange={() => set({ locKind: 'sql', locPreview: null })} /> A SQL query
        </label>
      </fieldset>
      {http ? (
        <HttpSourceFields
          value={state.locHttp}
          onChange={(patch) => set({ locHttp: { ...state.locHttp, ...patch }, locHttpSample: null, locPreview: null })}
          prefix="loc-"
          urlLabel="Locations URL"
          urlHelp="The address that lists the locations, without query parameters."
          queryHelp="Fixed parameters, such as type=waterlevels."
          errors={errors}
          showErrors={showErrors}
        />
      ) : (
        <>
          <SqlConnectionFields value={state.locSql} onChange={(patch) => set({ locSql: { ...state.locSql, ...patch }, locSqlSample: null, locPreview: null })} errors={errors} showErrors={showErrors} prefix="loc-" legend="Database" />
          <label htmlFor="loc-query">Locations query</label>
          <p className="muted small" id="loc-query-help">
            One SELECT with an id, a geometry (or longitude and latitude) and any other columns.
          </p>
          <CodeEditor id="loc-query" value={state.locQuery} language={state.locSql.dialect === 'sqlite' ? 'sql-sqlite' : 'sql-postgresql'} label="Locations query" describedBy="loc-query-help" onChange={(locQuery) => set({ locQuery, locSqlSample: null, locPreview: null })} />
          <FieldError id="loc-query" errors={errors} show={showErrors} />
        </>
      )}
      <p>
        <button type="button" className="primary" onClick={http ? fetchHttp : runSql} disabled={busy}>
          {http ? 'Fetch the locations' : 'Run the locations query'}
        </button>
      </p>
      {busy && <Spinner label="Reading the locations in the sandbox…" />}
      <FieldError id="loc-sample" errors={errors} show={showErrors} />
      {sampled && !busy && !sampled.ok && <Failure result={sampled} />}
      {sampled?.ok && !busy && (
        <>
          <div className="inline-fields">
            {http && (
              <label>
                Records
                <input value={state.locRecords} onChange={(e) => set({ locRecords: e.target.value, locPreview: null })} />
              </label>
            )}
            <label>
              Location id <span aria-hidden="true">*</span>
              <select id="loc-id" value={state.locId} onChange={(e) => set({ locId: e.target.value, locPreview: null })} {...described('loc-id', errors, showErrors)}>
                <option value="">Choose…</option>
                {names.map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
            {!http && (
              <label>
                Id type
                <select value={state.locIdType} onChange={(e) => set({ locIdType: e.target.value as SqlIdType, locPreview: null })}>
                  <option value="string">text</option>
                  <option value="integer">integer</option>
                </select>
              </label>
            )}
          </div>
          <FieldError id="loc-id" errors={errors} show={showErrors} />
          {http ? (
            <div className="inline-fields">
              {(['x', 'y'] as const).map((axis) => (
                <label key={axis}>
                  {axis === 'x' ? 'Longitude' : 'Latitude'} <span aria-hidden="true">*</span>
                  <select
                    id={`loc-${axis}`}
                    value={state.locHttpGeometry.type === 'point' ? state.locHttpGeometry[axis] : ''}
                    onChange={(e) => {
                      const g = state.locHttpGeometry.type === 'point' ? state.locHttpGeometry : { type: 'point' as const, x: '', y: '' }
                      set({ locHttpGeometry: { ...g, [axis]: e.target.value }, locPreview: null })
                    }}
                  >
                    <option value="">Choose…</option>
                    {names.map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          ) : (
            <SqlGeometryFields geometry={state.locSqlGeometry} names={names} onChange={(g) => set({ locSqlGeometry: g, locPreview: null })} />
          )}
          <FieldError id="loc-geometry" errors={errors} show={showErrors} />
          <fieldset>
            <legend>Properties of each location</legend>
            {state.locProperties.map((p, i) => (
              <label key={p.from ?? p.name} className="check">
                <input type="checkbox" checked={p.include} onChange={(e) => setProp(i, { include: e.target.checked })} /> {p.name}
                <span className="muted small"> ({p.type})</span>
              </label>
            ))}
            <label>
              Name shown for each location
              <select value={state.locName} onChange={(e) => set({ locName: e.target.value, locPreview: null })}>
                <option value="">None</option>
                {state.locProperties
                  .filter((p) => p.include && p.type === 'string')
                  .map((p) => (
                    <option key={p.name}>{p.name}</option>
                  ))}
              </select>
            </label>
          </fieldset>
          <p>
            <button type="button" className="primary" onClick={runPreview} disabled={preview.busy}>
              Preview the locations
            </button>
          </p>
        </>
      )}
      {preview.busy && <Spinner label="Loading the locations with the features engine…" />}
      <FieldError id="loc-preview" errors={errors} show={showErrors} />
      {state.locPreview && !state.locPreview.ok && !preview.busy && <Failure result={state.locPreview} />}
      {fc && !preview.busy && (
        <>
          <p className="notice ok" role="status">
            {fc.numberMatched ?? fc.features.length} location(s).
          </p>
          <div className="two-col">
            <SampleTable sample={fc} />
            <MapView features={fc} bbox={state.locPreview?.result?.extent?.bbox} label="Map of the locations" />
          </div>
        </>
      )}
    </>
  )
}

function SqlGeometryFields({ geometry, names, onChange }: { geometry: SqlGeometry; names: string[]; onChange: (g: SqlGeometry) => void }) {
  return (
    <fieldset>
      <legend>Where each location is</legend>
      <select
        id="loc-geometry-encoding"
        aria-label="Geometry encoding"
        value={geometry.encoding}
        onChange={(e) => {
          const encoding = e.target.value as SqlEncoding
          if (encoding === 'none') onChange({ encoding })
          else if (encoding === 'xy') onChange({ encoding, x: names[0] ?? '', y: names[1] ?? '' })
          else onChange({ encoding, column: names[0] ?? '' })
        }}
      >
        <option value="native">A PostGIS geometry column</option>
        <option value="gpkg">A GeoPackage geometry column</option>
        <option value="wkb">A WKB column</option>
        <option value="wkt">A WKT text column</option>
        <option value="geojson">A GeoJSON column</option>
        <option value="xy">Longitude and latitude columns</option>
        <option value="none">No geometry</option>
      </select>
      {geometry.encoding === 'xy' && (
        <div className="inline-fields">
          {(['x', 'y'] as const).map((axis) => (
            <label key={axis}>
              {axis === 'x' ? 'Longitude (x)' : 'Latitude (y)'}
              <select value={geometry[axis]} onChange={(e) => onChange({ ...geometry, [axis]: e.target.value })}>
                {names.map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
      {geometry.encoding !== 'none' && geometry.encoding !== 'xy' && (
        <label>
          Column
          <select value={geometry.column} onChange={(e) => onChange({ ...geometry, column: e.target.value })}>
            {names.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      )}
    </fieldset>
  )
}

// ------------------------------------------------------------------- parameters

const EMPTY: ParameterDraft = { id: '', label: '', symbol: '', unitLabel: '', upstream: '', observedProperty: '', unitUri: '' }

export function TsParametersStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  const setRows = (parameters: ParameterDraft[]) => dispatch({ type: 'set', patch: { parameters, obsSample: null } })
  const setRow = (i: number, patch: Partial<ParameterDraft>) => setRows(state.parameters.map((p, j) => (j === i ? { ...p, ...patch } : p)))
  return (
    <>
      <p>
        What is measured at each location? Each parameter needs a unit, which EDR clients show. The source's name for a parameter is what fills{' '}
        <code>{'{parameter}'}</code> in the observations request.
      </p>
      <FieldError id="parameters" errors={errors} show={showErrors} />
      {state.parameters.map((p, i) => (
        <fieldset key={i} className="parameter">
          <legend>Parameter {i + 1}</legend>
          <div className="inline-fields">
            <label>
              Id <span aria-hidden="true">*</span>
              <input id={`param-id-${i}`} value={p.id} placeholder="water_level" onChange={(e) => setRow(i, { id: e.target.value.replace(/[^A-Za-z0-9_]/g, '_') })} />
            </label>
            <label>
              Label
              <input value={p.label} placeholder="Water level" onChange={(e) => setRow(i, { label: e.target.value })} />
            </label>
            <label>
              Unit symbol <span aria-hidden="true">*</span>
              <input id={`param-symbol-${i}`} value={p.symbol} placeholder="m" onChange={(e) => setRow(i, { symbol: e.target.value })} />
            </label>
            <label>
              Unit name
              <input value={p.unitLabel} placeholder="metre" onChange={(e) => setRow(i, { unitLabel: e.target.value })} />
            </label>
            <label>
              The source's name
              <input value={p.upstream} placeholder={p.id || 'same as the id'} onChange={(e) => setRow(i, { upstream: e.target.value })} />
            </label>
          </div>
          <details>
            <summary>Vocabulary links (Geoconnex)</summary>
            <div className="inline-fields">
              <label>
                Observed property URI
                <span className="muted small"> e.g. an ODM2 variable name</span>
                <input value={p.observedProperty} placeholder="http://vocabulary.odm2.org/variablename/…" onChange={(e) => setRow(i, { observedProperty: e.target.value })} />
              </label>
              <label>
                Unit URI
                <span className="muted small"> e.g. a QUDT unit</span>
                <input value={p.unitUri} placeholder="http://qudt.org/vocab/unit/M" onChange={(e) => setRow(i, { unitUri: e.target.value })} />
              </label>
            </div>
          </details>
          {state.parameters.length > 1 && (
            <button type="button" onClick={() => setRows(state.parameters.filter((_, j) => j !== i))}>
              Remove parameter {i + 1}
            </button>
          )}
        </fieldset>
      ))}
      <p>
        <button type="button" onClick={() => setRows([...state.parameters, { ...EMPTY }])}>
          Add a parameter
        </button>
      </p>
    </>
  )
}

// ------------------------------------------------------------------- observations

export function TsObservationsStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  const set = (patch: Parameters<typeof dispatch>[0] extends infer A ? (A extends { type: 'set'; patch: infer P } ? P : never) : never) => dispatch({ type: 'set', patch })
  const job = useJob<ObservationSampleResult>()
  const http = state.obsKind === 'http'
  const locations = (state.locPreview?.ok ? state.locPreview.result?.sample?.features : []) ?? []
  const parameter = state.parameters.find((p) => p.id === state.obsParameter) ?? state.parameters[0]

  const fetch = () =>
    void job.run(
      async () =>
        unwrap(
          await api.POST('/api/samples/observations', {
            body: {
              observations: observationsBlock(state) as unknown as Record<string, unknown>,
              location_id: state.obsLocation,
              parameter: parameter ? parameter.upstream || parameter.id : '',
              window: state.obsWindow,
              test_secrets: observationSecrets(state),
            },
          }),
        ),
      (r) => {
        const s = r.suggestions
        const patch: Parameters<typeof set>[0] = { obsSample: r }
        if (s && http) {
          if (s.records) patch.obsRecords = s.records
          if (s.time) Object.assign(patch, { obsTime: refOf(s.time.from), obsTimeFormat: s.time.format })
          if (s.value) patch.obsValue = refOf(s.value.from)
        } else if (s && !http) {
          const time = s.properties?.find((p) => p.type === 'datetime')
          const value = s.properties?.find((p) => p.type === 'number' || p.type === 'integer')
          if (time) Object.assign(patch, { obsTime: time.from ?? time.name, obsTimeFormat: time.format ?? 'iso8601' })
          if (value) patch.obsValue = value.from ?? value.name
        }
        set(patch)
      },
      (m) => set({ obsSample: failed(m) }),
    )

  const sample = state.obsSample
  const columns = http ? (sample?.suggestions?.columns ?? []).map((c) => refOf(c.path)) : (sample?.result?.columns ?? []).map((c) => c.name)
  return (
    <>
      <p>How are one location's observations fetched? Fetch them for one location to choose the time and value fields.</p>
      <fieldset>
        <legend>The observations come from</legend>
        <label className="radio">
          <input type="radio" name="obs-kind" checked={http} onChange={() => set({ obsKind: 'http', obsSample: null })} /> An HTTP JSON API
        </label>
        <label className="radio">
          <input type="radio" name="obs-kind" checked={!http} onChange={() => set({ obsKind: 'sql', obsSample: null })} /> A SQL query
        </label>
      </fieldset>
      {http ? (
        <>
          <HttpSourceFields
            value={state.obsHttp}
            onChange={(patch) => set({ obsHttp: { ...state.obsHttp, ...patch }, obsSample: null })}
            prefix="obs-"
            urlLabel="Observations URL"
            urlHelp="May contain {location_id} and {parameter} in its path."
            queryHelp="Values may use {location_id}, {parameter}, {start} and {end}; give dates a format, such as {start:%Y%m%d %H:%M}."
            errors={errors}
            showErrors={showErrors}
          />
          <label htmlFor="obs-error-path">Error message path (optional)</label>
          <p className="muted small" id="obs-error-path-help">
            For APIs that answer HTTP 200 with an error body, such as <code>$.error</code>: a location with an error has no data.
          </p>
          <input id="obs-error-path" value={state.obsErrorPath} placeholder="$.error" onChange={(e) => set({ obsErrorPath: e.target.value, obsSample: null })} aria-describedby="obs-error-path-help" />
        </>
      ) : (
        <>
          <SqlConnectionFields value={state.obsSql} onChange={(patch) => set({ obsSql: { ...state.obsSql, ...patch }, obsSample: null })} errors={errors} showErrors={showErrors} prefix="obs-" legend="Database" />
          <label htmlFor="obs-query">Observations query</label>
          <p className="muted small" id="obs-query-help">
            One SELECT that filters by <code>:location_id</code>, and by <code>:parameter</code>, <code>:start</code> and <code>:end</code> as needed.
          </p>
          <CodeEditor id="obs-query" value={state.obsQuery} language={state.obsSql.dialect === 'sqlite' ? 'sql-sqlite' : 'sql-postgresql'} label="Observations query" describedBy="obs-query-help" onChange={(obsQuery) => set({ obsQuery, obsSample: null })} />
        </>
      )}
      <fieldset>
        <legend>Try it</legend>
        <div className="inline-fields">
          <label>
            Location
            <select id="obs-location" value={state.obsLocation} onChange={(e) => set({ obsLocation: e.target.value, obsSample: null })}>
              {locations.map((f) => (
                <option key={String(f.id)} value={String(f.id)}>
                  {String(f.id)}
                  {state.locName && f.properties?.[state.locName] ? ` (${String(f.properties[state.locName])})` : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Parameter
            <select value={parameter?.id ?? ''} onChange={(e) => set({ obsParameter: e.target.value, obsSample: null })}>
              {state.parameters.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label || p.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            The last
            <select value={state.obsWindow} onChange={(e) => set({ obsWindow: e.target.value, obsSample: null })}>
              <option value="PT6H">6 hours</option>
              <option value="P1D">day</option>
              <option value="P7D">7 days</option>
            </select>
          </label>
        </div>
        <button type="button" className="primary" onClick={fetch} disabled={job.busy || !state.obsLocation}>
          Fetch the observations
        </button>
      </fieldset>
      {job.busy && <Spinner label="Fetching the observations in the sandbox…" />}
      <FieldError id="obs-sample" errors={errors} show={showErrors} />
      {sample && !job.busy && !sample.ok && <Failure result={sample} />}
      {sample?.ok && !job.busy && (
        <>
          <div className="notice ok" role="status">
            <p>
              {http
                ? `${sample.suggestions?.record_count ?? 0} observation(s) at ${sample.suggestions?.records ?? '?'}`
                : `${sample.result?.row_count ?? 0} row(s)`}
              {sample.egress?.length ? ` · through the egress gateway to ${sample.egress.join(', ')}` : ''}.
            </p>
            {sample.request && <p className="small mono">GET {sample.request.url}?{new URLSearchParams(sample.request.query).toString()}</p>}
            {sample.suggestions?.notes?.map((n) => (
              <p key={n} className="small">
                {n}
              </p>
            ))}
          </div>
          {http && (
            <div className="tree-wrap">
              <h3>Response</h3>
              <JsonTree data={sample.result?.json ?? null} label="The observations response" selected={state.obsRecords} onPick={(p) => set({ obsRecords: p })} />
            </div>
          )}
          <div className="inline-fields">
            {http && (
              <label>
                Records
                <input value={state.obsRecords} onChange={(e) => set({ obsRecords: e.target.value })} />
              </label>
            )}
            <label>
              Time <span aria-hidden="true">*</span>
              <select id="obs-time" value={state.obsTime} onChange={(e) => set({ obsTime: e.target.value })} {...described('obs-time', errors, showErrors)}>
                <option value="">Choose…</option>
                {columns.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label>
              Time format
              <input list="time-formats" value={state.obsTimeFormat} onChange={(e) => set({ obsTimeFormat: e.target.value })} />
              <datalist id="time-formats">
                {TIME_FORMATS.map((f) => (
                  <option key={f} value={f} />
                ))}
              </datalist>
            </label>
            <label>
              Value <span aria-hidden="true">*</span>
              <select id="obs-value" value={state.obsValue} onChange={(e) => set({ obsValue: e.target.value })} {...described('obs-value', errors, showErrors)}>
                <option value="">Choose…</option>
                {columns.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <FieldError id="obs-time" errors={errors} show={showErrors} />
          <FieldError id="obs-value" errors={errors} show={showErrors} />
        </>
      )}
    </>
  )
}

// ------------------------------------------------------------------- time and queries

export function TsQueriesStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  const set = (patch: Parameters<typeof dispatch>[0] extends infer A ? (A extends { type: 'set'; patch: infer P } ? P : never) : never) => dispatch({ type: 'set', patch })
  return (
    <>
      <p>How much time one request may cover, and which EDR queries clients may ask.</p>
      <div className="inline-fields">
        <label>
          Without a datetime, return the last
          <input id="default-window" value={state.defaultWindow} onChange={(e) => set({ defaultWindow: e.target.value.toUpperCase() })} {...described('default-window', errors, showErrors)} />
        </label>
        <label>
          At most, per request
          <input id="max-window" value={state.maxWindow} onChange={(e) => set({ maxWindow: e.target.value.toUpperCase() })} {...described('max-window', errors, showErrors)} />
        </label>
      </div>
      <p className="muted small">ISO 8601 durations: P7D is 7 days, PT6H is 6 hours. Months and years aren't allowed: they have no fixed length.</p>
      <FieldError id="default-window" errors={errors} show={showErrors} />
      <FieldError id="max-window" errors={errors} show={showErrors} />
      <fieldset>
        <legend>EDR queries</legend>
        {QUERY_TYPES.map(([qt, label]) => (
          <label key={qt} className="check">
            <input
              type="checkbox"
              checked={state.queryTypes.includes(qt)}
              onChange={(e) => set({ queryTypes: e.target.checked ? QUERY_TYPES.map(([q]) => q).filter((q) => q === qt || state.queryTypes.includes(q)) : state.queryTypes.filter((q) => q !== qt) })}
            />{' '}
            {label}
          </label>
        ))}
        <FieldError id="query-types" errors={errors} show={showErrors} />
      </fieldset>
      <div className="inline-fields">
        <label>
          position snaps to a station within (km)
          <input type="number" min={0.1} step={0.1} value={state.snapKm} onChange={(e) => set({ snapKm: Math.max(0.1, Number(e.target.value) || 1) })} />
        </label>
        <label>
          Most locations per request
          <input type="number" min={1} max={1000} value={state.maxLocations} onChange={(e) => set({ maxLocations: Math.max(1, Number(e.target.value) || 1) })} />
        </label>
        <label>
          Most observations per series
          <input type="number" min={1} value={state.maxObservations} onChange={(e) => set({ maxObservations: Math.max(1, Number(e.target.value) || 1) })} />
        </label>
      </div>
    </>
  )
}

// ------------------------------------------------------------------- check and save

export function TsCheckStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  const onResult = useCallback((result: ModeBResult<HarnessData> | null) => dispatch({ type: 'harness', result }), [dispatch])
  return <HarnessPanel spec={buildTsSpec(state)} secrets={secretsOf(state)} result={state.harness} onResult={onResult} errors={errors} showErrors={showErrors} />
}

export function TsSaveStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useTs()
  return (
    <SavePanel
      spec={buildTsSpec(state)}
      harness={state.harness?.result ?? null}
      meta={state.meta}
      onMeta={(patch, idEdited) => dispatch({ type: 'meta', patch, idEdited })}
      saved={state.saved}
      onSaved={(saved) => dispatch({ type: 'saved', saved })}
      errors={errors}
      showErrors={showErrors}
    />
  )
}

