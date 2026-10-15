// Wizard state for J1 (PLAN.md §6.1). Test secrets live here, in memory
// only: they are never written to form state, storage or the URL.
import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'

import type { FormState, ProviderState, ResourceState } from '../api/client'
import type { DescriptorDetail, IntrospectData, ProbeData, SandboxResult } from '../api/types'
import { activeFields } from '../forms/validate'

export const STEPS = [
  { id: 'start', title: 'Start' },
  { id: 'backend', title: 'Choose a backend' },
  { id: 'connect', title: 'Connect' },
  { id: 'test', title: 'Test and inspect' },
  { id: 'fields', title: 'Map fields' },
  { id: 'describe', title: 'Describe' },
  { id: 'review', title: 'Review' },
  { id: 'publish', title: 'Publish' },
] as const

export type StepId = (typeof STEPS)[number]['id']

/**
 * new: J1 (also a duplicate or an imported snippet, prefilled).
 * edit: J2, an existing resource on a target; `addProvider` is J3.
 */
export type Mode = { kind: 'new' } | { kind: 'edit'; resourceId: string; etag: string; addProvider: boolean }

export interface WizardState {
  step: StepId
  mode: Mode
  /** The provider the Connect, Test and Map steps work on (J3 appends one). */
  index: number
  target: string
  detail: DescriptorDetail | null
  form: FormState
  /** Secret test values by field key (never persisted). */
  testSecrets: Record<string, string>
  introspection: SandboxResult<IntrospectData> | null
  probe: SandboxResult<ProbeData> | null
  idEdited: boolean
  envEdited: Record<string, boolean>
  /** Admin API risks the user accepted on Review (ADR-0015). */
  acknowledge: string[]
}

/** The steps each mode walks through. A provider with nothing to map (tiles, maps) skips Map fields. */
export function stepsFor(mode: Mode, detail?: DescriptorDetail | null): (typeof STEPS)[number][] {
  const map = detail?.ui.groups.find((g) => g.id === 'map')
  const nothingToMap = Boolean(detail) && !map?.keys.length && !map?.advanced.length
  const steps = STEPS.filter((s) => !(nothingToMap && s.id === 'fields'))
  if (mode.kind === 'edit' && mode.addProvider) return steps.filter((s) => s.id !== 'start')
  if (mode.kind === 'edit') return steps.filter((s) => s.id !== 'start' && s.id !== 'backend')
  return steps
}

const CRS84 = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'

export function emptyResource(): ResourceState {
  return {
    type: 'collection',
    title: '',
    description: '',
    keywords: [],
    links: [],
    extents: { spatial: { bbox: [-180, -90, 180, 90], crs: CRS84 }, temporal: null },
    linked_data_context: null,
    templates_path: null,
    jsonld_template: null,
  }
}

export function initialState(target: string): WizardState {
  return {
    step: 'start',
    mode: { kind: 'new' },
    index: 0,
    target,
    detail: null,
    form: { form_version: 1, resource_id: '', resource: emptyResource(), providers: [] },
    testSecrets: {},
    introspection: null,
    probe: null,
    idEdited: false,
    envEdited: {},
    acknowledge: [],
  }
}

export function slug(text: string): string {
  return text
    .replace(/\.[A-Za-z0-9]+$/, '')
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, '')
    .toLowerCase()
}

/** Same rule as backend render.env_name: PYGEOAPI_<RESOURCE>_<FIELD>. */
export function envName(resourceId: string, key: string, hint?: string | null): string {
  const stem = hint || key.replace(/^data\./, '')
  return `PYGEOAPI_${resourceId || 'resource'}_${stem}`
    .toUpperCase()
    .replace(/[^A-Z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
}

export type Action =
  | { type: 'goto'; step: StepId }
  | {
      type: 'load'
      form: FormState
      mode: Mode
      index: number
      detail: DescriptorDetail | null
      step: StepId
    }
  | { type: 'target'; target: string }
  | { type: 'descriptor'; detail: DescriptorDetail }
  | { type: 'value'; key: string; value: unknown }
  | { type: 'variant'; variant: string }
  | { type: 'secretEnv'; key: string; env: string; edited: boolean }
  | { type: 'testSecret'; key: string; value: string }
  | { type: 'introspection'; result: SandboxResult<IntrospectData> | null }
  | { type: 'probe'; result: SandboxResult<ProbeData> | null }
  | { type: 'resource'; patch: Partial<ResourceState> }
  | { type: 'resourceId'; id: string; edited: boolean }
  | { type: 'acknowledge'; value: string; on: boolean }

const EMPTY_PROVIDER: ProviderState = { descriptor: '', variant: null, values: {}, secrets: {}, extra: {} }

/** The provider being edited. */
export function current(state: WizardState): ProviderState | undefined {
  return state.form.providers[state.index]
}

function provider(state: WizardState): ProviderState {
  return current(state) ?? EMPTY_PROVIDER
}

function replaceProvider(state: WizardState, p: ProviderState): ProviderState[] {
  const providers = [...state.form.providers]
  providers[state.index] = p
  return providers
}

/** Connection fields decide whether the provider loads; changing one invalidates the probe. */
function isConnectKey(detail: DescriptorDetail | null, key: string): boolean {
  if (!detail) return true
  const connect = detail.ui.groups.find((g) => g.id === 'connect')
  return Boolean(connect?.keys.includes(key) || connect?.advanced.includes(key) || detail.ui.variants.some((v) => v.keys.includes(key)))
}

function withProvider(state: WizardState, p: ProviderState, invalidate = true): WizardState {
  return { ...state, form: { ...state.form, providers: replaceProvider(state, p) }, probe: invalidate ? null : state.probe }
}

function syncEnvNames(state: WizardState): WizardState {
  const d = state.detail
  const p = current(state)
  if (!d || !p) return state
  const secrets = { ...(p.secrets ?? {}) }
  for (const f of Object.values(activeFields(d, p.variant))) {
    if (f.type !== 'secret' || state.envEdited[f.key]) continue
    secrets[f.key] = { env: envName(state.form.resource_id, f.key, f.env_name) }
  }
  return { ...state, form: { ...state.form, providers: replaceProvider(state, { ...p, secrets }) } }
}

function suggestId(state: WizardState, key: string, value: unknown): WizardState {
  if (state.idEdited || typeof value !== 'string' || !value) return state
  const tableKey = state.detail?.introspect?.table_key ?? 'table'
  if (key !== 'data' && key !== tableKey) return state
  const p = provider(state)
  if (key === 'data' && state.detail && activeFields(state.detail, p.variant).data?.type !== 'path') return state
  const id = slug(value.split('/').pop() ?? value)
  return id ? syncEnvNames({ ...state, form: { ...state.form, resource_id: id } }) : state
}

export function reducer(state: WizardState, action: Action): WizardState {
  switch (action.type) {
    case 'goto':
      return { ...state, step: action.step }
    case 'load': {
      // Loaded secrets keep their variable names: the server already defines them.
      const loaded = action.form.providers[action.index]
      const envEdited = Object.fromEntries(Object.keys(loaded?.secrets ?? {}).map((k) => [k, true]))
      return {
        ...state,
        form: action.form,
        mode: action.mode,
        index: action.index,
        detail: action.detail,
        step: action.step,
        idEdited: true,
        envEdited,
        introspection: null,
        probe: null,
      }
    }
    case 'target':
      return { ...state, target: action.target }
    case 'descriptor': {
      const d = action.detail
      const values: Record<string, unknown> = {}
      const variant = d.ui.variants[0]?.id ?? null
      for (const f of Object.values(activeFields(d, variant))) if (f.suggest !== undefined && f.suggest !== null) values[f.key] = f.suggest
      if (variant) values['$variant'] = variant
      // A created provider is one API: its id is a good collection id to start from.
      const resourceId = !state.idEdited && !state.form.resource_id && d.origin === 'created' ? slug(d.id.split('/').pop() ?? '') : state.form.resource_id
      const next: WizardState = {
        ...state,
        detail: d,
        introspection: null,
        probe: null,
        envEdited: {},
        form: { ...state.form, resource_id: resourceId, providers: replaceProvider(state, { descriptor: d.id, variant, values, secrets: {}, extra: {} }) },
      }
      return syncEnvNames(next)
    }
    case 'value': {
      const p = provider(state)
      const empty = action.value === undefined || action.value === '' || action.value === null
      const values = Object.fromEntries(Object.entries(p.values ?? {}).filter(([k]) => k !== action.key))
      if (!empty) values[action.key] = action.value
      return suggestId(withProvider(state, { ...p, values }, isConnectKey(state.detail, action.key)), action.key, action.value)
    }
    case 'variant': {
      const p = provider(state)
      const values: Record<string, unknown> = { ...(p.values ?? {}), $variant: action.variant }
      // The new variant's own suggestions fill what's empty or no longer one of its choices.
      const own = state.detail?.ui.variants.find((v) => v.id === action.variant)?.fields ?? {}
      for (const f of Object.values(own)) {
        if (f.suggest === undefined || f.suggest === null) continue
        const stale = Boolean(f.choices?.length) && !f.choices?.includes(values[f.key])
        if (values[f.key] === undefined || stale) values[f.key] = f.suggest
      }
      return withProvider(state, { ...p, variant: action.variant, values })
    }
    case 'secretEnv': {
      const p = provider(state)
      return {
        ...withProvider(state, { ...p, secrets: { ...(p.secrets ?? {}), [action.key]: { env: action.env } } }),
        envEdited: { ...state.envEdited, [action.key]: action.edited },
      }
    }
    case 'testSecret':
      return { ...state, testSecrets: { ...state.testSecrets, [action.key]: action.value }, probe: null }
    case 'introspection': {
      const next = { ...state, introspection: action.result }
      const found = action.result?.ok ? action.result.result : null
      const d = state.detail
      if (!found || !d?.introspect) return next
      // Prefill what has one answer: the only table or layer, then its key column.
      const p = provider(next)
      const values = { ...(p.values ?? {}) }
      const tableKey = d.introspect.table_key ?? 'table'
      const only = found.tables?.length === 1 ? found.tables[0] : undefined
      if (d.ui.fields[tableKey] && values[tableKey] === undefined && only) values[tableKey] = only.name
      const table = found.tables?.find((t) => t.name === values[tableKey])
      const pk = (found.columns ?? table?.columns ?? []).find((c) => c.pk)
      if (d.ui.fields.id_field && values.id_field === undefined && pk) values.id_field = pk.name
      return withProvider(next, { ...p, values })
    }
    case 'probe':
      return { ...state, probe: action.result }
    case 'resource':
      return { ...state, form: { ...state.form, resource: { ...state.form.resource, ...action.patch } } }
    case 'acknowledge':
      return { ...state, acknowledge: action.on ? [...new Set([...state.acknowledge, action.value])] : state.acknowledge.filter((a) => a !== action.value) }
    case 'resourceId':
      return syncEnvNames({ ...state, idEdited: action.edited, form: { ...state.form, resource_id: action.id } })
  }
}

const Ctx = createContext<{ state: WizardState; dispatch: Dispatch<Action> } | null>(null)

export function WizardProvider({ target, children }: { target: string; children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, target, initialState)
  return <Ctx.Provider value={{ state, dispatch }}>{children}</Ctx.Provider>
}

export function useWizard() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useWizard outside WizardProvider')
  return ctx
}

/** The secret values a run needs: only those the provider references. */
export function runSecrets(state: WizardState): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, ref] of Object.entries(current(state)?.secrets ?? {})) {
    const v = state.testSecrets[key]
    if (v) out[ref.env] = v
  }
  // A created provider's own variables (its spec placeholders), by name.
  for (const v of state.detail?.env ?? []) {
    const value = state.testSecrets[`env:${v.name}`]
    if (value) out[v.name] = value
  }
  return out
}
