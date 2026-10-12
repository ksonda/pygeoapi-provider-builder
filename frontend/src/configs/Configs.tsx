// J2 (PLAN.md §6.5): a target's collections. Edit, add a provider (J3),
// duplicate, delete, import a YAML snippet, and the publish log with rollback.
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, unwrap } from '../api/client'
import type { ImportedResource, LogEntry, PublishPlan, PublishResult, ResourceSummary, TargetInfo, VerifyResult } from '../api/types'
import { Spinner, YamlView } from '../components/common'
import { handOff } from './handoff'
import { DiffView, planPublish, PublishOutcome, publishLog, runPublish, runRollback, StepList } from './publishing'

function navigate(hash: string): void {
  window.location.hash = hash
}

function Health({ target, id }: { target: string; id: string }) {
  const [h, setH] = useState<VerifyResult | null>(null)
  useEffect(() => {
    let live = true
    api
      .GET('/api/targets/{target_id}/health/{resource_id}', { params: { path: { target_id: target, resource_id: id } } })
      .then((r) => live && setH(unwrap(r) as unknown as VerifyResult))
      .catch(() => live && setH({ ok: false, checks: [], message: 'Not checked' }))
    return () => {
      live = false
    }
  }, [target, id])
  if (!h) return <span className="muted small">checking…</span>
  const failed = h.checks.find((c) => !c.ok)
  return h.ok ? (
    <span className="badge ok">served</span>
  ) : (
    <span className="badge error" title={failed ? `${failed.url}: ${failed.status ?? ''} ${failed.detail ?? ''}` : h.message}>
      {failed?.status ? `HTTP ${failed.status}` : 'not served'}
    </span>
  )
}

function DeletePanel({ target, id, etag, onDone }: { target: TargetInfo; id: string; etag: string; onDone: () => void }) {
  const [plan, setPlan] = useState<PublishPlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PublishResult | 'running' | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
    planPublish({ target: target.id, action: 'delete', resource_id: id, etag })
      .then(setPlan)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [target.id, id, etag])
  const run = async () => {
    setResult('running')
    try {
      setResult(await runPublish({ target: target.id, action: 'delete', resource_id: id, etag }))
    } catch (e) {
      setResult(null)
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <section className="card panel" aria-labelledby="delete-heading">
      <h3 id="delete-heading" ref={heading} tabIndex={-1}>
        Delete {id}
      </h3>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!plan && !error && <Spinner label="Working out the change…" />}
      {plan && result === null && (
        <>
          <p>
            This removes the collection's lines from {target.config}. Nothing else in the file changes. A backup is taken first, so you can roll it back.
          </p>
          <DiffView diff={plan.diff} label={`Changes to ${target.config}`} />
          <p className="actions">
            <button type="button" className="danger" onClick={() => void run()}>
              Delete {id} from {target.title}
            </button>
            <button type="button" onClick={onDone}>
              Cancel
            </button>
          </p>
        </>
      )}
      {result === 'running' && <Spinner label="Deleting: writing the config, reloading and verifying…" />}
      {result && result !== 'running' && (
        <>
          <PublishOutcome result={result} serverUrl={target.server_url} onChanged={onDone} />
          <button type="button" onClick={onDone}>
            Close
          </button>
        </>
      )}
    </section>
  )
}

function ImportPanel({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState('')
  const [found, setFound] = useState<ImportedResource[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const read = async () => {
    setError(null)
    setFound(null)
    try {
      setFound(unwrap(await api.POST('/api/import', { body: { yaml: text } })) as unknown as ImportedResource[])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <section className="card panel" aria-labelledby="import-heading">
      <h3 id="import-heading">Import YAML</h3>
      <p>Paste a resource from a pygeoapi config, a whole config, or one resource's keys. The wizard opens with the form filled in.</p>
      <label htmlFor="import-yaml">YAML</label>
      <textarea id="import-yaml" rows={10} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} className="mono" />
      <p className="actions">
        <button type="button" className="primary" onClick={() => void read()} disabled={!text.trim()}>
          Read the snippet
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {found?.map((r) => (
        <div key={r.resource_id} className="import-result">
          <p>
            <strong>{r.resource_id}</strong>: {r.form.providers.map((p) => (p.descriptor === '_raw' ? 'kept as YAML' : p.descriptor)).join(', ')}
          </p>
          {r.warnings.map((w) => (
            <p key={w} className="notice warning">
              {w}
            </p>
          ))}
          <button type="button" onClick={() => handOff({ form: r.form, intent: 'import' })}>
            Open {r.resource_id} in the wizard
          </button>
        </div>
      ))}
    </section>
  )
}

function Log({ target, refresh, onChanged }: { target: TargetInfo; refresh: number; onChanged: () => void }) {
  const [entries, setEntries] = useState<LogEntry[] | null>(null)
  const [undo, setUndo] = useState<{ id: string; result: PublishResult | 'running' | string } | null>(null)
  useEffect(() => {
    publishLog(target.id)
      .then(setEntries)
      .catch(() => setEntries([]))
  }, [target.id, refresh])
  const rollback = async (id: string) => {
    setUndo({ id, result: 'running' })
    try {
      setUndo({ id, result: await runRollback(id) })
    } catch (e) {
      setUndo({ id, result: e instanceof Error ? e.message : String(e) })
    }
    onChanged()
  }
  if (!entries) return <Spinner label="Reading the publish log…" />
  if (!entries.length) return <p className="muted">Nothing published to this target yet.</p>
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Publish log">
      <table>
        <thead>
          <tr>
            <th scope="col">When (UTC)</th>
            <th scope="col">Change</th>
            <th scope="col">Providers</th>
            <th scope="col">Result</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="nowrap">{e.time.replace('T', ' ').replace('Z', '')}</td>
              <td>
                {e.action} <code>{e.resource_id}</code>
                {e.rolled_back_by && <span className="badge">rolled back</span>}
              </td>
              <td>{e.providers.join(', ')}</td>
              <td>
                <span className={`badge ${e.status === 'published' ? 'ok' : e.status === 'written' ? '' : 'error'}`}>{e.status}</span>
              </td>
              <td>
                {e.action !== 'rollback' && !e.rolled_back_by && (
                  <button type="button" className="small" onClick={() => void rollback(e.id)} disabled={undo?.result === 'running'}>
                    Roll back
                  </button>
                )}
                {undo?.id === e.id && undo.result === 'running' && <Spinner label="Rolling back…" />}
                {undo?.id === e.id && typeof undo.result === 'string' && undo.result !== 'running' && (
                  <p className="error-text" role="alert">
                    {undo.result}
                  </p>
                )}
                {undo?.id === e.id && typeof undo.result === 'object' && <StepList steps={undo.result.steps} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Configs({ initialTarget }: { initialTarget: string | null }) {
  const [targets, setTargets] = useState<TargetInfo[] | null>(null)
  const [targetId, setTargetId] = useState<string | null>(initialTarget)
  const [listing, setListing] = useState<{ etag: string; resources: ResourceSummary[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<{ kind: 'delete' | 'yaml'; id: string } | { kind: 'import' } | null>(null)
  const [yaml, setYaml] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    api
      .GET('/api/targets')
      .then((r) => {
        const all = unwrap(r) as unknown as TargetInfo[]
        setTargets(all)
        setTargetId((t) => t ?? all[0]?.id ?? null)
      })
      .catch((e: unknown) => setError(String(e)))
  }, [])

  const target = targets?.find((t) => t.id === targetId) ?? null
  const reload = useCallback(() => {
    setListing(null)
    setError(null)
    setRefresh((n) => n + 1)
  }, [])

  useEffect(() => {
    if (!targetId) return
    api
      .GET('/api/targets/{target_id}/resources', { params: { path: { target_id: targetId } } })
      .then((r) => setListing(unwrap(r) as unknown as { etag: string; resources: ResourceSummary[] }))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [targetId, refresh])

  const showYaml = async (id: string) => {
    setOpen({ kind: 'yaml', id })
    setYaml(null)
    const r = await api.GET('/api/targets/{target_id}/resources/{resource_id}', { params: { path: { target_id: targetId ?? '', resource_id: id } } })
    setYaml((unwrap(r) as unknown as { yaml: string }).yaml)
  }

  const writable = target && target.kind !== 'export'
  const edit = (id: string, intent: 'edit' | 'provider' | 'duplicate') =>
    navigate(`#/configure/edit?target=${encodeURIComponent(targetId ?? '')}&id=${encodeURIComponent(id)}&intent=${intent}`)

  return (
    <div className="configs">
      <h2>Collections on a server</h2>
      {targets && targets.length > 1 && (
        <p>
          <label htmlFor="cfg-target">Target</label>{' '}
          <select id="cfg-target" value={targetId ?? ''} onChange={(e) => (setTargetId(e.target.value), setOpen(null), setListing(null), setError(null))}>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </p>
      )}
      {target && (
        <p className="muted">
          {target.title}: <code>{target.config}</code>
          {target.kind === 'export' && ' (export only: read, not written)'}
          {target.kind === 'admin' && ' (through the Admin API)'}
          {target.server_url && (
            <>
              {' '}
              ·{' '}
              <a href={target.server_url} target="_blank" rel="noreferrer">
                {target.server_url}
              </a>
            </>
          )}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {writable && (
        <p className="actions">
          <button type="button" onClick={() => setOpen({ kind: 'import' })}>
            Import YAML
          </button>
          <a className="button" href="#/configure/new">
            Create a collection
          </a>
        </p>
      )}
      {open?.kind === 'import' && <ImportPanel onClose={() => setOpen(null)} />}
      {!listing && !error && <Spinner label="Reading the config…" />}
      {listing && target && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Collections">
          <table>
            <thead>
              <tr>
                <th scope="col">Collection</th>
                <th scope="col">Providers</th>
                <th scope="col">Health</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {listing.resources.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.title || r.id}</strong>
                    <br />
                    <code className="small">{r.id}</code>
                    {r.type !== 'collection' && <span className="badge">{r.type}</span>}
                  </td>
                  <td>
                    <ul className="plain">
                      {r.providers.map((p, i) => (
                        <li key={i}>
                          <span className="badge">{p.type}</span> {p.name}
                          {p.version && <span className="muted small"> {p.version}</span>}
                          {i === 0 && r.providers.length > 1 && <span className="muted small"> (default)</span>}
                        </li>
                      ))}
                    </ul>
                  </td>
                  <td>{r.type === 'collection' && target.server_url ? <Health key={`${r.id}:${refresh}`} target={target.id} id={r.id} /> : '—'}</td>
                  <td className="row-actions">
                    {writable && r.editable && (
                      <>
                        <button type="button" className="small" onClick={() => edit(r.id, 'edit')} aria-label={`Edit ${r.id}`}>
                          Edit
                        </button>
                        <button type="button" className="small" onClick={() => edit(r.id, 'provider')} aria-label={`Add a provider to ${r.id}`}>
                          Add provider
                        </button>
                      </>
                    )}
                    {writable && r.editable && (
                      <button type="button" className="small" onClick={() => edit(r.id, 'duplicate')} aria-label={`Duplicate ${r.id}`}>
                        Duplicate
                      </button>
                    )}
                    <button type="button" className="small" onClick={() => void showYaml(r.id)} aria-label={`Show the YAML of ${r.id}`}>
                      YAML
                    </button>
                    {writable && (
                      <button type="button" className="small danger" onClick={() => setOpen({ kind: 'delete', id: r.id })} aria-label={`Delete ${r.id}`}>
                        Delete
                      </button>
                    )}
                    {r.warnings.map((w) => (
                      <p key={w} className="muted small">
                        {w}
                      </p>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open?.kind === 'yaml' && (
        <section className="card panel" aria-label={`YAML of ${open.id}`}>
          {yaml === null ? <Spinner label="Loading…" /> : <YamlView text={yaml} label={`YAML of ${open.id}`} />}
          <button type="button" onClick={() => setOpen(null)}>
            Close
          </button>
        </section>
      )}
      {open?.kind === 'delete' && listing && target && (
        <DeletePanel key={open.id} target={target} id={open.id} etag={listing.etag} onDone={() => (setOpen(null), reload())} />
      )}
      {target && target.kind !== 'export' && (
        <>
          <h2>Publish log</h2>
          <p className="muted small">Every publish and rollback to this target. A rollback restores the backup taken before a publish.</p>
          <Log target={target} refresh={refresh} onChanged={reload} />
        </>
      )}
    </div>
  )
}
