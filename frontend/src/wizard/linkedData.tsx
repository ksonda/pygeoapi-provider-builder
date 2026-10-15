// The Describe step's linked data options (Geoconnex options, PLAN.md §6.1
// step 6): a JSON-LD template for items, where it goes, the collection's
// linked-data context, and a preview of the RDF pygeoapi would serve.
import { useEffect, useState } from 'react'

import { api, unwrap, waitForJob } from '../api/client'
import type { PublicSettings, SandboxResult } from '../api/types'
import { ExplainedError, Spinner } from '../components/common'
import { current, runSecrets, useWizard } from './state'

interface Slot {
  id: string
  title: string
  kind: 'field' | 'text' | 'uri'
  help: string
  required: boolean
}

interface LibraryTemplate {
  id: string
  title: string
  description: string
  slots: Slot[]
}

interface Preview {
  item_id: string
  status: number
  jsonld: string
  triples: [string, string, string][]
  triple_count: number
  dropped: string[]
  problems: string[]
}

type Context = Record<string, string>

function contextPairs(context: Record<string, unknown>[] | null | undefined): [string, string][] {
  const out: [string, string][] = []
  for (const entry of context ?? []) for (const [k, v] of Object.entries(entry)) if (typeof v === 'string') out.push([k, v])
  return out
}

export function LinkedDataPanel({ settings }: { settings: PublicSettings }) {
  const { state, dispatch } = useWizard()
  const r = state.form.resource
  const [library, setLibrary] = useState<LibraryTemplate[] | null>(null)
  const [preview, setPreview] = useState<SandboxResult<Preview> | null>(null)
  const [busy, setBusy] = useState(false)
  const [contextRows, setContextRows] = useState<[string, string][]>(() => contextPairs(r.linked_data_context))

  useEffect(() => {
    api
      .GET('/api/jsonld/templates')
      .then((res) => setLibrary(unwrap(res) as unknown as LibraryTemplate[]))
      .catch(() => setLibrary([]))
  }, [])

  const target = settings.targets.find((t) => t.id === state.target)
  const choice = r.jsonld_template ?? null
  const template = library?.find((t) => t.id === choice?.id) ?? null
  const fields = Object.keys(state.probe?.ok ? (state.probe.result?.fields ?? {}) : {})
  const uriField = current(state)?.values?.uri_field
  const setChoice = (next: { id: string; slots: Record<string, string> } | null) => {
    dispatch({ type: 'resource', patch: { jsonld_template: next } })
    setPreview(null)
  }
  const setSlot = (id: string, value: string) => {
    if (!choice) return
    const slots = Object.fromEntries(Object.entries(choice.slots ?? {}).filter(([k]) => k !== id))
    if (value) slots[id] = value
    setChoice({ id: choice.id, slots })
  }
  const setContext = (rows: [string, string][]) => {
    setContextRows(rows)
    const entry: Context = {}
    for (const [k, v] of rows) if (k.trim() && v.trim()) entry[k.trim()] = v.trim()
    dispatch({ type: 'resource', patch: { linked_data_context: Object.keys(entry).length ? [entry] : null } })
    setPreview(null)
  }

  const run = async () => {
    setBusy(true)
    try {
      const job = unwrap(await api.POST('/api/jsonld/preview', { body: { form: state.form, test_secrets: runSecrets(state) } }))
      setPreview(await waitForJob<SandboxResult<Preview>>(job.job_id))
    } catch (e) {
      setPreview({ ok: false, explanation: { code: 'client', message: String(e) } })
    } finally {
      setBusy(false)
    }
  }

  const p = preview?.ok ? preview.result : null
  return (
    <fieldset className="linked-data">
      <legend>Linked data (JSON-LD)</legend>
      <p className="help">
        pygeoapi serves each item as JSON-LD with <code>?f=jsonld</code>, which search engines and the Geoconnex knowledge graph read.
        {uriField ? (
          <>
            {' '}
            Each item's <code>@id</code> comes from <code>{String(uriField)}</code>.
          </>
        ) : (
          <> Without a URI field (Map fields), each item's @id is its pygeoapi URL.</>
        )}
      </p>
      <label htmlFor="jsonld-template">JSON-LD for each item</label>
      <select
        id="jsonld-template"
        value={choice?.id ?? ''}
        onChange={(e) => setChoice(e.target.value ? { id: e.target.value, slots: {} } : null)}
      >
        <option value="">pygeoapi's default (each property as is)</option>
        {(library ?? []).map((t) => (
          <option key={t.id} value={t.id}>
            {t.title}
          </option>
        ))}
      </select>
      {template && <p className="help">{template.description}</p>}
      {template && (
        <div className="slots">
          {template.slots.map((s) => {
            const value = choice?.slots?.[s.id] ?? ''
            const id = `jsonld-slot-${s.id}`
            return (
              <div className="field" key={s.id}>
                <label htmlFor={id}>
                  {s.title}
                  {s.required ? <span aria-hidden="true"> *</span> : null}
                </label>
                {s.kind === 'field' && fields.length ? (
                  <select id={id} value={value} onChange={(e) => setSlot(s.id, e.target.value)}>
                    <option value="">{s.required ? 'Choose…' : '(none)'}</option>
                    {fields.map((f) => (
                      <option key={f}>{f}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={id}
                    className={s.kind === 'text' ? undefined : 'mono'}
                    type={s.kind === 'uri' ? 'url' : 'text'}
                    value={value}
                    placeholder={s.kind === 'uri' ? 'https://geoconnex.us/ref/…' : ''}
                    onChange={(e) => setSlot(s.id, e.target.value)}
                  />
                )}
                {s.help && <p className="help">{s.help}</p>}
              </div>
            )
          })}
          {target?.templates_path ? (
            <p className="help">
              Publishing writes it to <code>{`${target.templates_path}/${state.form.resource_id || '<id>'}/collections/items/item.jsonld`}</code> on the
              server and points the collection's <code>templates.path</code> there.
            </p>
          ) : (
            <div className="field">
              <label htmlFor="templates-path">Templates folder on the server</label>
              <input
                id="templates-path"
                className="mono"
                value={r.templates_path ?? ''}
                placeholder="/srv/pygeoapi/templates/my-collection"
                onChange={(e) => dispatch({ type: 'resource', patch: { templates_path: e.target.value || null } })}
              />
              <p className="help">This target doesn't write templates: the export gives you item.jsonld to save in that folder's collections/items/.</p>
            </div>
          )}
        </div>
      )}
      <details>
        <summary>Context terms ({contextRows.length})</summary>
        <p className="help">
          Extra prefixes and terms for the collection's <code>linked-data</code> context, such as hyf →
          https://www.opengis.net/def/schema/hy_features/hyf/.
        </p>
        {contextRows.map(([k, v], i) => (
          <div className="inline" key={i}>
            <input aria-label={`Context term ${i + 1}`} className="mono" value={k} placeholder="hyf" onChange={(e) => setContext(contextRows.map((row, j) => (j === i ? [e.target.value, row[1]] : row)))} />
            <input aria-label={`Context IRI ${i + 1}`} className="mono grow" value={v} placeholder="https://…" onChange={(e) => setContext(contextRows.map((row, j) => (j === i ? [row[0], e.target.value] : row)))} />
            <button type="button" className="small" aria-label={`Remove context term ${i + 1}`} onClick={() => setContext(contextRows.filter((_, j) => j !== i))}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" className="small" onClick={() => setContextRows([...contextRows, ['', '']])}>
          Add a term
        </button>
      </details>
      <p>
        <button type="button" onClick={() => void run()} disabled={busy || !state.probe?.ok}>
          Preview an item as RDF
        </button>
        {!state.probe?.ok && <span className="muted small"> (after Test and inspect)</span>}
      </p>
      {busy && <Spinner label="Rendering an item with pygeoapi in the sandbox…" />}
      {preview && !preview.ok && !busy && <ExplainedError explanation={preview.explanation} error={preview.error} logs={preview.logs} />}
      {p && !busy && (
        <div className="jsonld-preview">
          {p.problems.length > 0 ? (
            <div className="notice warning">
              <ul>
                {p.problems.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="notice ok" role="status">
              Item <code>{p.item_id}</code> gives {p.triple_count} triple(s).
            </p>
          )}
          {p.dropped.length > 0 && (
            <p className="notice warning">
              No context defines {p.dropped.length === 1 ? 'this key' : 'these keys'}, so RDF readers drop {p.dropped.length === 1 ? 'it' : 'them'}:{' '}
              {p.dropped.map((k) => (
                <code key={k} className="chip">
                  {k}
                </code>
              ))}
              . Map them in a template, or add terms for them.
            </p>
          )}
          {p.triples.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Triples">
              <table className="compact triples">
                <caption>
                  Triples{p.triple_count > p.triples.length ? ` (the first ${p.triples.length} of ${p.triple_count})` : ''}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Subject</th>
                    <th scope="col">Predicate</th>
                    <th scope="col">Object</th>
                  </tr>
                </thead>
                <tbody>
                  {p.triples.map(([s, pr, o], i) => (
                    <tr key={i}>
                      <td className="mono small">{s}</td>
                      <td className="mono small">{pr}</td>
                      <td className="mono small">{o}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary>The JSON-LD</summary>
            <pre className="mono small">{p.jsonld}</pre>
          </details>
        </div>
      )}
    </fieldset>
  )
}
