// A template-built provider (PLAN.md §7.4, §7.8): its saved versions and their
// contract checks, delivery of a version (zip, test image, Git), where it's used,
// and the ways on: publish a collection, or eject to code.
import { useCallback, useEffect, useRef, useState } from 'react'

import { api, unwrap } from '../api/client'
import type { HarnessData, ModeBResult, SpecProviderDetail } from '../api/types'
import { Spinner, YamlView } from '../components/common'
import { GitPanel, ImagePanel, UsagePanel, ZipLink } from './delivery'
import { HarnessPanel } from './shared'

const TEMPLATE_NAMES: Record<string, string> = {
  'http-features': 'HTTP JSON API → OGC API - Features',
  'sql-features': 'SQL query → OGC API - Features',
  'timeseries-edr': 'Time series → OGC API - EDR',
}

function envNames(spec: string): string[] {
  return [...new Set([...spec.matchAll(/\$\{([A-Za-z_][A-Za-z0-9_]*)/g)].map((m) => m[1] ?? ''))].filter(Boolean).sort()
}

export function SpecProviderPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<SpecProviderDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState('')
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [checks, setChecks] = useState<ModeBResult<HarnessData> | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)

  const load = useCallback(async () => {
    const res = await api.GET('/api/workspace/{provider_id}', { params: { path: { provider_id: id } } })
    if (res.error) {
      const detail = (res.error as { detail?: unknown }).detail
      setError(typeof detail === 'string' ? detail : 'This provider could not be loaded.')
      return null
    }
    const d = res.data as unknown as SpecProviderDetail
    setDetail(d)
    return d
  }, [id])

  useEffect(() => {
    heading.current?.focus()
    const t = setTimeout(() => {
      void load().then((d) => d && setVersion((v) => v || (d.versions[0]?.version ?? '')))
    }, 0)
    return () => clearTimeout(t)
  }, [load])

  const selected = detail?.versions.find((v) => v.version === version) ?? null
  const passed = Boolean(selected?.harness?.ok)
  const names = selected ? envNames(selected.spec) : []

  const startChecks = useCallback(
    async () =>
      unwrap(
        await api.POST('/api/workspace/{provider_id}/versions/{version}/checks', {
          params: { path: { provider_id: id, version } },
          body: { test_secrets: secrets },
        }),
      ),
    [id, version, secrets],
  )
  const onChecks = useCallback(
    (r: ModeBResult<HarnessData> | null) => {
      setChecks(r)
      if (r?.ok && r.result?.ok) void load()
    },
    [load],
  )
  const startImage = useCallback(
    async () => unwrap(await api.POST('/api/workspace/{provider_id}/versions/{version}/image', { params: { path: { provider_id: id, version } } })),
    [id, version],
  )
  const push = useCallback(
    (remote: string, branch: string) => api.POST('/api/workspace/{provider_id}/push', { params: { path: { provider_id: id } }, body: { remote, branch } }),
    [id],
  )
  const usage = useCallback(() => api.GET('/api/workspace/{provider_id}/usage', { params: { path: { provider_id: id } } }), [id])

  return (
    <div className="home provider-page">
      <h1 ref={heading} tabIndex={-1}>
        {id}
      </h1>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!detail && !error && <Spinner label="Loading the provider…" />}
      {detail && (
        <>
          <p className="lead">
            A template-built provider: {TEMPLATE_NAMES[detail.template] ?? detail.template}. Catalog entry <code>{detail.descriptor}</code>.
          </p>
          <p className="button-row">
            <a className="button primary" href={`#/configure/new?descriptor=${encodeURIComponent(detail.descriptor)}`}>
              Publish a collection with this provider →
            </a>
            <a className="button" href={`#/eject/${encodeURIComponent(id)}`}>
              Eject to code…
            </a>
          </p>
          {detail.ejected.length > 0 && (
            <p className="muted small">
              Ejected:{' '}
              {detail.ejected.map((e, i) => (
                <span key={e.code_id}>
                  {i > 0 ? ', ' : ''}version {e.version} → <a href={`#/code/${encodeURIComponent(e.code_id)}`}>{e.code_id}</a>
                </span>
              ))}
            </p>
          )}

          <section aria-labelledby="versions-heading">
            <h2 id="versions-heading">Versions</h2>
            <table className="data-table">
              <caption className="visually-hidden">Saved versions of {id}</caption>
              <thead>
                <tr>
                  <th scope="col">Version</th>
                  <th scope="col">Contract checks</th>
                  <th scope="col">Spec path, as pygeoapi sees it</th>
                </tr>
              </thead>
              <tbody>
                {detail.versions.map((v) => (
                  <tr key={v.version} aria-current={v.version === version ? 'true' : undefined}>
                    <td>
                      <label className="radio">
                        <input type="radio" name="spec-version" checked={v.version === version} onChange={() => (setVersion(v.version), setChecks(null))} /> {v.version}
                      </label>
                    </td>
                    <td>{v.harness?.ok ? 'Passed' : 'Not recorded'}</td>
                    <td>
                      <code>{v.data}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {selected && (
            <section aria-labelledby="deliver-heading">
              <h2 id="deliver-heading">Deliver version {version}</h2>
              {(!passed || checks) && (
                <>
                  {!passed && <p>Only a version that passed the contract checks is delivered. This one was saved without them: run them now.</p>}
                  {names.map((name) => (
                    <label key={name}>
                      Test value for <code>{`\${${name}}`}</code>
                      <input type="password" autoComplete="off" value={secrets[name] ?? ''} onChange={(e) => setSecrets({ ...secrets, [name]: e.target.value })} />
                    </label>
                  ))}
                  <HarnessPanel start={startChecks} auto={false} result={checks} onResult={onChecks} errors={{}} showErrors={false} />
                </>
              )}
              <h3>A package</h3>
              <p className="muted small">
                A pip package whose spec is a resource, so a config says <code>data: pkg:…/spec.yaml</code>. It carries the spec engine's source, which isn't on PyPI yet.
              </p>
              <p>
                <ZipLink href={`/api/workspace/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/package.zip`} version={version} disabled={!passed} />
              </p>
              <h3>A test image</h3>
              <ImagePanel key={version} start={startImage} version={version} disabled={!passed} />
              <h3>Git</h3>
              <GitPanel push={push} idPrefix="spec-git" />
              <details>
                <summary>The spec of version {version}</summary>
                <YamlView text={selected.spec} label={`The spec of version ${version}`} />
              </details>
            </section>
          )}

          <section aria-labelledby="usage-heading">
            <h2 id="usage-heading">Used by</h2>
            <UsagePanel load={usage} />
          </section>
        </>
      )}
    </div>
  )
}
