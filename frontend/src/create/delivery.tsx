// J6 (PLAN.md §7.8): deliver a saved version — a zip, a test image, or a push to
// a Git remote — and see which collections use the provider (ADR-0013).
import { useEffect, useState } from 'react'

import { waitForJob } from '../api/client'
import type { ImageResult, PushResult, UsageResult } from '../api/types'
import { CopyButton, Spinner } from '../components/common'

type Start = () => Promise<{ job_id: string }>

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string' ? detail : fallback
}

/** A download link for a version's package; the gate (passing checks) is the server's. */
export function ZipLink({ href, version, disabled }: { href: string; version: string; disabled?: boolean }) {
  if (disabled) {
    return (
      <button type="button" disabled>
        Download version {version} (.zip)
      </button>
    )
  }
  return (
    <a className="button" href={href} download>
      Download version {version} (.zip)
    </a>
  )
}

export function ImagePanel({ start, version, disabled }: { start: Start; version: string; disabled?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ImageResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const build = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const job = await start()
      setResult(await waitForJob<ImageResult>(job.job_id))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="delivery-image">
      <p className="muted small">
        A <code>geopython/pygeoapi</code> image with this version installed, built by Docker with no secrets, then checked offline: the provider must import.
      </p>
      <p>
        <button type="button" onClick={() => void build()} disabled={busy || disabled}>
          Build a test image of version {version}
        </button>
      </p>
      {busy && <Spinner label="Building the image (the first build downloads packages)…" />}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {result && result.ok && (
        <div className="notice ok" role="status">
          <p>
            <strong>Built {result.tag}</strong>
            {result.size ? ` (${(result.size / 1e6).toFixed(0)} MB)` : ''}. The smoke test passed: <code>{(result.smoke ?? '').trim()}</code>
          </p>
          {result.run && (
            <>
              <p className="small">Try it with a pygeoapi config that holds the example resource:</p>
              <pre className="code-block">{result.run}</pre>
              <CopyButton text={result.run} label="Copy the command" />
            </>
          )}
        </div>
      )}
      {result && !result.ok && (
        <div className="notice error" role="alert">
          <p>
            <strong>{result.stage === 'build' ? 'The image did not build.' : 'The image built, but the provider did not load in it.'}</strong>
          </p>
          <pre className="code-block">{result.stage === 'build' ? result.log : result.smoke}</pre>
        </div>
      )}
    </div>
  )
}

export function GitPanel({ push, idPrefix }: { push: (remote: string, branch: string) => Promise<{ data?: unknown; error?: unknown }>; idPrefix: string }) {
  const [remote, setRemote] = useState('')
  const [branch, setBranch] = useState('main')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<PushResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    const res = await push(remote.trim(), branch.trim())
    setBusy(false)
    if (res.error) setError(detailOf(res.error, 'The push failed.'))
    else setResult(res.data as PushResult)
  }

  return (
    <div className="delivery-git">
      <p className="muted small">
        Pushes this provider's repository — the current commit as a branch, with its version tags — for review on the way to production. Git uses your own credentials
        (its credential helper or SSH), never a password in the URL.
      </p>
      <div className="inline-fields">
        <label htmlFor={`${idPrefix}-remote`}>
          Git remote URL
          <input id={`${idPrefix}-remote`} value={remote} placeholder="git@github.com:org/providers.git" onChange={(e) => setRemote(e.target.value)} />
        </label>
        <label htmlFor={`${idPrefix}-branch`}>
          Branch
          <input id={`${idPrefix}-branch`} value={branch} onChange={(e) => setBranch(e.target.value)} />
        </label>
      </div>
      <p>
        <button type="button" onClick={() => void send()} disabled={busy || !remote.trim() || !branch.trim()}>
          Push to Git
        </button>
      </p>
      {busy && <Spinner label="Pushing…" />}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <p className="notice ok" role="status">
          Pushed {result.commit.slice(0, 10)} to {result.branch} on {result.remote}
          {result.tags.length ? `, with ${result.tags.join(', ')}` : ''}.{' '}
          {result.compare_url && (
            <a href={result.compare_url} target="_blank" rel="noreferrer">
              Open a pull request
            </a>
          )}
        </p>
      )}
    </div>
  )
}

export function UsagePanel({ load }: { load: () => Promise<{ data?: unknown; error?: unknown }> }) {
  const [usage, setUsage] = useState<UsageResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    load()
      .then((res) => {
        if (!live) return
        if (res.error) setError(detailOf(res.error, 'Usage could not be read.'))
        else setUsage(res.data as UsageResult)
      })
      .catch((e: unknown) => live && setError(String(e)))
    return () => {
      live = false
    }
  }, [load])
  if (error) return <p className="notice error">{error}</p>
  if (!usage) return <Spinner label="Reading the targets' configs…" />
  return (
    <>
      {usage.collections.length ? (
        <ul>
          {usage.collections.map((c) => (
            <li key={`${c.target}:${c.resource_id}`}>
              <a href={`#/configure/edit?target=${encodeURIComponent(c.target)}&id=${encodeURIComponent(c.resource_id)}`}>{c.title || c.resource_id}</a>{' '}
              <span className="muted small">
                ({c.resource_id}) on {c.target_title} · {c.version ? `version ${c.version}` : c.via === 'package' ? 'the installed package' : 'any version'}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No collection in the targets' configs uses it.</p>
      )}
      {usage.errors.map((e) => (
        <p key={e.target} className="notice warning small">
          {e.target}: {e.error}
        </p>
      ))}
      {usage.history.length > 0 && (
        <details>
          <summary>Publish history</summary>
          <ul className="small">
            {usage.history.map((h) => (
              <li key={h.id}>
                {h.time} · {h.action} {h.resource_id} on {h.target} · {h.providers.join(', ')} · {h.status}
                {h.rolled_back_by ? ' (rolled back)' : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}
