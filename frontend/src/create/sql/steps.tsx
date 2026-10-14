// The J4 steps for the sql-features template: database and query, map fields,
// check, save (PLAN.md §7.4). The query runs in the sandbox through the spec
// engine's own guard and read-only connection, exactly as the provider runs it.
import { useCallback, useState } from 'react'

import { api, unwrap, waitForJob } from '../../api/client'
import type { HarnessData, ModeBResult, ProbeData, PropertyType, SqlEncoding, SqlGeometry, SqlIdType, SqlSampleResult } from '../../api/types'
import { CodeEditor } from '../../components/CodeEditor'
import { SampleTable, Spinner } from '../../components/common'
import { MapView } from '../../components/MapView'
import { FieldError, Failure, HarnessPanel, Problems, SavePanel, described, type CreateStepProps } from '../shared'
import { SqlConnectionFields } from './connection'
import { buildSqlSpec, connectionOf, secretsOf, sourceOf, useSql, type PropertyDraft } from './state'

const TYPES: PropertyType[] = ['string', 'integer', 'number', 'boolean', 'datetime']
const TIME_FORMATS = ['iso8601', 'date', 'epoch_s', 'epoch_ms']
const ENCODINGS: [SqlEncoding, string][] = [
  ['native', 'A PostGIS geometry or geography column'],
  ['gpkg', 'A GeoPackage geometry column'],
  ['wkb', 'A WKB column (binary)'],
  ['wkt', 'A WKT text column'],
  ['geojson', 'A GeoJSON column'],
  ['xy', 'Longitude and latitude columns'],
  ['none', 'No geometry'],
]

function show(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') {
    const bytes = (v as { bytes?: number }).bytes
    return typeof bytes === 'number' ? `${bytes} bytes` : JSON.stringify(v)
  }
  return String(v)
}

// ------------------------------------------------------------------- source

export function SqlSourceStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useSql()
  const [busy, setBusy] = useState(false)
  const set = (patch: Parameters<typeof dispatch>[0] extends infer A ? (A extends { type: 'source'; patch: infer P } ? P : never) : never) => dispatch({ type: 'source', patch })

  const run = async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/samples/sql', { body: { source: sourceOf(state) as unknown as Record<string, unknown>, query: state.query, test_secrets: secretsOf(state) } }))
      dispatch({ type: 'sample', result: await waitForJob<SqlSampleResult>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'sample', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
  }

  const sample = state.sample
  const data = sample?.ok ? sample.result : null
  const queryProblems = sample?.problems?.filter((p) => p.pointer === '/query') ?? []
  const otherProblems = sample?.problems?.filter((p) => p.pointer !== '/query') ?? []
  const pg = state.dialect === 'postgresql'
  return (
    <>
      <p>Publish the result of a SQL query: a join, a view, or computed columns. The query runs in the sandbox, read-only, with a time limit.</p>
      <p className="muted small">
        Template: SQL query → OGC API - Features. <a href="#/create/new">Choose another template</a>
      </p>
      <SqlConnectionFields
        value={connectionOf(state)}
        onChange={({ testSecret, ...patch }) => {
          if (testSecret !== undefined) dispatch({ type: 'testSecret', value: testSecret })
          if (Object.keys(patch).length) set(patch)
        }}
        errors={errors}
        showErrors={showErrors}
      />
      <label htmlFor="sql-query">
        SQL query <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="sql-query-help">
        One SELECT. Joins, views, CTEs and computed columns are fine. The provider adds filters, sorting and paging around it.
      </p>
      <CodeEditor
        id="sql-query"
        value={state.query}
        language={pg ? 'sql-postgresql' : 'sql-sqlite'}
        label="SQL query"
        describedBy={['sql-query-help', showErrors && errors['sql-query'] ? 'sql-query-error' : ''].filter(Boolean).join(' ')}
        invalid={Boolean((showErrors && errors['sql-query']) || queryProblems.length)}
        onChange={(query) => set({ query })}
      />
      <FieldError id="sql-query" errors={errors} show={showErrors} />
      {queryProblems.map((p) => (
        <p key={p.message} className="field-error" role="alert">
          {p.message}
        </p>
      ))}
      <div className="inline-fields">
        <label>
          Time limit per query (seconds)
          <input type="number" min={1} max={300} value={state.statementTimeout} onChange={(e) => set({ statementTimeout: Math.max(1, Number(e.target.value) || 10) })} />
        </label>
      </div>
      <p>
        <button type="button" className="primary" onClick={() => void run()} disabled={busy || !state.query.trim()}>
          Run the query
        </button>
      </p>
      {busy && <Spinner label="Running the query in the sandbox…" />}
      <FieldError id="sample" errors={errors} show={showErrors} />
      {sample && !busy && !sample.ok && (otherProblems.length ? <Problems problems={otherProblems} /> : !queryProblems.length && <Failure result={sample} />)}
      {data && !busy && (
        <>
          <div className="notice ok" role="status">
            <p>
              <strong>{data.row_count} row(s)</strong> in {data.ms} ms{sample?.egress?.length ? ` · through the egress gateway to ${sample.egress.join(', ')}` : ''}.
            </p>
            {state.suggestions?.notes.map((n) => (
              <p key={n} className="small">
                {n}
              </p>
            ))}
          </div>
          <h3>The query as it will run</h3>
          <pre className="mono small query-view">{data.query}</pre>
          <h3 id="rows-heading">First rows</h3>
          <div className="table-wrap" tabIndex={0} role="region" aria-labelledby="rows-heading">
            <table className="compact">
              <thead>
                <tr>
                  {data.columns.map((c) => (
                    <th key={c.name} scope="col">
                      {c.name}
                      <span className="muted small"> {c.geometry ? `geometry (${c.geometry})` : (c.db_type ?? c.kinds.join('/'))}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.slice(0, 10).map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j} className="small">
                        {show(v).slice(0, 80)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

// ------------------------------------------------------------------- map

export function SqlMapStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useSql()
  const [busy, setBusy] = useState(false)
  const columns = state.suggestions?.columns ?? []
  const names = columns.map((c) => c.name)
  const geometry = state.geometry
  const setGeometry = (g: SqlGeometry) => dispatch({ type: 'mapping', patch: { geometry: g } })
  const setProp = (i: number, patch: Partial<PropertyDraft>) =>
    dispatch({ type: 'mapping', patch: { properties: state.properties.map((p, j) => (j === i ? { ...p, ...patch } : p)) } })
  const included = state.properties.filter((p) => p.include)
  const encodings = ENCODINGS.filter(([e]) => e !== 'native' || state.dialect === 'postgresql')

  const preview = useCallback(async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/specs/preview', { body: { spec: buildSqlSpec(state) as unknown as Record<string, unknown>, test_secrets: secretsOf(state) } }))
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
      <div className="inline-fields">
        <label>
          Item id <span aria-hidden="true">*</span>
          <select id="item-id" value={state.itemId} onChange={(e) => dispatch({ type: 'mapping', patch: { itemId: e.target.value } })} {...described('item-id', errors, showErrors)}>
            <option value="">Choose…</option>
            {columns
              .filter((c) => !c.geometry)
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                  {c.unique ? ' (unique in the sample)' : ''}
                </option>
              ))}
          </select>
        </label>
        <label>
          Id type
          <select value={state.itemIdType} onChange={(e) => dispatch({ type: 'mapping', patch: { itemIdType: e.target.value as SqlIdType } })}>
            <option value="string">text</option>
            <option value="integer">integer</option>
            <option value="number">number</option>
          </select>
        </label>
      </div>
      <p className="muted small">Must be unique: it becomes /items/&#123;id&#125;, and the provider sorts by it so pages stay stable.</p>
      <FieldError id="item-id" errors={errors} show={showErrors} />
      <fieldset>
        <legend>Geometry</legend>
        <label htmlFor="geometry-encoding">Where each item's location comes from</label>
        <select
          id="geometry-encoding"
          value={geometry.encoding}
          onChange={(e) => {
            const encoding = e.target.value as SqlEncoding
            if (encoding === 'none') setGeometry({ encoding })
            else if (encoding === 'xy') setGeometry({ encoding, x: names[0] ?? '', y: names[1] ?? '' })
            else setGeometry({ encoding, column: columns.find((c) => c.geometry === encoding)?.name ?? names[0] ?? '' })
          }}
        >
          {encodings.map(([e, label]) => (
            <option key={e} value={e}>
              {label}
            </option>
          ))}
        </select>
        {geometry.encoding === 'xy' && (
          <div className="inline-fields">
            {(['x', 'y'] as const).map((axis) => (
              <label key={axis}>
                {axis === 'x' ? 'Longitude (x)' : 'Latitude (y)'}
                <select value={geometry[axis]} onChange={(e) => setGeometry({ ...geometry, [axis]: e.target.value })}>
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
            <select value={geometry.column} onChange={(e) => setGeometry({ ...geometry, column: e.target.value })}>
              {names.map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
        )}
        {geometry.encoding !== 'none' && (
          <label>
            Coordinate reference system (optional)
            <span className="muted small"> The CRS the geometries are stored in, as a URI. Leave it empty for longitude/latitude (CRS84).</span>
            <input value={state.crs} placeholder="http://www.opengis.net/def/crs/EPSG/0/4326" onChange={(e) => dispatch({ type: 'mapping', patch: { crs: e.target.value } })} />
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
              <th scope="col">Column</th>
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
                    <select value={p.format ?? ''} aria-label={`Time format of ${p.name}`} onChange={(e) => setProp(i, { format: e.target.value || undefined })}>
                      <option value="">a date or time column</option>
                      {TIME_FORMATS.map((f) => (
                        <option key={f} value={f}>
                          text or number: {f}
                        </option>
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
        <label>
          Most rows read per request
          <input type="number" min={1} max={100000} value={state.maxRows} onChange={(e) => dispatch({ type: 'mapping', patch: { maxRows: Math.max(1, Number(e.target.value) || 1) } })} />
        </label>
      </div>
      <p className="muted small">Filters the database can't apply itself (a bbox on WKT or GeoJSON text, dates kept as text) are applied after reading, up to this many rows.</p>
      <p>
        <button type="button" className="primary" onClick={() => void preview()} disabled={busy}>
          Preview
        </button>
      </p>
      <FieldError id="preview" errors={errors} show={showErrors} />
      {busy && <Spinner label="Running the spec in the sandbox…" />}
      {state.preview && !state.preview.ok && !busy && <Failure result={state.preview} />}
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

// ------------------------------------------------------------------- check and save

export function SqlCheckStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useSql()
  const onResult = useCallback((result: ModeBResult<HarnessData> | null) => dispatch({ type: 'harness', result }), [dispatch])
  return <HarnessPanel spec={buildSqlSpec(state)} secrets={secretsOf(state)} result={state.harness} onResult={onResult} errors={errors} showErrors={showErrors} />
}

export function SqlSaveStep({ errors, showErrors }: CreateStepProps) {
  const { state, dispatch } = useSql()
  return (
    <SavePanel
      spec={buildSqlSpec(state)}
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
