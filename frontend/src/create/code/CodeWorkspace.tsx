// J5, the code workspace (PLAN.md §7.5): a code-built provider's files in a
// CodeMirror editor, ruff lint and format from the backend, the contract checks
// in the sandbox, and versions as Git tags. The backend never runs this code.
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, unwrap } from '../../api/client'
import type { CodeDetail, Diagnostic, HarnessData, ModeBResult } from '../../api/types'
import { CodeEditor, type CodeLanguage } from '../../components/CodeEditor'
import { Spinner } from '../../components/common'
import { GitPanel, ImagePanel, UsagePanel, ZipLink } from '../delivery'
import { HarnessPanel, SEMVER } from '../shared'

function languageOf(path: string): CodeLanguage {
  if (path.endsWith('.py')) return 'python'
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return 'yaml'
  return 'text'
}

function detailError(res: { error?: unknown }): string {
  const detail = (res.error as { detail?: unknown } | undefined)?.detail
  return typeof detail === 'string' ? detail : 'The request failed.'
}

export function CodeWorkspace({ id }: { id: string }) {
  const [detail, setDetail] = useState<CodeDetail | null>(null)
  const [path, setPath] = useState('')
  const [saved, setSaved] = useState('')
  const [content, setContent] = useState('')
  const [diagnostics, setDiagnostics] = useState<Diagnostic[] | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'error' | 'info'; text: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [harness, setHarness] = useState<ModeBResult<HarnessData> | null>(null)
  const [newFile, setNewFile] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [changes, setChanges] = useState('')
  const [chosen, setChosen] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const dirty = content !== saved

  const load = useCallback(async () => {
    const res = await api.GET('/api/code/{provider_id}', { params: { path: { provider_id: id } } })
    if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
    const d = res.data as unknown as CodeDetail
    setDetail(d)
    return d
  }, [id])

  const open = useCallback(
    async (file: string) => {
      const res = await api.GET('/api/code/{provider_id}/files/{path}', { params: { path: { provider_id: id, path: file } } })
      if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
      const text = (res.data as unknown as { content: string }).content
      setPath(file)
      setSaved(text)
      setContent(text)
      setDiagnostics(null)
    },
    [id],
  )

  useEffect(() => {
    heading.current?.focus()
    const t = setTimeout(() => {
      void load().then((d) => {
        if (!d) return
        const provider = d.files.find((f) => f.endsWith('/provider.py')) ?? d.files[0]
        if (provider) void open(provider)
        const latest = d.versions[0]
        if (latest) {
          const [major, minor] = latest.split('.').map(Number)
          setVersion(`${major ?? 0}.${(minor ?? 0) + 1}.0`)
        }
      })
    }, 0)
    return () => clearTimeout(t)
  }, [load, open])

  const pick = (file: string) => {
    if (dirty && !window.confirm(`${path} has unsaved changes. Discard them?`)) return
    void open(file)
  }

  const save = async () => {
    setBusy('save')
    const res = await api.PUT('/api/code/{provider_id}/files/{path}', { params: { path: { provider_id: id, path } }, body: { content } })
    setBusy(null)
    if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
    setSaved(content)
    setHarness(null)
    setMessage({ kind: 'ok', text: `Saved ${path}.` })
    void load()
  }

  const lint = async () => {
    setBusy('lint')
    try {
      const out = unwrap(await api.POST('/api/code/{provider_id}/lint', { params: { path: { provider_id: id } }, body: { path, content } }))
      setDiagnostics(out as unknown as Diagnostic[])
    } catch (e) {
      setMessage({ kind: 'error', text: String(e) })
    } finally {
      setBusy(null)
    }
  }

  const format = async () => {
    setBusy('format')
    try {
      const out = unwrap(await api.POST('/api/code/{provider_id}/format', { params: { path: { provider_id: id } }, body: { path, content } }))
      setContent((out as unknown as { content: string }).content)
      setMessage({ kind: 'info', text: 'Formatted with ruff. Save the file to keep it.' })
    } catch (e) {
      setMessage({ kind: 'error', text: String(e) })
    } finally {
      setBusy(null)
    }
  }

  const startHarness = useCallback(
    async () => unwrap(await api.POST('/api/code/{provider_id}/harness', { params: { path: { provider_id: id } }, body: { test_secrets: secrets, levels: ['L1', 'L2', 'L3'] } })),
    [id, secrets],
  )

  const addFile = async () => {
    const file = newFile.trim().replace(/^\/+/, '')
    if (!file) return
    const res = await api.PUT('/api/code/{provider_id}/files/{path}', { params: { path: { provider_id: id, path: file } }, body: { content: '' } })
    if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
    setNewFile('')
    await load()
    void open(file)
  }

  const saveVersion = async () => {
    setBusy('version')
    const res = await api.POST('/api/code/{provider_id}/versions', {
      params: { path: { provider_id: id } },
      body: { version, changes, harness: (harness?.result as unknown as Record<string, unknown>) ?? null },
    })
    setBusy(null)
    if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
    setMessage({ kind: 'ok', text: `Saved version ${version} (Git tag v${version}).` })
    void load()
  }

  const pull = async () => {
    setBusy('pull')
    const res = await api.POST('/api/code/{provider_id}/pull', { params: { path: { provider_id: id } } })
    setBusy(null)
    if (res.error) return setMessage({ kind: 'error', text: detailError(res) })
    setMessage({ kind: 'ok', text: 'Pulled from the Git remote.' })
    void load().then(() => {
      if (path) void open(path)
    })
  }

  const passed = Boolean(harness?.ok && harness.result?.ok)
  const deliverVersion = chosen || detail?.versions[0] || ''
  const startImage = useCallback(
    async () =>
      unwrap(await api.POST('/api/code/{provider_id}/versions/{version}/image', { params: { path: { provider_id: id, version: deliverVersion } } })),
    [id, deliverVersion],
  )
  const push = useCallback(
    (remote: string, branch: string) => api.POST('/api/code/{provider_id}/push', { params: { path: { provider_id: id } }, body: { remote, branch } }),
    [id],
  )
  const usage = useCallback(() => api.GET('/api/code/{provider_id}/usage', { params: { path: { provider_id: id } } }), [id])
  return (
    <div className="code-workspace">
      <h1 ref={heading} tabIndex={-1}>
        {detail?.title ?? id}
        <span className="muted small"> · a code-built {detail?.provider_type ?? ''} provider</span>
      </h1>
      {detail && (
        <p className="muted small">
          <code>{detail.plugin}</code> · {detail.versions.length ? `versions ${detail.versions.join(', ')}` : 'no version saved yet'}
          {detail.changed.length ? ` · ${detail.changed.length} changed file(s) since the last version` : ''}
          {detail.remote ? ` · Git remote ${detail.remote}` : ''}
        </p>
      )}
      {detail?.ejected_from && (
        <p className="notice info">
          Ejected from the spec <code>{detail.ejected_from.spec}</code>, version {detail.ejected_from.version}, on {detail.ejected_from.date}. The spec's settings are
          the constants at the top of <code>provider.py</code>; nothing reads the spec any more.
        </p>
      )}
      {message && (
        <p className={`notice ${message.kind}`} role={message.kind === 'error' ? 'alert' : 'status'}>
          {message.text}
        </p>
      )}
      <div className="code-layout">
        <nav aria-label="Files" className="code-files">
          <h2 className="h4">Files</h2>
          <ul>
            {detail?.files.map((f) => (
              <li key={f}>
                <button type="button" className={`link${f === path ? ' current' : ''}`} aria-current={f === path ? 'true' : undefined} onClick={() => pick(f)}>
                  {f}
                  {detail.changed.includes(f) ? ' •' : ''}
                </button>
              </li>
            ))}
          </ul>
          <label htmlFor="new-file" className="small">
            Add a file
          </label>
          <div className="button-row">
            <input id="new-file" value={newFile} placeholder="tests/fixtures/items.json" onChange={(e) => setNewFile(e.target.value)} />
            <button type="button" onClick={() => void addFile()} disabled={!newFile.trim()}>
              Add
            </button>
          </div>
        </nav>
        <section className="code-main" aria-labelledby="code-file-heading">
          <h2 id="code-file-heading" className="h4">
            {path || 'No file'}
            {dirty ? <span className="muted small"> (unsaved)</span> : null}
          </h2>
          {path && <CodeEditor id="code-editor" value={content} language={languageOf(path)} label={`Contents of ${path}`} onChange={setContent} minHeight="24rem" />}
          <p className="button-row">
            <button type="button" className="primary" onClick={() => void save()} disabled={!dirty || busy !== null}>
              Save file
            </button>
            {path.endsWith('.py') && (
              <>
                <button type="button" onClick={() => void lint()} disabled={busy !== null}>
                  Check with ruff
                </button>
                <button type="button" onClick={() => void format()} disabled={busy !== null}>
                  Format with ruff
                </button>
              </>
            )}
            <button type="button" onClick={() => (setContent(saved), setDiagnostics(null))} disabled={!dirty}>
              Revert
            </button>
          </p>
          {busy && <Spinner label={`Working (${busy})…`} />}
          {diagnostics && (
            <div className={`notice ${diagnostics.length ? 'warning' : 'ok'}`} role="status">
              {diagnostics.length ? (
                <ul className="small">
                  {diagnostics.map((d, i) => (
                    <li key={i}>
                      Line {d.line}: <code>{d.code}</code> {d.message}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>ruff found nothing to fix.</p>
              )}
            </div>
          )}
        </section>
      </div>

      <section aria-labelledby="checks-heading">
        <h2 id="checks-heading">Contract checks</h2>
        <p>
          They run in the sandbox against <code>examples/pygeoapi-config.snippet.yml</code>, with the package's <code>src</code> on the path and only the hosts the descriptor declares.
          Save your files first.
        </p>
        {detail?.env.map((name) => (
          <label key={name}>
            Test value for <code>${'{'}
            {name}
            {'}'}</code>
            <input type="password" autoComplete="off" value={secrets[name] ?? ''} onChange={(e) => setSecrets({ ...secrets, [name]: e.target.value })} />
          </label>
        ))}
        <HarnessPanel start={startHarness} auto={false} result={harness} onResult={setHarness} errors={{}} showErrors={false} />
      </section>

      <section aria-labelledby="version-heading">
        <h2 id="version-heading">Save a version</h2>
        <p className="muted small">A version is a Git tag, with the version in pyproject.toml, the package and its descriptor. The contract checks must pass first.</p>
        <div className="inline-fields">
          <label>
            Version
            <input value={version} onChange={(e) => setVersion(e.target.value)} aria-invalid={SEMVER.test(version) ? undefined : true} />
          </label>
          <label>
            What changed
            <input value={changes} placeholder="First version." onChange={(e) => setChanges(e.target.value)} />
          </label>
        </div>
        <p className="button-row">
          <button type="button" className="primary" onClick={() => void saveVersion()} disabled={!passed || !SEMVER.test(version) || dirty || busy !== null}>
            Save version {version}
          </button>
          {detail?.remote && (
            <button type="button" onClick={() => void pull()} disabled={busy !== null}>
              Pull from Git
            </button>
          )}
        </p>
        {!passed && <p className="muted small">Run the contract checks, and pass them, to save a version.</p>}
        {detail && (
          <p>
            <a className="button" href={`#/configure/new?descriptor=${encodeURIComponent(detail.descriptor)}`}>
              Publish a collection with this provider →
            </a>
          </p>
        )}
      </section>

      {detail && detail.versions.length > 0 && (
        <section aria-labelledby="deliver-heading">
          <h2 id="deliver-heading">Deliver a version</h2>
          <label htmlFor="deliver-version">Version</label>
          <select id="deliver-version" value={deliverVersion} onChange={(e) => setChosen(e.target.value)}>
            {detail.versions.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <h3>A package</h3>
          <p className="muted small">The version's Git tag, as a zip: what pip installs.</p>
          <p>
            <ZipLink href={`/api/code/${encodeURIComponent(id)}/versions/${encodeURIComponent(deliverVersion)}/package.zip`} version={deliverVersion} />
          </p>
          <h3>A test image</h3>
          <ImagePanel key={deliverVersion} start={startImage} version={deliverVersion} />
          <h3>Git</h3>
          <GitPanel push={push} idPrefix="code-git" />
        </section>
      )}

      <section aria-labelledby="code-usage-heading">
        <h2 id="code-usage-heading">Used by</h2>
        <UsagePanel load={usage} />
      </section>
    </div>
  )
}
