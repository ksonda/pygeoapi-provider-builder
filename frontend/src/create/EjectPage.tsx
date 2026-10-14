// Eject (PLAN.md §7.6): a template-built provider's spec becomes a Python package
// in the code workspace. It's one way, so the page says what changes before it
// asks: the spec stays as it is, and the code doesn't go back into the wizard.
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { CodeDetail, Problem, SpecProviderDetail } from '../api/types'
import { ErrorSummary, Spinner } from '../components/common'
import { FieldError, ID_RE, Problems, defaultClassName, defaultPackageName, described } from './shared'

const TEMPLATE_NAMES: Record<string, string> = {
  'http-features': 'HTTP JSON API',
  'sql-features': 'SQL query',
  'timeseries-edr': 'time series',
}

export function EjectPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<SpecProviderDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [version, setVersion] = useState('')
  const [codeId, setCodeId] = useState(`${id}-code`)
  const [understood, setUnderstood] = useState(false)
  const [showErrors, setShowErrors] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ text: string; problems: Problem[] } | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    heading.current?.focus()
    let live = true
    api
      .GET('/api/workspace/{provider_id}', { params: { path: { provider_id: id } } })
      .then((res) => {
        if (!live) return
        if (res.error) {
          const detail = (res.error as { detail?: unknown }).detail
          setLoadError(typeof detail === 'string' ? detail : 'This provider could not be loaded.')
          return
        }
        const d = res.data as unknown as SpecProviderDetail
        setDetail(d)
        setVersion(d.versions[0]?.version ?? '')
      })
      .catch((e: unknown) => live && setLoadError(String(e)))
    return () => {
      live = false
    }
  }, [id])

  const errors: Record<string, string> = {}
  if (!ID_RE.test(codeId)) errors['eject-id'] = 'Package id: lower-case letters, digits and hyphens.'
  else if (codeId === id) errors['eject-id'] = 'Give the code-built provider an id of its own.'
  if (!understood) errors['eject-understood'] = 'Confirm that you understand eject is one way.'

  const eject = async () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    setBusy(true)
    setError(null)
    const res = await api.POST('/api/workspace/{provider_id}/eject', {
      params: { path: { provider_id: id } },
      body: { id: codeId, version },
    })
    setBusy(false)
    if (res.error) {
      const body = res.error as { detail?: unknown; problems?: Problem[] }
      setError({ text: typeof body.detail === 'string' ? body.detail : 'Eject failed.', problems: body.problems ?? [] })
      return
    }
    window.location.hash = `#/code/${encodeURIComponent((res.data as unknown as CodeDetail).id)}`
  }

  const template = detail ? (TEMPLATE_NAMES[detail.template] ?? detail.template) : ''
  return (
    <div className="home eject">
      <h1 ref={heading} tabIndex={-1}>
        Eject {id} to code
      </h1>
      {loadError && (
        <p className="notice error" role="alert">
          {loadError}
        </p>
      )}
      {!detail && !loadError && <Spinner label="Loading the provider…" />}
      {detail && (
        <>
          <p className="lead">
            Eject turns this {template} spec into a Python package that subclasses pygeoapi's base classes and does what the spec does. Use it when the template gets
            you most of the way and the rest needs code.
          </p>
          <div className="notice warning" role="note" aria-labelledby="eject-warning-heading">
            <h2 id="eject-warning-heading" className="h4">
              Eject is one way
            </h2>
            <ul>
              <li>The package gets its own copy of the spec's settings. Later changes to the spec don't reach it.</li>
              <li>The code doesn't go back into the wizard: from then on you change the provider in the code workspace.</li>
              <li>The spec stays as it is, so collections that already use it keep working.</li>
              <li>The package doesn't need pygeoapi-spec-providers: install it on the pygeoapi server like any provider.</li>
            </ul>
          </div>
          {detail.ejected.length > 0 && (
            <section aria-labelledby="ejected-heading">
              <h2 id="ejected-heading" className="h4">
                Already ejected
              </h2>
              <ul>
                {detail.ejected.map((e) => (
                  <li key={e.code_id}>
                    Version {e.version} → <a href={`#/code/${encodeURIComponent(e.code_id)}`}>{e.code_id}</a> <span className="muted small">({e.date})</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {showErrors && Object.keys(errors).length > 0 && <ErrorSummary errors={Object.entries(errors)} onGo={(key) => document.getElementById(key)?.focus()} />}
          <label htmlFor="eject-version">Spec version</label>
          <select id="eject-version" value={version} onChange={(e) => setVersion(e.target.value)}>
            {detail.versions.map((v, i) => (
              <option key={v.version} value={v.version}>
                {v.version}
                {i === 0 ? ' (newest)' : ''}
              </option>
            ))}
          </select>
          <label htmlFor="eject-id">
            Package id <span aria-hidden="true">*</span>
          </label>
          <p className="muted small" id="eject-id-help">
            The code-built provider's folder and catalog entry. Package <code>{defaultPackageName(codeId || 'my-provider')}</code>, class{' '}
            <code>{defaultClassName(codeId || 'my')}</code>.
          </p>
          <input id="eject-id" value={codeId} onChange={(e) => setCodeId(e.target.value)} {...described('eject-id', errors, showErrors, 'help')} />
          <FieldError id="eject-id" errors={errors} show={showErrors} />
          <label className="check">
            <input
              id="eject-understood"
              type="checkbox"
              checked={understood}
              onChange={(e) => setUnderstood(e.target.checked)}
              {...described('eject-understood', errors, showErrors)}
            />{' '}
            I understand that eject is one way.
          </label>
          <FieldError id="eject-understood" errors={errors} show={showErrors} />
          {error && (
            <>
              <p className="notice error" role="alert">
                {error.text}
              </p>
              <Problems problems={error.problems} />
            </>
          )}
          <p className="button-row">
            <button type="button" className="primary" onClick={() => void eject()} disabled={busy || !version}>
              Eject version {version} to code
            </button>
            <a className="button" href="#/create/new">
              Cancel
            </a>
          </p>
          {busy && <Spinner label="Writing the package…" />}
        </>
      )}
    </div>
  )
}
