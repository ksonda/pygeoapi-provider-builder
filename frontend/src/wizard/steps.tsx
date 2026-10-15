// The eight J1 steps (PLAN.md §6.1).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, unwrap, waitForJob, type ExportResult, type Finding, type ValidateResult } from '../api/client'
import type {
  CatalogCard,
  DescriptorDetail,
  IntrospectData,
  ProbeData,
  PublicSettings,
  SandboxResult,
  UiField,
  VerifyResult,
} from '../api/types'
import { MapView } from '../components/MapView'
import { CopyButton, ExplainedError, FindingList, SampleTable, Spinner, YamlView } from '../components/common'
import { DescriptorForm } from '../forms/DescriptorForm'
import { CoveragePreview, EdrPreview, MapPreview, probeSummary, TilePreview } from './previews'
import { current, runSecrets, useWizard, type StepId, type WizardState } from './state'

export interface StepProps {
  settings: PublicSettings
  showErrors: boolean
  errors: Record<string, string>
  goToField: (step: StepId, key: string) => void
}

// ------------------------------------------------------------------ helpers

export function choicesFor(state: WizardState, f: UiField): string[] | null {
  const intro = state.introspection?.ok ? state.introspection.result : null
  const values = current(state)?.values ?? {}
  // The table (or layer, for OGR) whose columns fill the pickers; the only one when there's one.
  const chosen = values[state.detail?.introspect?.table_key ?? 'table']
  const table = intro?.tables?.find((t) => t.name === chosen) ?? (intro?.tables?.length === 1 ? intro.tables[0] : undefined)
  switch (f.choices_from) {
    case 'introspect.columns':
      return intro?.columns?.map((c) => c.name) ?? table?.columns.map((c) => c.name) ?? null
    case 'introspect.tables':
      return intro?.tables?.map((t) => t.name) ?? null
    case 'introspect.geometry_columns':
      return table?.columns.filter((c) => c.geometry).map((c) => c.name) ?? null
    case 'probe.fields': {
      const fields = state.probe?.ok ? state.probe.result?.fields : null
      if (!fields) return null
      return Object.entries(fields)
        .filter(([, v]) => !f.field_types?.length || f.field_types.includes(v.type))
        .map(([k]) => k)
    }
    default:
      return null
  }
}

/** One entry per provider: what the probe of the edited one found, null for the others. */
export function probeSummaries(state: WizardState) {
  const r = state.probe?.ok ? state.probe.result : null
  return state.form.providers.map((_, i) => (i === state.index && r ? { fields: r.fields, sample_stats: r.sample_stats ?? null } : null))
}

function useFormProps(props: StepProps, step: 'connect' | 'map') {
  const { state, dispatch } = useWizard()
  const settingsRoot = props.settings.data_roots[0]?.server_path ?? '/'
  return {
    detail: state.detail as DescriptorDetail,
    step,
    provider: current(state) ?? { descriptor: '', variant: null, values: {}, secrets: {}, extra: {} },
    errors: props.errors,
    showErrors: props.showErrors,
    testSecrets: state.testSecrets,
    dataRoot: settingsRoot,
    choicesFor: (f: UiField) => choicesFor(state, f),
    onValue: (key: string, value: unknown) => dispatch({ type: 'value', key, value }),
    onVariant: (variant: string) => dispatch({ type: 'variant', variant }),
    onSecretEnv: (key: string, env: string) => dispatch({ type: 'secretEnv', key, env, edited: true }),
    onTestSecret: (key: string, value: string) => dispatch({ type: 'testSecret', key, value }),
  }
}


// -------------------------------------------------------------------- start

export function StartStep({ settings }: StepProps) {
  const { state, dispatch } = useWizard()
  const target = settings.targets.find((t) => t.id === state.target)
  return (
    <>
      <p>Publish a data source as an OGC API collection on your pygeoapi server, without writing YAML.</p>
      <p>
        To edit a collection, add a provider to one, or import YAML, go to <a href="#/configs">the collections on a server</a>.
      </p>
      <div className="field">
        <label htmlFor="target">Publish to</label>
        <select id="target" value={state.target} onChange={(e) => dispatch({ type: 'target', target: e.target.value })}>
          {settings.targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.title}
            </option>
          ))}
        </select>
        {target && (
          <p className="help">
            The new collection is added to <code>{target.base_config}</code>
            {target.server_url ? (
              <>
                {' '}
                for the server at <code>{target.server_url}</code>
              </>
            ) : null}
            .{' '}
            {target.kind === 'export'
              ? 'Nothing is written to the server: you download the result.'
              : target.kind === 'admin'
                ? 'Publishing writes it through the server\'s Admin API, then checks the server serves it.'
                : 'Publishing edits that file in place (with a backup), reloads pygeoapi and checks it serves the collection.'}
          </p>
        )}
      </div>
    </>
  )
}

// ------------------------------------------------------------------ backend

const INTENTS: { type: string; title: string; api: string }[] = [
  { type: 'feature', title: 'Vector features', api: 'OGC API - Features' },
  { type: 'coverage', title: 'Gridded coverages', api: 'OGC API - Coverages' },
  { type: 'edr', title: 'EDR time series and positions', api: 'OGC API - EDR' },
  { type: 'tile', title: 'Vector tiles', api: 'OGC API - Tiles' },
  { type: 'map', title: 'Maps', api: 'OGC API - Maps' },
  { type: 'record', title: 'Catalog records', api: 'OGC API - Records' },
  { type: 'stac', title: 'STAC', api: 'STAC' },
]

const CAPABILITY_LABELS: Record<string, string> = {
  bbox: 'bbox',
  datetime: 'datetime',
  properties: 'property filters',
  sortby: 'sorting',
  select_properties: 'property selection',
  skip_geometry: 'skip geometry',
  q: 'full-text',
  hits: 'counts',
  cql: 'CQL2',
  transactions: 'edits',
}

export function BackendStep() {
  const { state, dispatch } = useWizard()
  const [cards, setCards] = useState<CatalogCard[] | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)

  useEffect(() => {
    api
      .GET('/api/catalog')
      .then((r) => setCards(unwrap(r) as unknown as CatalogCard[]))
      .catch((e: unknown) => setError(String(e)))
  }, [])

  const choose = async (id: string) => {
    setLoading(id)
    try {
      const detail = unwrap(await api.GET('/api/catalog/{descriptor_id}', { params: { path: { descriptor_id: id } } }))
      dispatch({ type: 'descriptor', detail: detail as unknown as DescriptorDetail })
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(null)
    }
  }

  const q = query.trim().toLowerCase()
  const visible = (cards ?? []).filter((c) => !q || `${c.title} ${c.summary} ${c.plugin}`.toLowerCase().includes(q))
  // J3: pygeoapi uses the first provider of each type, so a second one would never be used.
  const taken = new Set(
    state.mode.kind === 'edit' && state.mode.addProvider
      ? state.form.providers
          .filter((_, i) => i !== state.index)
          .map((p) => cards?.find((c) => c.id === p.descriptor)?.provider_type ?? (p.extra as { type?: string } | undefined)?.type)
      : [],
  )

  return (
    <>
      <div className="field">
        <label htmlFor="catalog-search">Search the catalog</label>
        <input id="catalog-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="CSV, PostGIS, GeoPackage…" />
      </div>
      {error && <p className="error-text">{error}</p>}
      {!cards && !error && <Spinner label="Loading the catalog…" />}
      {INTENTS.map((intent) => {
        const group = visible.filter((c) => c.provider_type === intent.type)
        if (!group.length) return null
        return (
          <section key={intent.type} aria-labelledby={`intent-${intent.type}`}>
            <h3 id={`intent-${intent.type}`}>
              {intent.title} <span className="muted">· {intent.api}</span>
            </h3>
            <ul className="cards">
              {group.map((c) => {
                const selected = state.detail?.id === c.id
                const blocked = taken.has(c.provider_type)
                return (
                  <li key={c.id} className={`card${selected ? ' selected' : ''}`}>
                    <h4>{c.title}</h4>
                    <p>{c.summary}</p>
                    {c.verdict?.status === 'known-issue' && (
                      <p className="small warning-text">
                        <strong>Known issue:</strong> {c.verdict.note}
                      </p>
                    )}
                    {blocked && <p className="muted small">This collection already has a {c.provider_type} provider, and pygeoapi only uses the first one of each type.</p>}
                    <p className="chips" aria-label="Capabilities">
                      {Object.entries(c.capabilities)
                        .filter(([, v]) => v)
                        .map(([k]) => (
                          <span key={k} className="chip">
                            {CAPABILITY_LABELS[k] ?? k}
                          </span>
                        ))}
                    </p>
                    <p className="card-foot">
                      <span className={`badge ${c.availability.status}`}>
                        {c.availability.status === 'ready'
                          ? 'Ready'
                          : c.availability.status === 'missing'
                            ? `Missing ${c.availability.missing.join(', ')}`
                            : 'Checking…'}
                      </span>
                      {c.docs && (
                        <a href={c.docs} target="_blank" rel="noreferrer">
                          Docs
                        </a>
                      )}
                      <button type="button" aria-pressed={selected} onClick={() => void choose(c.id)} disabled={loading !== null || blocked}>
                        {selected ? 'Selected' : loading === c.id ? 'Loading…' : 'Use this'}
                      </button>
                    </p>
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}
      <p className="muted">
        Nothing fits? <a href="#/create/new">Create a new provider</a> for a source pygeoapi can't read today.
      </p>
    </>
  )
}

// ------------------------------------------------------------------ connect

const LOOKUP_LABELS: Record<string, string> = {
  sql: 'tables and columns',
  sqlite: 'tables and columns',
  ogr: 'layers and fields',
  esri: 'fields',
}

function introspectionReady(state: WizardState): boolean {
  const d = state.detail
  const values = current(state)?.values ?? {}
  if (!d?.introspect) return false
  if (d.introspect.what === 'sql') {
    const variant = current(state)?.variant
    if (variant === 'dsn') return Boolean(state.testSecrets.data)
    return Boolean(values['data.host'] && values['data.dbname'] && values['data.user'] && state.testSecrets['data.password'])
  }
  const source = values[d.introspect.source_key ?? 'data']
  if (typeof source !== 'string') return false
  // A server path, a URL, or a GDAL source such as WFS:https://…
  return source.startsWith('/') || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(source)
}

export function ConnectStep(props: StepProps) {
  const { state, dispatch } = useWizard()
  const form = useFormProps(props, 'connect')
  const [busy, setBusy] = useState(false)
  const ready = introspectionReady(state)
  const p = current(state)
  const sourceKey = state.detail?.introspect?.source_key ?? 'data'
  const key = JSON.stringify([p?.values?.[sourceKey], p?.values?.['data.source_type'], p?.values?.['data.host'], p?.values?.['data.dbname'], p?.values?.['data.user'], p?.variant, state.testSecrets])

  const lookUp = useCallback(async () => {
    if (!p) return
    setBusy(true)
    try {
      const job = unwrap(
        await api.POST('/api/introspect', {
          body: { provider: p, resource_id: state.form.resource_id || 'resource', test_secrets: runSecrets(state) },
        }),
      )
      dispatch({ type: 'introspection', result: await waitForJob<SandboxResult<IntrospectData>>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'introspection', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    if (!ready) return
    const t = setTimeout(() => void lookUp(), 500)
    return () => clearTimeout(t)
  }, [ready, lookUp])

  const intro = state.introspection
  const verdict = state.detail?.verdict
  return (
    <>
      {verdict?.status === 'known-issue' && (
        <p className="notice warning">
          <strong>Known issue in pygeoapi 0.24:</strong> {verdict.note}
        </p>
      )}
      {state.detail?.notes.length ? (
        <ul className="notice info">
          {state.detail.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      <DescriptorForm {...form} />
      {state.detail?.env?.length ? (
        <fieldset>
          <legend>Variables the provider reads</legend>
          <p className="muted small">
            Its spec reads these from the pygeoapi server's environment. Values entered here are for this test only and stay in this page's memory.
          </p>
          {state.detail.env.map((v) => (
            <label key={v.name} className="block">
              Test value for <code>{v.name}</code>
              <input
                type={v.secret ? 'password' : 'text'}
                autoComplete="off"
                value={state.testSecrets[`env:${v.name}`] ?? ''}
                onChange={(e) => dispatch({ type: 'testSecret', key: `env:${v.name}`, value: e.target.value })}
              />
            </label>
          ))}
        </fieldset>
      ) : null}
      <div className="lookup">
        {busy ? (
          <Spinner label={`Looking up ${LOOKUP_LABELS[state.detail?.introspect?.what ?? ''] ?? 'columns'} in the sandbox…`} />
        ) : intro?.ok ? (
          <p className="ok-text" role="status">
            Found{' '}
            {intro.result?.tables
              ? `${intro.result.tables.length} ${state.detail?.introspect?.what === 'ogr' ? 'layer' : 'table'}(s)`
              : `${intro.result?.columns?.length ?? 0} ${state.detail?.introspect?.what === 'esri' ? 'field' : 'column'}(s)`}
            . Pick them from the lists above.
          </p>
        ) : intro && !intro.ok ? (
          <ExplainedError explanation={intro.explanation} error={intro.error} logs={intro.logs} />
        ) : null}
        {state.detail?.introspect && (
          <button type="button" onClick={() => void lookUp()} disabled={!ready || busy}>
            Look up {LOOKUP_LABELS[state.detail.introspect.what] ?? 'columns'}
          </button>
        )}
      </div>
    </>
  )
}

// --------------------------------------------------------------------- test

/** What the probe does after loading the provider, by provider type. */
const TEST_INTRO: Record<string, string> = {
  feature: 'reads its fields and a few items.',
  record: 'reads its fields and a few records.',
  coverage: 'asks for a window of the first band as CoverageJSON, as /coverage?subset= does.',
  map: "asks for a map of the collection's extent, as /map does.",
  tile: 'lists its tiling schemes and fetches the first tiles over the extent.',
  edr: 'lists its query types and parameters, and runs one query.',
}

export function TestStep() {
  const { state, dispatch } = useWizard()
  const [busy, setBusy] = useState(false)
  const probe = state.probe

  const run = useCallback(async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/probe', { body: { form: state.form, provider_index: state.index, test_secrets: runSecrets(state) } }))
      dispatch({ type: 'probe', result: await waitForJob<SandboxResult<ProbeData>>(job.job_id) })
    } catch (e) {
      dispatch({ type: 'probe', result: { ok: false, explanation: { code: 'client', message: String(e) } } })
    } finally {
      setBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.form, state.testSecrets])

  useEffect(() => {
    if (state.probe) return
    const t = setTimeout(() => void run(), 0)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const r = probe?.ok ? probe.result : null
  const kind = r?.provider_type ?? state.detail?.provider_type
  const summary = r ? probeSummary(r) : null
  const items = kind === 'feature' || kind === 'record'
  return (
    <>
      <p>
        pygeoapi loads the provider with its own <code>load_plugin</code> in a sandbox, then {TEST_INTRO[kind ?? 'feature'] ?? TEST_INTRO.feature}
      </p>
      {busy && <Spinner label="Testing the connection in the sandbox…" />}
      {probe && !probe.ok && !busy && (
        <>
          <ExplainedError explanation={probe.explanation} error={probe.error} logs={probe.logs} />
          <p>
            <button type="button" onClick={() => dispatch({ type: 'goto', step: 'connect' })}>
              Back to the connection
            </button>{' '}
            <button type="button" onClick={() => void run()}>
              Test again
            </button>
          </p>
        </>
      )}
      {r && !busy && (
        <>
          <div className="notice ok" role="status">
            <p>
              <strong>pygeoapi loaded the provider</strong>{' '}
              {summary ?? (
                <>
                  and returned {r.sample?.features.length ?? 0} item(s)
                  {typeof r.sample?.numberMatched === 'number' ? ` of ${r.sample.numberMatched}` : ''}
                </>
              )}
              .
            </p>
            <p className="muted small">
              Tested with {probe?.runner === 'docker' ? 'the sandbox image (geopython/pygeoapi:0.24.0 plus the spec engine)' : 'a local pygeoapi 0.24.0, without the sandbox'} in {probe?.ms} ms
              {r.timings.load_ms !== undefined ? ` (load ${r.timings.load_ms} ms, query ${r.timings.query_ms ?? '?'} ms)` : ''}.
            </p>
          </div>
          {r.id_stats && (r.id_stats.duplicates > 0 || r.id_stats.nulls > 0) && (
            <p className="notice warning">
              The identifier has {r.id_stats.duplicates} duplicate(s) and {r.id_stats.nulls} empty value(s) in {r.id_stats.count} item(s). Items
              need unique ids for /items/&#123;id&#125;.
            </p>
          )}
          {kind === 'coverage' && <CoveragePreview r={r} />}
          {kind === 'map' && <MapPreview r={r} />}
          {kind === 'tile' && <TilePreview r={r} />}
          {kind === 'edr' && <EdrPreview r={r} />}
          {items && (
          <>
          <h3>Fields</h3>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Fields">
          <table className="compact">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Type</th>
                <th scope="col">Sample values</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(r.fields).map(([name, f]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td>{f.type}</td>
                  <td className="muted">{(r.sample_stats?.[name]?.values ?? []).map(String).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          </>
          )}
          {r.sample && (
            <div className="two-col">
              <SampleTable sample={r.sample} />
              <MapView features={r.sample} bbox={r.extent?.bbox} label="Map of the sample items" />
            </div>
          )}
          <p>
            <button type="button" onClick={() => void run()}>
              Test again
            </button>
          </p>
        </>
      )}
    </>
  )
}

// ------------------------------------------------------------------- fields

export function FieldsStep(props: StepProps) {
  const { state } = useWizard()
  const form = useFormProps(props, 'map')
  const r = state.probe?.ok ? state.probe.result : null
  const values = current(state)?.values ?? {}
  const uriField = typeof values.uri_field === 'string' ? values.uri_field : null
  const uriValues = uriField ? (r?.sample_stats?.[uriField]?.values ?? []).map(String) : []
  const props_ = Array.isArray(values.properties) ? (values.properties as string[]) : undefined
  return (
    <>
      <p>Choose how items are identified, titled and dated, and which properties to publish.</p>
      <DescriptorForm {...form} />
      {props.settings.geoconnex && uriField && (
        <div className={`notice ${uriValues.every((v) => v.startsWith('https://geoconnex.us/')) ? 'ok' : 'warning'}`}>
          <p>
            <strong>Geoconnex:</strong>{' '}
            {uriValues.length === 0
              ? `No sample values for ${uriField}.`
              : uriValues.every((v) => v.startsWith('https://geoconnex.us/'))
                ? `The sample values of ${uriField} are Geoconnex PIDs.`
                : `Some sample values of ${uriField} aren't Geoconnex PIDs (https://geoconnex.us/…): ${uriValues.slice(0, 2).join(', ')}.`}
          </p>
        </div>
      )}
      {r?.sample && (
        <div className="two-col">
          <SampleTable sample={r.sample} columns={props_} />
          <MapView features={r.sample} bbox={r.extent?.bbox} label="Map of the sample items" />
        </div>
      )}
    </>
  )
}

// ----------------------------------------------------------------- describe

const LINK_RELS = ['canonical', 'alternate', 'about', 'describedby', 'license', 'related']

export function DescribeStep(props: StepProps) {
  const { state, dispatch } = useWizard()
  const r = state.form.resource
  const probe = state.probe?.ok ? state.probe.result : null
  const bbox = (r.extents?.spatial?.bbox ?? [-180, -90, 180, 90]).map(Number)
  const temporal = r.extents?.temporal ?? null
  const setExtents = (patch: Partial<NonNullable<typeof r.extents>>) =>
    dispatch({ type: 'resource', patch: { extents: { ...(r.extents ?? {}), ...patch } } })
  const setBbox = useCallback(
    (b: number[]) => dispatch({ type: 'resource', patch: { extents: { ...(r.extents ?? {}), spatial: { ...(r.extents?.spatial ?? {}), bbox: b } } } }),
    [dispatch, r.extents],
  )
  const err = (key: string) => (props.showErrors ? props.errors[key] : undefined)
  const keywords = Array.isArray(r.keywords) ? r.keywords : []
  const [keywordText, setKeywordText] = useState(keywords.join(', '))
  const links = r.links ?? []
  const setLinks = (next: typeof links) => dispatch({ type: 'resource', patch: { links: next } })

  return (
    <>
      <div className={`field${err('resource_id') ? ' has-error' : ''}`}>
        <label htmlFor="resource_id">Collection id *</label>
        <input
          id="resource_id"
          className="mono"
          value={state.form.resource_id}
          aria-invalid={err('resource_id') ? true : undefined}
          aria-describedby="resource_id-help"
          onChange={(e) => dispatch({ type: 'resourceId', id: e.target.value, edited: true })}
        />
        <p id="resource_id-help" className="help">
          Used in the URL: <code>/collections/{state.form.resource_id || '…'}</code>. Renaming it later breaks links, including any Geoconnex
          PIDs that redirect to it.
        </p>
        {err('resource_id') && <p className="error-text">{err('resource_id')}</p>}
      </div>
      <div className={`field${err('title') ? ' has-error' : ''}`}>
        <label htmlFor="title">Title *</label>
        <input id="title" value={typeof r.title === 'string' ? r.title : ''} aria-invalid={err('title') ? true : undefined}
          onChange={(e) => dispatch({ type: 'resource', patch: { title: e.target.value } })} />
        {err('title') && <p className="error-text">{err('title')}</p>}
      </div>
      <div className={`field${err('description') ? ' has-error' : ''}`}>
        <label htmlFor="description">Description *</label>
        <textarea id="description" rows={3} value={typeof r.description === 'string' ? r.description : ''}
          aria-invalid={err('description') ? true : undefined}
          onChange={(e) => dispatch({ type: 'resource', patch: { description: e.target.value } })} />
        {err('description') && <p className="error-text">{err('description')}</p>}
      </div>
      <div className="field">
        <label htmlFor="keywords">Keywords</label>
        <input id="keywords" value={keywordText} aria-describedby="keywords-help"
          onChange={(e) => {
            setKeywordText(e.target.value)
            dispatch({ type: 'resource', patch: { keywords: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) } })
          }} />
        <p id="keywords-help" className="help">Separate keywords with commas.</p>
      </div>

      <fieldset>
        <legend>Links</legend>
        {links.map((link, i) => (
          <div key={i} className="link-row">
            <label>
              Title * <input value={link.title} onChange={(e) => setLinks(links.map((l, j) => (j === i ? { ...l, title: e.target.value } : l)))} />
            </label>
            <label>
              URL * <input className="mono" value={link.href} onChange={(e) => setLinks(links.map((l, j) => (j === i ? { ...l, href: e.target.value } : l)))} />
            </label>
            <label>
              Relation
              <select value={link.rel} onChange={(e) => setLinks(links.map((l, j) => (j === i ? { ...l, rel: e.target.value } : l)))}>
                {LINK_RELS.map((rel) => (
                  <option key={rel}>{rel}</option>
                ))}
              </select>
            </label>
            <button type="button" aria-label={`Remove link ${i + 1}`} onClick={() => setLinks(links.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" onClick={() => setLinks([...links, { type: 'text/html', rel: 'canonical', title: '', href: '' }])}>
          Add a link
        </button>
        <p className="help">pygeoapi needs a title on every link, or the collection disappears from /collections.</p>
      </fieldset>

      <fieldset>
        <legend>Spatial extent</legend>
        <div className="bbox-inputs">
          {(['West', 'South', 'East', 'North'] as const).map((label, i) => (
            <label key={label}>
              {label}
              <input type="number" step="any" value={Number.isFinite(bbox[i]) ? bbox[i] : ''}
                onChange={(e) => setBbox(bbox.map((v, j) => (j === i ? Number(e.target.value) : v)))} />
            </label>
          ))}
        </div>
        {probe?.extent?.bbox && (
          <button type="button" onClick={() => setBbox((probe.extent?.bbox ?? []).map((v) => Math.round(v * 1e6) / 1e6))}>
            Use the data extent{probe.extent.from === 'sample' ? ' (from the sample)' : ''}
          </button>
        )}
        <MapView features={probe?.sample} bbox={bbox} onDraw={setBbox} label="Map of the collection's spatial extent" />
      </fieldset>

      <fieldset>
        <legend>Temporal extent (optional)</legend>
        <div className="inline">
          <label>
            Begin <input className="mono" placeholder="2000-01-01T00:00:00Z" value={temporal?.begin ?? ''}
              onChange={(e) => setExtents({ temporal: { begin: e.target.value || null, end: temporal?.end ?? null } })} />
          </label>
          <label>
            End <input className="mono" placeholder="Open-ended" value={temporal?.end ?? ''}
              onChange={(e) => setExtents({ temporal: { begin: temporal?.begin ?? null, end: e.target.value || null } })} />
          </label>
        </div>
        {probe?.extent?.temporal && (
          <button type="button" onClick={() => setExtents({ temporal: { begin: probe.extent?.temporal?.[0] ?? null, end: probe.extent?.temporal?.[1] ?? null } })}>
            Use the data's time range
          </button>
        )}
        <p className="help">RFC 3339 timestamps, e.g. 2000-10-30T18:24:39Z. Leave End empty for open-ended.</p>
      </fieldset>
    </>
  )
}

// ------------------------------------------------------------------- review

export function ReviewStep({ goToField, onBlocking }: StepProps & { onBlocking: (b: boolean | null) => void }) {
  const { state } = useWizard()
  const [result, setResult] = useState<ValidateResult | null>(null)
  const [openapi, setOpenapi] = useState<SandboxResult<{ paths: string[] }> | 'running' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    api
      .POST('/api/validate', { body: { form: state.form, target: state.target, probes: probeSummaries(state) } })
      .then((r) => setResult(unwrap(r)))
      .catch((e: unknown) => setError(String(e)))
    setOpenapi('running')
    api
      .POST('/api/openapi-check', { body: { form: state.form, target: state.target, test_secrets: runSecrets(state) } })
      .then((r) => waitForJob<SandboxResult<{ paths: string[] }>>(unwrap(r).job_id))
      .then(setOpenapi)
      .catch((e: unknown) => setOpenapi({ ok: false, explanation: { code: 'client', message: String(e) } }))
  }, [state])

  const findings: Finding[] = useMemo(() => {
    const out = [...(result?.findings ?? [])]
    if (openapi && openapi !== 'running' && !openapi.ok) {
      out.push({
        layer: 'openapi',
        severity: 'error',
        code: openapi.explanation?.code ?? 'openapi',
        message: `pygeoapi couldn't build its OpenAPI document with this collection: ${openapi.explanation?.message ?? 'unknown error'}`,
        hint: openapi.explanation?.hint ?? null,
        pointer: null,
        source: null,
        details: null,
      })
    }
    return out
  }, [result, openapi])

  const blocking = result ? result.blocking || (openapi !== 'running' && openapi !== null && !openapi.ok) : null
  useEffect(() => onBlocking(openapi === 'running' ? null : blocking), [blocking, openapi, onBlocking])

  const go = (f: Finding) => {
    const ptr = f.pointer ?? ''
    const m = /^\/providers\/\d+\/values\/(.+)$/.exec(ptr)
    if (m?.[1]) {
      const key = m[1]
      const connect = state.detail?.ui.groups.find((g) => g.id === 'connect')
      const inConnect = connect?.keys.includes(key) || connect?.advanced.includes(key) || state.detail?.ui.variants.some((v) => v.keys.includes(key))
      goToField(inConnect ? 'connect' : 'fields', key)
    } else if (ptr === '/resource_id' || ptr.startsWith('/resource')) {
      goToField('describe', ptr === '/resource_id' ? 'resource_id' : (ptr.split('/')[2] ?? 'title'))
    }
  }

  return (
    <>
      {error && <p className="error-text">{error}</p>}
      {!result && !error && <Spinner label="Checking the collection against pygeoapi's rules…" />}
      {result && (
        <>
          <h3>Checks</h3>
          <FindingList findings={findings} onGo={go} />
          {openapi === 'running' && <Spinner label="Generating pygeoapi's OpenAPI document with this collection (this loads the provider)…" />}
          {openapi && openapi !== 'running' && openapi.ok && (
            <p className="ok-text">pygeoapi generated its OpenAPI document with this collection.</p>
          )}
          <h3>Configuration</h3>
          <p>
            This block is added under <code>resources:</code> in the target config. Everything else in that file stays exactly as it is.
          </p>
          <YamlView text={result.yaml} label="Generated YAML for this collection" />
          {result.env.length > 0 && (
            <>
              <h3>Environment variables the server needs</h3>
              <ul>
                {result.env.map((e) => (
                  <li key={e.name}>
                    <code>{e.name}</code> {e.secret ? '(secret)' : ''} for {e.used_by.join(', ')}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </>
  )
}

// ------------------------------------------------------------------ publish

export function PublishStep({ settings }: StepProps) {
  const { state } = useWizard()
  const [result, setResult] = useState<ExportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [verify, setVerify] = useState<VerifyResult | 'running' | null>(null)
  const target = settings.targets.find((t) => t.id === state.target)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    api
      .POST('/api/export', { body: { form: state.form, target: state.target, probes: probeSummaries(state) } })
      .then((r) => setResult(unwrap(r)))
      .catch((e: unknown) => setError(String(e)))
  }, [state])

  const download = () => {
    if (!result) return
    const url = URL.createObjectURL(new Blob([result.merged_yaml], { type: 'application/yaml' }))
    const a = document.createElement('a')
    a.href = url
    a.download = result.merged_filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const check = async () => {
    setVerify('running')
    try {
      const r = await api.POST('/api/targets/{target_id}/verify', {
        params: { path: { target_id: state.target } },
        body: { resource_id: state.form.resource_id },
      })
      setVerify(unwrap(r) as unknown as VerifyResult)
    } catch (e) {
      setVerify({ ok: false, checks: [], message: String(e) })
    }
  }

  if (error) return <p className="error-text">{error}</p>
  if (!result) return <Spinner label="Preparing the export…" />
  const base = target?.server_url?.replace(/\/$/, '')
  return (
    <>
      <div className="notice ok" role="status">
        <p>
          <strong>Your collection is ready.</strong> Download the merged config, or copy the block into your own.
        </p>
      </div>
      <p className="actions">
        <button type="button" className="primary" onClick={download}>
          Download {result.merged_filename}
        </button>
        <CopyButton text={result.fragment_yaml} label="Copy the collection's YAML" />
      </p>
      <h3>What's left</h3>
      <ol>
        {result.instructions.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      {result.env.length > 0 && (
        <p className="notice warning">
          Set {result.env.map((e) => e.name).join(', ')} on the server before restarting. Secret values are never written to the config.
        </p>
      )}
      <YamlView text={result.fragment_yaml} label="YAML for this collection" />
      {base && (
        <>
          <h3>Check the live server</h3>
          <p>After installing the config and restarting pygeoapi, check that the collection is served.</p>
          <button type="button" onClick={() => void check()} disabled={verify === 'running'}>
            Check {base}/collections/{state.form.resource_id}
          </button>
          {verify === 'running' && <Spinner label="Checking the server…" />}
          {verify && verify !== 'running' && (
            <ul className="checks">
              {verify.message && <li>{verify.message}</li>}
              {verify.checks.map((c) => (
                <li key={c.url} className={c.ok ? 'ok' : 'error'}>
                  {c.ok ? '✓' : '✗'}{' '}
                  <a href={c.url.replace('?f=json', '').replace('f=json&', '')} target="_blank" rel="noreferrer">
                    {c.url}
                  </a>{' '}
                  <span className="muted">
                    {c.status ?? ''} {c.detail ?? ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  )
}
