// J5, step 1 (PLAN.md §7.5): scaffold a provider package of any type, with
// method stubs from the installed pygeoapi, then open it in the code workspace.
import { useState } from 'react'

import { api } from '../../api/client'
import type { CodeDetail, CodeProviderType } from '../../api/types'
import { ErrorSummary } from '../../components/common'
import { FieldError, ID_RE, described, slugify } from '../shared'

const TYPES: [CodeProviderType, string][] = [
  ['feature', 'Features (OGC API - Features)'],
  ['record', 'Catalog records (OGC API - Records)'],
  ['coverage', 'A coverage (OGC API - Coverages)'],
  ['edr', 'EDR queries (OGC API - EDR)'],
  ['tile', 'Vector tiles (OGC API - Tiles)'],
  ['map', 'Map images (OGC API - Maps)'],
]
const EDR_TYPES = ['position', 'radius', 'area', 'cube', 'trajectory', 'corridor', 'locations'] as const
const STYLES: [string, string][] = [
  ['rest', 'An HTTP API'],
  ['sql', 'A database'],
  ['files', 'Files'],
]

function camel(id: string): string {
  const name = id
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join('')
  const safe = /^[A-Za-z]/.test(name) ? name : `P${name}`
  return safe.endsWith('Provider') ? safe : `${safe}Provider`
}

function pkg(id: string): string {
  const name = id.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  const safe = /^[a-z_]/.test(name) ? name : `p_${name}`
  return safe.endsWith('provider') ? safe : `${safe}_provider`
}

export function NewCodeProvider() {
  const [type, setType] = useState<CodeProviderType>('feature')
  const [title, setTitle] = useState('')
  const [id, setId] = useState('')
  const [idEdited, setIdEdited] = useState(false)
  const [style, setStyle] = useState('rest')
  const [queryTypes, setQueryTypes] = useState<string[]>(['position', 'locations'])
  const [description, setDescription] = useState('')
  const [showErrors, setShowErrors] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const slug = idEdited ? id : slugify(title)

  const errors: Record<string, string> = {}
  if (!title.trim()) errors['code-title'] = 'Title is required.'
  if (!ID_RE.test(slug)) errors['code-id'] = 'Provider id: lower-case letters, digits and hyphens.'
  if (type === 'edr' && !queryTypes.length) errors['code-query-types'] = 'Pick at least one EDR query type.'

  const create = async () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    setBusy(true)
    setError(null)
    const res = await api.POST('/api/code', {
      body: { provider_type: type, id: slug, title, description, style: style as 'rest' | 'sql' | 'files', query_types: queryTypes as never },
    })
    setBusy(false)
    if (res.error) {
      const detail = (res.error as { detail?: unknown }).detail
      setError(typeof detail === 'string' ? detail : 'Creating the provider failed.')
      return
    }
    window.location.hash = `#/code/${(res.data as unknown as CodeDetail).id}`
  }

  return (
    <div className="home code-new">
      <h1 tabIndex={-1}>Write a provider in Python</h1>
      <p className="lead">
        A pip-installable package with every method pygeoapi calls, stubbed from pygeoapi's own code, and the contract checks the builder runs.{' '}
        <a href="#/create/new">Choose a no-code template instead</a>
      </p>
      {showErrors && Object.keys(errors).length > 0 && <ErrorSummary errors={Object.entries(errors)} onGo={(key) => document.getElementById(key)?.focus()} />}
      <fieldset>
        <legend>What it serves</legend>
        {TYPES.map(([value, label]) => (
          <label key={value} className="radio">
            <input type="radio" name="code-type" checked={type === value} onChange={() => setType(value)} /> {label}
          </label>
        ))}
      </fieldset>
      {type === 'edr' && (
        <fieldset>
          <legend>EDR query types</legend>
          <p className="muted small">Each becomes a method in the class body, as pygeoapi 0.24 needs.</p>
          {EDR_TYPES.map((qt) => (
            <label key={qt} className="check">
              <input type="checkbox" checked={queryTypes.includes(qt)} onChange={(e) => setQueryTypes(e.target.checked ? EDR_TYPES.filter((q) => q === qt || queryTypes.includes(q)) : queryTypes.filter((q) => q !== qt))} /> {qt}
            </label>
          ))}
          <FieldError id="code-query-types" errors={errors} show={showErrors} />
        </fieldset>
      )}
      <fieldset>
        <legend>Where its data comes from</legend>
        {STYLES.map(([value, label]) => (
          <label key={value} className="radio">
            <input type="radio" name="code-style" checked={style === value} onChange={() => setStyle(value)} /> {label}
          </label>
        ))}
        <p className="muted small">This adds a small helper (an HTTP client or a database engine) and an example for the data setting.</p>
      </fieldset>
      <label htmlFor="code-title">
        Title <span aria-hidden="true">*</span>
      </label>
      <input id="code-title" value={title} onChange={(e) => setTitle(e.target.value)} {...described('code-title', errors, showErrors)} />
      <FieldError id="code-title" errors={errors} show={showErrors} />
      <label htmlFor="code-id">
        Provider id <span aria-hidden="true">*</span>
      </label>
      <p className="muted small" id="code-id-help">
        The workspace folder and catalog entry. Package <code>{pkg(slug || 'my-provider')}</code>, class <code>{camel(slug || 'my')}</code>.
      </p>
      <input
        id="code-id"
        value={slug}
        onChange={(e) => {
          setIdEdited(true)
          setId(e.target.value)
        }}
        {...described('code-id', errors, showErrors, 'help')}
      />
      <FieldError id="code-id" errors={errors} show={showErrors} />
      <label htmlFor="code-description">Description</label>
      <textarea id="code-description" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      {error && <p className="notice error">{error}</p>}
      <p>
        <button type="button" className="primary" onClick={() => void create()} disabled={busy}>
          Create the package
        </button>
      </p>
    </div>
  )
}
