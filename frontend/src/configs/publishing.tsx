// Publishing to file and Admin API targets (PLAN.md §6.4): the plan's diff,
// the job's steps, and one-click rollback. Shared by the wizard and J2.
import { useState } from 'react'

import { api, unwrap, waitForJob, ApiError } from '../api/client'
import type { LogEntry, PublishPlan, PublishResult, PublishStepResult } from '../api/types'
import { Spinner } from '../components/common'

export type PublishBody = {
  target: string
  action: 'add' | 'update' | 'delete'
  resource_id: string
  form?: unknown
  etag?: string | null
  probes?: unknown[]
  test_secrets?: Record<string, string>
  acknowledge?: string[]
}

/** A 409 from the backend: the config changed on disk since it was loaded. */
export class ConflictError extends ApiError {
  constructor(
    message: string,
    readonly diff: string | null,
  ) {
    super(message)
  }
}

export async function planPublish(body: PublishBody): Promise<PublishPlan> {
  const res = await fetch('/api/publish/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Provider-Builder': '1' },
    body: JSON.stringify(body),
  })
  const data = (await res.json()) as PublishPlan & { detail?: string; diff?: string | null }
  if (res.status === 409) throw new ConflictError(data.detail ?? 'The config changed on disk.', data.diff ?? null)
  if (!res.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`)
  return data
}

export async function runPublish(body: PublishBody): Promise<PublishResult> {
  const job = unwrap(await api.POST('/api/publish', { body: body as never }))
  return waitForJob<PublishResult>(job.job_id)
}

export async function runRollback(entryId: string): Promise<PublishResult> {
  const job = unwrap(await api.POST('/api/publish/{entry_id}/rollback', { params: { path: { entry_id: entryId } } }))
  return waitForJob<PublishResult>(job.job_id)
}

export async function publishLog(target?: string, resourceId?: string): Promise<LogEntry[]> {
  const r = await api.GET('/api/publish/log', { params: { query: { target: target ?? null, resource_id: resourceId ?? null } } })
  return unwrap(r) as unknown as LogEntry[]
}

export function DiffView({ diff, label }: { diff: string; label: string }) {
  const lines = diff.split('\n').filter((l, i, all) => l || i < all.length - 1)
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <pre className="diff">
        {lines.map((line, i) => {
          const kind = line.startsWith('+++') || line.startsWith('---') ? 'file' : line[0] === '+' ? 'add' : line[0] === '-' ? 'del' : line.startsWith('@@') ? 'hunk' : 'ctx'
          return (
            <span key={i} className={`diff-${kind}`}>
              {kind === 'add' && <span className="sr-only">Added: </span>}
              {kind === 'del' && <span className="sr-only">Removed: </span>}
              {line}
              {'\n'}
            </span>
          )
        })}
      </pre>
    </div>
  )
}

const STEP_TITLES: Record<string, string> = {
  plan: 'Plan',
  validate: 'Validate',
  guard: 'Admin API checks',
  openapi: 'Generate OpenAPI',
  write: 'Write',
  restore: 'Restore the backup',
  reload: 'Reload',
  verify: 'Verify',
  'admin-read': 'Re-read through the Admin API',
}

function Checks({ detail }: { detail: unknown }) {
  if (!Array.isArray(detail)) return null
  const checks = detail as { url?: string; status?: number | null; ok?: boolean; detail?: string | null }[]
  if (!checks.every((c) => typeof c?.url === 'string')) return null
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.url} className={c.ok ? 'ok' : 'error'}>
          {c.ok ? '✓' : '✗'}{' '}
          <a href={(c.url ?? '').replace('?f=json', '').replace('f=json&', '')} target="_blank" rel="noreferrer">
            {c.url}
          </a>{' '}
          <span className="muted">
            {c.status ?? ''} {c.detail ?? ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function StepList({ steps }: { steps: PublishStepResult[] }) {
  return (
    <ol className="publish-steps">
      {steps.map((s, i) => (
        <li key={`${s.name}-${i}`} className={s.ok ? 'ok' : 'error'}>
          <strong>
            {s.ok ? '✓' : '✗'} {STEP_TITLES[s.name] ?? s.name}
          </strong>{' '}
          <span>{s.message}</span>
          {s.name === 'verify' && <Checks detail={s.detail} />}
          {!s.ok && typeof s.detail === 'string' && s.detail.includes('\n@@') && <DiffView diff={s.detail} label="What changed on disk" />}
        </li>
      ))}
    </ol>
  )
}

/** A publish's outcome, with a rollback button while it can still be undone. */
export function PublishOutcome({
  result,
  serverUrl,
  onChanged,
}: {
  result: PublishResult
  serverUrl?: string | null
  onChanged?: () => void
}) {
  const [undo, setUndo] = useState<PublishResult | 'running' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const entry = result.entry
  const rollback = async () => {
    if (!entry) return
    setUndo('running')
    setError(null)
    try {
      setUndo(await runRollback(entry.id))
      onChanged?.()
    } catch (e) {
      setUndo(null)
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const nothing = result.ok && !entry
  const base = serverUrl?.replace(/\/$/, '')
  return (
    <>
      <div className={`notice ${result.ok ? 'ok' : 'error'}`} role="status">
        <p>
          <strong>
            {nothing
              ? 'Nothing to publish: the config already says this.'
              : result.ok
                ? entry?.status === 'written'
                  ? 'Written to the config.'
                  : 'Published. The server serves the change.'
                : entry
                  ? 'Written, but the server did not serve the change.'
                  : 'Not published. Nothing was written.'}
          </strong>
        </p>
      </div>
      <StepList steps={result.steps} />
      {entry && entry.action !== 'delete' && base && result.ok && (
        <p>
          <a href={`${base}/collections/${entry.resource_id}`} target="_blank" rel="noreferrer">
            Open {base}/collections/{entry.resource_id}
          </a>
        </p>
      )}
      {entry && entry.action !== 'rollback' && (
        <section aria-label="Roll back">
          {undo === null && (
            <p className="actions">
              <button type="button" className={result.ok ? '' : 'primary'} onClick={() => void rollback()}>
                Roll back this publish
              </button>
              <span className="muted small">Restores the config from the backup taken before it, then reloads and verifies.</span>
            </p>
          )}
          {undo === 'running' && <Spinner label="Rolling back…" />}
          {undo && undo !== 'running' && (
            <>
              <h3>Rollback</h3>
              <p className={undo.ok ? 'ok-text' : 'error-text'} role="status">
                {undo.ok ? 'Rolled back: the previous config is back and served.' : 'The rollback did not complete.'}
              </p>
              <StepList steps={undo.steps} />
            </>
          )}
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </section>
      )}
    </>
  )
}
