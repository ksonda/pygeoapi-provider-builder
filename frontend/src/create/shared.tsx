// Pieces every template wizard shares (PLAN.md §7.4 steps 5 and 6): field
// errors, spec problems, the contract checklist and saving a version.
import { useCallback, useEffect, useState } from 'react'

import { api, unwrap, waitForJob } from '../api/client'
import type { CheckStatus, HarnessData, ModeBResult, Problem, SavedProvider, SpecCheck } from '../api/types'
import { ExplainedError, Spinner, YamlView } from '../components/common'

export interface CreateStepProps {
  errors: Record<string, string>
  showErrors: boolean
}

export const ENV_RE = /^[A-Z][A-Z0-9_]*$/
export const ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
export const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$/

export interface Meta {
  id: string
  title: string
  description: string
  version: string
  changes: string
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
}

/** A meta patch, with the id following the title until the steward edits the id. */
export function nextMeta(meta: Meta, patch: Partial<Meta>, idEdited: boolean): Meta {
  const out = { ...meta, ...patch }
  if (patch.title !== undefined && !idEdited) out.id = slugify(patch.title)
  return out
}

export function metaErrors(meta: Meta): Record<string, string> {
  const out: Record<string, string> = {}
  if (!meta.title.trim()) out.title = 'Title is required.'
  if (!ID_RE.test(meta.id)) out['provider-id'] = 'Provider id: lower-case letters, digits and hyphens.'
  if (!SEMVER.test(meta.version)) out.version = 'Version: a semantic version such as 1.0.0.'
  return out
}

export function FieldError({ id, errors, show }: { id: string; errors: Record<string, string>; show: boolean }) {
  if (!show || !errors[id]) return null
  return (
    <p className="field-error" id={`${id}-error`}>
      {errors[id]}
    </p>
  )
}

export function described(id: string, errors: Record<string, string>, show: boolean, help?: string) {
  const ids = [help ? `${id}-help` : null, show && errors[id] ? `${id}-error` : null].filter(Boolean)
  return { 'aria-describedby': ids.length ? ids.join(' ') : undefined, 'aria-invalid': show && errors[id] ? true : undefined }
}

export function Problems({ problems }: { problems?: Problem[] }) {
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

/** A failed sandbox call: spec problems, or an explained error. */
export function Failure({ result }: { result: Pick<ModeBResult<unknown>, 'problems' | 'explanation' | 'error' | 'logs'> }) {
  return result.problems?.length ? <Problems problems={result.problems} /> : <ExplainedError explanation={result.explanation} error={result.error} logs={result.logs} />
}

// ------------------------------------------------------------------- check

const STATUS_TEXT: Record<CheckStatus, string> = { pass: 'Pass', fail: 'Fail', warn: 'Warning', skip: 'Skipped', unsupported: 'Not supported' }
const LEVELS: [string, string][] = [
  ['L1', 'The provider itself'],
  ['L2', "pygeoapi's API, in-process"],
  ['L3', 'A live pygeoapi server'],
]

interface HarnessProps extends CreateStepProps {
  spec: object
  secrets: Record<string, string>
  result: ModeBResult<HarnessData> | null
  onResult: (r: ModeBResult<HarnessData> | null) => void
}

export function HarnessPanel({ spec, secrets, result, onResult, errors, showErrors }: HarnessProps) {
  const [busy, setBusy] = useState(false)
  const specKey = JSON.stringify(spec)
  const secretsKey = JSON.stringify(secrets)
  const run = useCallback(async () => {
    setBusy(true)
    try {
      const job = unwrap(
        await api.POST('/api/specs/harness', {
          body: { spec: JSON.parse(specKey) as Record<string, unknown>, test_secrets: JSON.parse(secretsKey) as Record<string, string>, levels: ['L1', 'L2', 'L3'] },
        }),
      )
      onResult(await waitForJob<ModeBResult<HarnessData>>(job.job_id))
    } catch (e) {
      onResult({ ok: false, explanation: { code: 'client', message: String(e) } })
    } finally {
      setBusy(false)
    }
  }, [specKey, secretsKey, onResult])

  useEffect(() => {
    if (result) return
    const t = setTimeout(() => void run(), 0)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const data = result?.ok ? result.result : null
  return (
    <>
      <p>The contract checks run in the sandbox: the provider itself, pygeoapi's API in-process, then a live pygeoapi server.</p>
      {busy && <Spinner label="Running the contract checks…" />}
      <FieldError id="harness" errors={errors} show={showErrors} />
      {result && !result.ok && !busy && <Failure result={result} />}
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

interface SaveProps extends CreateStepProps {
  spec: object
  harness: HarnessData | null
  meta: Meta
  onMeta: (patch: Partial<Meta>, idEdited?: boolean) => void
  saved: SavedProvider | null
  onSaved: (s: SavedProvider) => void
}

export function SavePanel({ spec, harness, meta, onMeta, saved, onSaved, errors, showErrors }: SaveProps) {
  const [check, setCheck] = useState<SpecCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
        body: { spec: JSON.parse(specKey) as Record<string, unknown>, version: meta.version, changes: meta.changes, harness: (harness as unknown as Record<string, unknown>) ?? null },
      })
      if (res.error) {
        const detail = res.error as { detail?: string; problems?: SpecCheck['problems'] }
        setError(detail.detail ?? 'Saving failed.')
        if (detail.problems?.length) setCheck((c) => (c ? { ...c, ok: false, problems: detail.problems ?? [] } : c))
      } else {
        onSaved(res.data as unknown as SavedProvider)
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
      <input id="title" value={meta.title} onChange={(e) => onMeta({ title: e.target.value })} {...described('title', errors, showErrors)} />
      <FieldError id="title" errors={errors} show={showErrors} />
      <label htmlFor="provider-id">
        Provider id <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="provider-id-help">
        Lower-case letters, digits and hyphens. It names the workspace folder and the catalog entry.
      </p>
      <input id="provider-id" value={meta.id} onChange={(e) => onMeta({ id: e.target.value }, true)} {...described('provider-id', errors, showErrors, 'help')} />
      <FieldError id="provider-id" errors={errors} show={showErrors} />
      <label htmlFor="description">Description</label>
      <textarea id="description" rows={2} value={meta.description} onChange={(e) => onMeta({ description: e.target.value })} />
      <div className="inline-fields">
        <label>
          Version
          <input id="version" value={meta.version} onChange={(e) => onMeta({ version: e.target.value })} {...described('version', errors, showErrors)} />
        </label>
        <label>
          What changed
          <input value={meta.changes} placeholder="First version." onChange={(e) => onMeta({ changes: e.target.value })} />
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
      {!saved && (
        <p>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !check?.ok}>
            Save version {meta.version}
          </button>
        </p>
      )}
      {saved && (
        <div className="notice ok" role="status">
          <p>
            <strong>
              Saved {saved.id} {saved.version}.
            </strong>{' '}
            The spec is at <code>{saved.data}</code> as the pygeoapi server sees it, and Mode A's catalog lists it.
          </p>
          <p>
            <a className="button primary" href={`#/configure/new?descriptor=${encodeURIComponent(saved.descriptor)}`}>
              Publish a collection with this provider →
            </a>
          </p>
        </div>
      )}
    </>
  )
}

/** The wizard frame's step list and heading, shared by every template. */
export function StepList({ steps, index }: { steps: readonly { id: string; title: string }[]; index: number }) {
  return (
    <nav aria-label="Steps" className="steps">
      <ol>
        {steps.map((s, i) => (
          <li key={s.id} className={i === index ? 'current' : i < index ? 'done' : ''} aria-current={i === index ? 'step' : undefined}>
            <span className="step-num" aria-hidden="true">
              {i < index ? '✓' : i + 1}
            </span>
            <span>{s.title}</span>
          </li>
        ))}
      </ol>
    </nav>
  )
}
