// State for J4, create a provider from the http-features template (PLAN.md §7.4).
// The test secret lives here, in memory only, like J1's; the spec itself only
// ever holds the ${VAR} placeholder.
import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'

import type {
  Filters,
  GeometrySpec,
  HarnessData,
  ModeBResult,
  Pagination,
  ProbeData,
  SampleData,
  SavedProvider,
  SpecDoc,
  SpecProperty,
  Suggestions,
} from '../api/types'

export const CREATE_STEPS = [
  { id: 'source', title: 'Source' },
  { id: 'sample', title: 'Sample' },
  { id: 'map', title: 'Map fields' },
  { id: 'paging', title: 'Paging and filters' },
  { id: 'check', title: 'Check' },
  { id: 'save', title: 'Save' },
] as const

export type CreateStepId = (typeof CREATE_STEPS)[number]['id']
export type AuthType = 'none' | 'header' | 'query' | 'bearer' | 'basic'

export interface KeyValue {
  key: string
  value: string
}

export interface AuthDraft {
  type: AuthType
  name: string
  username: string
  env: string
}

export interface PropertyDraft extends SpecProperty {
  include: boolean
}

export interface CreateState {
  step: CreateStepId
  url: string
  query: KeyValue[]
  headers: KeyValue[]
  auth: AuthDraft
  /** The secret's test value, by variable name (never persisted). */
  testSecret: string
  sample: ModeBResult<SampleData> | null
  suggestions: Suggestions | null
  records: string | null
  itemId: string
  geometry: GeometrySpec
  properties: PropertyDraft[]
  timeField: string
  titleField: string
  pagination: Pagination
  filters: Filters
  limits: { max_records_scanned: number; max_pages: number }
  preview: ModeBResult<ProbeData> | null
  harness: ModeBResult<HarnessData> | null
  meta: { id: string; title: string; description: string; version: string; changes: string }
  idEdited: boolean
  saved: SavedProvider | null
}

export function initialCreateState(): CreateState {
  return {
    step: 'source',
    url: '',
    query: [],
    headers: [],
    auth: { type: 'none', name: 'X-API-Key', username: '', env: 'API_KEY' },
    testSecret: '',
    sample: null,
    suggestions: null,
    records: null,
    itemId: '',
    geometry: { type: 'none' },
    properties: [],
    timeField: '',
    titleField: '',
    pagination: { style: 'none' },
    filters: {},
    limits: { max_records_scanned: 10000, max_pages: 50 },
    preview: null,
    harness: null,
    meta: { id: '', title: '', description: '', version: '1.0.0', changes: '' },
    idEdited: false,
    saved: null,
  }
}

export type CreateAction =
  | { type: 'goto'; step: CreateStepId }
  | { type: 'source'; patch: Partial<Pick<CreateState, 'url' | 'query' | 'headers' | 'auth'>> }
  | { type: 'testSecret'; value: string }
  | { type: 'sample'; result: ModeBResult<SampleData> | null }
  | { type: 'suggestions'; suggestions: Suggestions }
  | { type: 'mapping'; patch: Partial<Pick<CreateState, 'itemId' | 'geometry' | 'properties' | 'timeField' | 'titleField'>> }
  | { type: 'paging'; patch: Partial<Pick<CreateState, 'pagination' | 'filters' | 'limits'>> }
  | { type: 'preview'; result: ModeBResult<ProbeData> | null }
  | { type: 'harness'; result: ModeBResult<HarnessData> | null }
  | { type: 'meta'; patch: Partial<CreateState['meta']>; idEdited?: boolean }
  | { type: 'saved'; saved: SavedProvider }

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
}

/** Every change to what the spec says invalidates the preview and the harness. */
const stale = { preview: null, harness: null, saved: null }

function applySuggestions(state: CreateState, s: Suggestions): CreateState {
  const pagination = Object.fromEntries(Object.entries(s.pagination ?? { style: 'none' }).filter(([k]) => k !== 'hints'))
  return {
    ...state,
    ...stale,
    suggestions: s,
    records: s.records,
    itemId: s.item_id?.from ?? '',
    geometry: s.geometry ?? { type: 'none' },
    properties: (s.properties ?? []).map((p) => ({ ...p, include: true })),
    timeField: s.time_field ?? '',
    titleField: s.title_field ?? '',
    pagination: pagination as Pagination,
  }
}

export function createReducer(state: CreateState, action: CreateAction): CreateState {
  switch (action.type) {
    case 'goto':
      return { ...state, step: action.step }
    case 'source':
      return { ...state, ...action.patch, ...stale, sample: null }
    case 'testSecret':
      return { ...state, testSecret: action.value, ...stale }
    case 'sample': {
      const next = { ...state, ...stale, sample: action.result }
      return action.result?.suggestions ? applySuggestions(next, action.result.suggestions) : next
    }
    case 'suggestions':
      return applySuggestions(state, action.suggestions)
    case 'mapping':
      return { ...state, ...action.patch, ...stale }
    case 'paging':
      return { ...state, ...action.patch, ...stale }
    case 'preview':
      return { ...state, preview: action.result }
    case 'harness':
      return { ...state, harness: action.result }
    case 'meta': {
      const meta = { ...state.meta, ...action.patch }
      const idEdited = action.idEdited ?? state.idEdited
      if (action.patch.title !== undefined && !idEdited) meta.id = slugify(action.patch.title)
      return { ...state, meta, idEdited, saved: null }
    }
    case 'saved':
      return { ...state, saved: action.saved }
  }
}

function pairs(list: KeyValue[]): Record<string, string> | undefined {
  const out = Object.fromEntries(list.filter((kv) => kv.key.trim()).map((kv) => [kv.key.trim(), kv.value]))
  return Object.keys(out).length ? out : undefined
}

export function sourceOf(state: CreateState): SpecDoc['source'] {
  const source: SpecDoc['source'] = { url: state.url.trim() }
  const query = pairs(state.query)
  const headers = pairs(state.headers)
  if (query) source.query = query
  if (headers) source.headers = headers
  const placeholder = '${' + state.auth.env + '}'
  switch (state.auth.type) {
    case 'header':
    case 'query':
      source.auth = { type: state.auth.type, name: state.auth.name, value: placeholder }
      break
    case 'bearer':
      source.auth = { type: 'bearer', value: placeholder }
      break
    case 'basic':
      source.auth = { type: 'basic', username: state.auth.username, password: placeholder }
      break
  }
  return source
}

/** The spec the wizard has built so far; the backend writes it as YAML. */
/** Query parameters the paging style sets on every request, so they don't belong in source.query. */
function pagingParams(p: Pagination): Set<string> {
  return new Set([p.offset_param, p.limit_param, p.page?.page_param, p.page?.size_param, p.cursor?.cursor_param].filter((x): x is string => Boolean(x)))
}

export function buildSpec(state: CreateState): SpecDoc {
  const included = state.properties.filter((p) => p.include)
  const source = sourceOf(state)
  const paging = pagingParams(state.pagination)
  if (source.query) {
    const query = Object.fromEntries(Object.entries(source.query).filter(([k]) => !paging.has(k)))
    if (Object.keys(query).length) source.query = query
    else delete source.query
  }
  const spec: SpecDoc = {
    spec_version: 1,
    template: 'http-features',
    id: state.meta.id || 'new-provider',
    title: state.meta.title || 'New provider',
    ...(state.meta.description ? { description: state.meta.description } : {}),
    ...(state.meta.version ? { version: state.meta.version } : {}),
    source,
    records: state.records ?? '$',
    item_id: { from: state.itemId },
    geometry: state.geometry,
    properties: included.map((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== 'include')) as SpecProperty),
  }
  if (state.timeField && included.some((p) => p.name === state.timeField)) spec.time_field = state.timeField
  if (state.titleField && included.some((p) => p.name === state.titleField)) spec.title_field = state.titleField
  if (state.pagination.style !== 'none' || state.pagination.total) spec.pagination = state.pagination
  if (Object.keys(state.filters).length) spec.filters = state.filters
  spec.limits = state.limits
  return spec
}

/** The test secret, under the variable name the spec uses. */
export function secretsOf(state: CreateState): Record<string, string> {
  return state.auth.type !== 'none' && state.testSecret ? { [state.auth.env]: state.testSecret } : {}
}

const Ctx = createContext<{ state: CreateState; dispatch: Dispatch<CreateAction> } | null>(null)

export function CreateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(createReducer, undefined, initialCreateState)
  return <Ctx.Provider value={{ state, dispatch }}>{children}</Ctx.Provider>
}

export function useCreate() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useCreate outside CreateProvider')
  return ctx
}
