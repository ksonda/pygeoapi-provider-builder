// The in-house form renderer (ADR-0004): renders one wizard step of a
// compiled descriptor. Every control is labelled, errors are linked with
// aria-describedby, and advanced options are collapsed.
import { useState, type ReactNode } from 'react'

import type { ProviderState } from '../api/client'
import type { DescriptorDetail, UiField } from '../api/types'
import { FileBrowser } from './FileBrowser'
import { activeFields } from './validate'

export interface FormProps {
  detail: DescriptorDetail
  step: 'connect' | 'map'
  provider: ProviderState
  errors: Record<string, string>
  showErrors: boolean
  testSecrets: Record<string, string>
  dataRoot: string
  choicesFor: (f: UiField) => string[] | null
  onValue: (key: string, value: unknown) => void
  onVariant: (variant: string) => void
  onSecretEnv: (key: string, env: string) => void
  onTestSecret: (key: string, value: string) => void
}

export const fieldId = (key: string) => `f-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`

function Row({ f, error, children, htmlFor }: { f: UiField; error?: string; children: ReactNode; htmlFor?: string }) {
  const id = fieldId(f.key)
  return (
    <div className={`field${error ? ' has-error' : ''}`}>
      <label htmlFor={htmlFor ?? id}>
        {f.title}
        {f.required ? <span aria-hidden="true"> *</span> : null}
        {f.unit ? <span className="muted"> ({f.unit})</span> : null}
      </label>
      {children}
      {f.help && (
        <p id={`${id}-help`} className="help">
          {f.help}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function describedBy(f: UiField, error?: string) {
  const id = fieldId(f.key)
  return [f.help ? `${id}-help` : '', error ? `${id}-err` : ''].filter(Boolean).join(' ') || undefined
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function FieldList({ f, value, choices, onChange, error }: {
  f: UiField
  value: string[]
  choices: string[]
  onChange: (v: string[]) => void
  error?: string
}) {
  const [pick, setPick] = useState('')
  const remaining = choices.filter((c) => !value.includes(c))
  const move = (i: number, d: number) => {
    const next = [...value]
    const [item] = next.splice(i, 1)
    if (item === undefined) return
    next.splice(i + d, 0, item)
    onChange(next)
  }
  const id = fieldId(f.key)
  return (
    <div className="field-list" aria-describedby={describedBy(f, error)}>
      {value.length === 0 ? <p className="muted">All properties are published.</p> : null}
      <ol>
        {value.map((v, i) => (
          <li key={v}>
            <span>{v}</span>
            <button type="button" aria-label={`Move ${v} up`} disabled={i === 0} onClick={() => move(i, -1)}>
              ↑
            </button>
            <button type="button" aria-label={`Move ${v} down`} disabled={i === value.length - 1} onClick={() => move(i, 1)}>
              ↓
            </button>
            <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))}>
              ✕
            </button>
          </li>
        ))}
      </ol>
      {remaining.length > 0 && (
        <div className="inline">
          <select id={id} value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Add a property…</option>
            {remaining.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button type="button" disabled={!pick} onClick={() => (onChange([...value, pick]), setPick(''))}>
            Add
          </button>
          <button type="button" onClick={() => onChange([...value, ...remaining])}>
            Add all
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * Text that holds a parsed value (a list, a mapping, YAML): typing keeps the raw
 * text, and the text is only rewritten when the value changes from elsewhere.
 */
function useDraft<T>(value: T, show: (v: T) => string, parse: (text: string) => T): [string, (text: string) => T] {
  const [draft, setDraft] = useState<string | null>(null)
  const shown = show(value)
  const text = draft !== null && show(parse(draft)) === shown ? draft : shown
  return [text, (t: string) => (setDraft(t), parse(t))]
}

const listItems = (text: string) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)

function ListText({ f, value, common, onChange }: { f: UiField; value: string[]; common: object; onChange: (v: string[] | undefined) => void }) {
  const [text, update] = useDraft(value, (v) => v.join('\n'), listItems)
  return (
    <textarea
      {...common}
      className="mono"
      rows={3}
      placeholder={Array.isArray(f.default) ? `Default: ${f.default.join(', ')}` : 'One per line'}
      value={text}
      onChange={(e) => {
        const items = update(e.target.value)
        onChange(items.length ? items : undefined)
      }}
    />
  )
}

type Pairs = [string, string][]

function KeyValue({ f, value, onChange }: { f: UiField; value: unknown; onChange: (v: Record<string, unknown> | undefined) => void }) {
  const obj = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
  const text = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))
  const toObject = (pairs: Pairs) => {
    const out: Record<string, unknown> = {}
    for (const [k, v] of pairs) {
      if (!k.trim()) continue
      // Keep the original value (a boolean, a number) until its text is edited.
      const before = obj[k.trim()]
      out[k.trim()] = before !== undefined && text(before) === v ? before : v
    }
    return out
  }
  const [draft, setDraft] = useState<Pairs | null>(null)
  // The rows being typed, unless the value changed elsewhere since.
  const rows: Pairs = draft && JSON.stringify(toObject(draft)) === JSON.stringify(obj) ? draft : Object.entries(obj).map(([k, v]) => [k, text(v)])
  const commit = (next: Pairs) => {
    setDraft(next)
    const out = toObject(next)
    onChange(Object.keys(out).length ? out : undefined)
  }
  return (
    <div className="key-value" role="group" aria-label={f.title}>
      {rows.map(([k, v], i) => (
        <div className="inline" key={i}>
          <input aria-label={`${f.title}: name ${i + 1}`} className="mono" value={k} placeholder="Name" onChange={(e) => commit(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <input aria-label={`${f.title}: value ${i + 1}`} className="mono grow" value={v} placeholder="Value" onChange={(e) => commit(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <button type="button" className="small" aria-label={`Remove ${k || `option ${i + 1}`}`} onClick={() => commit(rows.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="small" onClick={() => setDraft([...rows, ['', '']])}>
        Add an option
      </button>
    </div>
  )
}

function Control({ f, props }: { f: UiField; props: FormProps }) {
  const { provider, errors, showErrors, choicesFor, onValue } = props
  const [browsing, setBrowsing] = useState(false)
  const id = fieldId(f.key)
  const value = provider.values?.[f.key]
  const error = showErrors ? errors[f.key] : undefined
  const common = { id, 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy(f, error) }

  switch (f.widget) {
    case 'secret': {
      const env = provider.secrets?.[f.key]?.env ?? ''
      return (
        <Row f={f} error={error} htmlFor={`${id}-value`}>
          <input
            {...common}
            id={`${id}-value`}
            type="password"
            autoComplete="off"
            placeholder="Value to use for testing only"
            value={props.testSecrets[f.key] ?? ''}
            onChange={(e) => props.onTestSecret(f.key, e.target.value)}
          />
          <div className="secret-env">
            <label htmlFor={`${id}-env`} className="small">
              Written to the config as the environment variable
            </label>
            <input
              id={`${id}-env`}
              className="mono"
              value={env}
              onChange={(e) => props.onSecretEnv(f.key, e.target.value.toUpperCase())}
            />
            <p className="help">
              The value above is only used for this test and never saved. The config gets <code>{`\${${env}}`}</code>.
            </p>
          </div>
        </Row>
      )
    }
    case 'checkbox':
      return (
        <div className="field checkbox">
          <input
            {...common}
            type="checkbox"
            checked={value === undefined ? Boolean(f.default) : Boolean(value)}
            onChange={(e) => onValue(f.key, e.target.checked)}
          />
          <label htmlFor={id}>{f.title}</label>
          {f.help && (
            <p id={`${id}-help`} className="help">
              {f.help}
            </p>
          )}
        </div>
      )
    case 'number':
      return (
        <Row f={f} error={error}>
          <input
            {...common}
            type="number"
            inputMode="numeric"
            min={f.min ?? undefined}
            max={f.max ?? undefined}
            placeholder={f.default !== undefined && f.default !== null ? `Default: ${String(f.default)}` : ''}
            value={value === undefined ? '' : String(value)}
            onChange={(e) => onValue(f.key, e.target.value === '' ? undefined : Number(e.target.value))}
          />
        </Row>
      )
    case 'path':
      return (
        <Row f={f} error={error}>
          <div className="inline">
            <input
              {...common}
              className="mono grow"
              placeholder={`${props.dataRoot}/…`}
              value={typeof value === 'string' ? value : ''}
              onChange={(e) => onValue(f.key, e.target.value)}
            />
            <button type="button" onClick={() => setBrowsing(true)}>
              Browse…
            </button>
          </div>
          <FileBrowser
            open={browsing}
            start={props.dataRoot}
            extensions={f.extensions}
            pathKind={f.path_kind ?? 'file'}
            onClose={() => setBrowsing(false)}
            onPick={(p) => (onValue(f.key, p), setBrowsing(false))}
          />
        </Row>
      )
    case 'text':
    case 'field': {
      const choices = f.choices_from ? choicesFor(f) : null
      if (choices && choices.length) {
        const current = typeof value === 'string' ? value : ''
        return (
          <Row f={f} error={error}>
            <select {...common} value={current} onChange={(e) => onValue(f.key, e.target.value || undefined)}>
              <option value="">{f.default ? `Default: ${String(f.default)}` : 'Choose…'}</option>
              {current && !choices.includes(current) ? <option value={current}>{current} (not found)</option> : null}
              {choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Row>
        )
      }
      return (
        <Row f={f} error={error}>
          <input
            {...common}
            className="mono"
            placeholder={f.default ? `Default: ${String(f.default)}` : ''}
            value={typeof value === 'string' ? value : ''}
            onChange={(e) => onValue(f.key, e.target.value)}
          />
        </Row>
      )
    }
    case 'field-list':
      return (
        <Row f={f} error={error}>
          <FieldList f={f} value={asList(value)} choices={choicesFor(f) ?? []} error={error}
            onChange={(v) => onValue(f.key, v.length ? v : undefined)} />
        </Row>
      )
    case 'string-list':
    case 'crs-list':
      return (
        <Row f={f} error={error}>
          <ListText f={f} value={asList(value)} common={common} onChange={(v) => onValue(f.key, v)} />
        </Row>
      )
    case 'key-value':
      return (
        <Row f={f} error={error}>
          <KeyValue f={f} value={value} onChange={(v) => onValue(f.key, v)} />
        </Row>
      )
    case 'yaml':
      return (
        <Row f={f} error={error}>
          <textarea
            {...common}
            className="mono"
            rows={2}
            placeholder="A value in YAML, such as lon or [minlon, maxlon]"
            value={typeof value === 'string' ? value : ''}
            onChange={(e) => onValue(f.key, e.target.value || undefined)}
          />
        </Row>
      )
    case 'select':
      return (
        <Row f={f} error={error}>
          <select {...common} value={value === undefined ? '' : String(value)} onChange={(e) => onValue(f.key, e.target.value || undefined)}>
            <option value="">Choose…</option>
            {(f.choices ?? []).map((c) => (
              <option key={String(c)} value={String(c)}>
                {String(c)}
              </option>
            ))}
          </select>
        </Row>
      )
    default: {
      const choices = f.choices_from ? choicesFor(f) : null
      return (
        <Row f={f} error={error}>
          <input
            {...common}
            list={choices?.length ? `${id}-choices` : undefined}
            placeholder={f.default !== undefined && f.default !== null ? `Default: ${String(f.default)}` : ''}
            value={typeof value === 'string' || typeof value === 'number' ? String(value) : ''}
            onChange={(e) => onValue(f.key, e.target.value)}
          />
          {choices?.length ? (
            <datalist id={`${id}-choices`}>
              {choices.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          ) : null}
        </Row>
      )
    }
  }
}

export function DescriptorForm(props: FormProps) {
  const { detail, step, provider } = props
  const group = detail.ui.groups.find((g) => g.id === step)
  const variants = step === 'connect' ? detail.ui.variants : []
  const current = provider.variant ?? variants[0]?.id
  const variant = variants.find((v) => v.id === current)
  const defs = activeFields(detail, current)
  const fields = (keys: string[]) =>
    keys.map((k) => defs[k]).filter((f): f is UiField => Boolean(f)).map((f) => <Control key={f.key} f={f} props={props} />)

  return (
    <div className="descriptor-form">
      {variants.length > 0 && (
        <fieldset className="variants">
          <legend>How do you want to connect?</legend>
          {variants.map((v) => (
            <label key={v.id} className="radio">
              <input type="radio" name="variant" value={v.id} checked={v.id === current} onChange={() => props.onVariant(v.id)} />
              {v.title}
            </label>
          ))}
          {variant?.help && <p className="help">{variant.help}</p>}
        </fieldset>
      )}
      {variant && fields(variant.keys)}
      {group && fields(group.keys)}
      {group && group.advanced.length > 0 && (
        <details className="advanced">
          <summary>Advanced options ({group.advanced.length})</summary>
          {fields(group.advanced)}
        </details>
      )}
    </div>
  )
}
