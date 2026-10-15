// Inline validation with the same compiled JSON Schema the backend uses
// (PLAN.md §5.3). Returns plain-language messages keyed by flat field key.
import type { ErrorObject, ValidateFunction } from 'ajv'
import Ajv2020 from 'ajv/dist/2020'

import type { ProviderState } from '../api/client'
import type { DescriptorDetail, UiField } from '../api/types'

// Draft 2020-12, the dialect the backend compiles descriptors to.
const ajv = new Ajv2020({ allErrors: true, strict: false })
ajv.addFormat('uri', (v: string) => {
  try {
    return Boolean(new URL(v).protocol)
  } catch {
    return false
  }
})
ajv.addFormat('date-time', (v: string) => !Number.isNaN(Date.parse(v)))

const compiled = new WeakMap<object, ValidateFunction>()

/** The fields a provider's form uses: its variant's own definitions win over the shared ones. */
export function activeFields(detail: DescriptorDetail, variant: string | null | undefined): Record<string, UiField> {
  const chosen = detail.ui.variants.find((v) => v.id === (variant ?? detail.ui.variants[0]?.id))
  return { ...detail.ui.fields, ...(chosen?.fields ?? {}) }
}

export function flatValues(p: ProviderState | undefined): Record<string, unknown> {
  if (!p) return {}
  const values: Record<string, unknown> = { ...(p.values ?? {}) }
  for (const [key, ref] of Object.entries(p.secrets ?? {})) values[key] = ref.env
  const empty = (v: unknown) => v === '' || v === null || (Array.isArray(v) && !v.length)
  return Object.fromEntries(Object.entries(values).filter(([, v]) => !empty(v)))
}

const EXPECTED: Record<string, string> = {
  integer: 'a whole number',
  number: 'a number',
  string: 'text',
  boolean: 'true or false',
  array: 'a list',
}

function message(e: ErrorObject, f: UiField | undefined, key: string): string {
  const title = f?.title ?? key
  const p = e.params as Record<string, unknown>
  switch (e.keyword) {
    case 'required':
      return `${title} is required.`
    case 'type':
      return `${title} must be ${EXPECTED[String(p.type)] ?? p.type}.`
    case 'minLength':
      return `${title} can't be empty.`
    case 'minimum':
      return `${title} must be at least ${String(p.limit)}.`
    case 'maximum':
      return `${title} must be at most ${String(p.limit)}.`
    case 'minItems':
      return `${title} needs at least ${String(p.limit)} item(s).`
    case 'pattern':
      return f?.type === 'secret' ? `${title}: use letters, digits and underscores.` : `${title} has the wrong format.`
    case 'format':
      return `${title} must be a valid ${String(p.format)}.`
    default:
      return `${title}: ${e.message ?? 'invalid'}`
  }
}

export function validateProvider(
  schema: Record<string, unknown>,
  fields: Record<string, UiField>,
  p: ProviderState | undefined,
): Record<string, string> {
  let validate = compiled.get(schema)
  if (!validate) {
    validate = ajv.compile(schema)
    compiled.set(schema, validate)
  }
  const values = flatValues(p)
  const out: Record<string, string> = {}
  if (validate(values)) return out
  for (const e of validate.errors ?? []) {
    if (e.keyword === 'if') continue
    const key =
      e.keyword === 'required'
        ? String((e.params as { missingProperty: string }).missingProperty)
        : decodeURIComponent(e.instancePath.slice(1).split('/')[0] ?? '').replace(/~1/g, '/').replace(/~0/g, '~')
    if (key && !(key in out)) out[key] = message(e, fields[key], key)
  }
  return out
}
