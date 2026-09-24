// Wizard state for J1 (PLAN.md §6.1). Test secrets live here, in memory
// only: they are never written to form state, storage or the URL.
import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'

import type { FormState, ProviderState, ResourceState } from '../api/client'
import type { DescriptorDetail, IntrospectData, ProbeData, SandboxResult } from '../api/types'

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

export interface WizardState {
  step: StepId
  target: string
  detail: DescriptorDetail | null
  form: FormState
  /** Secret test values by field key (never persisted). */
  testSecrets: Record<string, string>
  introspection: SandboxResult<IntrospectData> | null
  probe: SandboxResult<ProbeData> | null
  idEdited: boolean
  envEdited: Record<string, boolean>
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
  }
}

export function initialState(target: string): WizardState {
  return {
    step: 'start',
    target,
    detail: null,
    form: { form_version: 1, resource_id: '', resource: emptyResource(), providers: [] },
    testSecrets: {},
    introspection: null,
    probe: null,
    idEdited: false,
    envEdited: {},
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

function provider(state: WizardState): ProviderState {
  return state.form.providers[0] ?? { descriptor: '', variant: null, values: {}, secrets: {}, extra: {} }
}

/** Connection fields decide whether the provider loads; changing one invalidates the probe. */
function isConnectKey(detail: DescriptorDetail | null, key: string): boolean {
  if (!detail) return true
  const connect = detail.ui.groups.find((g) => g.id === 'connect')
  return Boolean(connect?.keys.includes(key) || connect?.advanced.includes(key) || detail.ui.variants.some((v) => v.keys.includes(key)))
}

function withProvider(state: WizardState, p: ProviderState, invalidate = true): WizardState {
  return { ...state, form: { ...state.form, providers: [p] }, probe: invalidate ? null : state.probe }
}

function syncEnvNames(state: WizardState): WizardState {
  const d = state.detail
  const p = state.form.providers[0]
  if (!d || !p) return state
  const secrets = { ...(p.secrets ?? {}) }
  for (const f of Object.values(d.ui.fields)) {
    if (f.type !== 'secret' || state.envEdited[f.key]) continue
    secrets[f.key] = { env: envName(state.form.resource_id, f.key, f.env_name) }
  }
  return { ...state, form: { ...state.form, providers: [{ ...p, secrets }] } }
}

function suggestId(state: WizardState, key: string, value: unknown): WizardState {
  if (state.idEdited || typeof value !== 'string' || !value) return state
  if (key !== 'data' && key !== 'table') return state
  if (key === 'data' && state.detail?.ui.fields.data?.type !== 'path') return state
  const id = slug(value.split('/').pop() ?? value)
  return id ? syncEnvNames({ ...state, form: { ...state.form, resource_id: id } }) : state
}

export function reducer(state: WizardState, action: Action): WizardState {
  switch (action.type) {
    case 'goto':
      return { ...state, step: action.step }
    case 'target':
      return { ...state, target: action.target }
    case 'descriptor': {
      const d = action.detail
      const values: Record<string, unknown> = {}
      for (const f of Object.values(d.ui.fields)) if (f.suggest !== undefined && f.suggest !== null) values[f.key] = f.suggest
      const variant = d.ui.variants[0]?.id ?? null
      if (variant) values['$variant'] = variant
      const next: WizardState = {
        ...state,
        detail: d,
        introspection: null,
        probe: null,
        envEdited: {},
        form: { ...state.form, providers: [{ descriptor: d.id, variant, values, secrets: {}, extra: {} }] },
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
      return withProvider(state, { ...p, variant: action.variant, values: { ...(p.values ?? {}), $variant: action.variant } })
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
    case 'introspection':
      return { ...state, introspection: action.result }
    case 'probe':
      return { ...state, probe: action.result }
    case 'resource':
      return { ...state, form: { ...state.form, resource: { ...state.form.resource, ...action.patch } } }
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
  for (const [key, ref] of Object.entries(state.form.providers[0]?.secrets ?? {})) {
    const v = state.testSecrets[key]
    if (v) out[ref.env] = v
  }
  return out
}
