// State for J4 with the sql-features template (PLAN.md §7.2, §7.4). As with the
// HTTP template, the test secret (a password or a connection string) lives in
// memory only; the spec holds its ${VAR} placeholder.
import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'

import type {
  HarnessData,
  ModeBResult,
  ProbeData,
  SavedProvider,
  SpecProperty,
  SqlDialect,
  SqlGeometry,
  SqlIdType,
  SqlSampleResult,
  SqlSource,
  SqlSpecDoc,
  SqlSuggestions,
} from '../../api/types'
import { nextMeta, type Meta } from '../shared'
import { connectionSecrets, connectionSource, type SqlConnection } from './connection'

export const SQL_STEPS = [
  { id: 'source', title: 'Database and query' },
  { id: 'map', title: 'Map fields' },
  { id: 'check', title: 'Check' },
  { id: 'save', title: 'Save' },
] as const

export type SqlStepId = (typeof SQL_STEPS)[number]['id']

export interface PropertyDraft extends SpecProperty {
  include: boolean
}

export interface SqlState {
  step: SqlStepId
  dialect: SqlDialect
  connMode: 'fields' | 'dsn'
  host: string
  port: string
  dbname: string
  user: string
  /** The variable that holds the password (fields) or the connection string (dsn). */
  secretEnv: string
  /** The secret's test value (never persisted). */
  testSecret: string
  searchPath: string
  path: string
  query: string
  statementTimeout: number
  sample: SqlSampleResult | null
  suggestions: SqlSuggestions | null
  itemId: string
  itemIdType: SqlIdType
  geometry: SqlGeometry
  crs: string
  properties: PropertyDraft[]
  timeField: string
  titleField: string
  maxRows: number
  preview: ModeBResult<ProbeData> | null
  harness: ModeBResult<HarnessData> | null
  meta: Meta
  idEdited: boolean
  saved: SavedProvider | null
}

export function initialSqlState(): SqlState {
  return {
    step: 'source',
    dialect: 'postgresql',
    connMode: 'fields',
    host: '',
    port: '5432',
    dbname: '',
    user: '',
    secretEnv: 'DB_PASSWORD',
    testSecret: '',
    searchPath: '',
    path: '',
    query: '',
    statementTimeout: 10,
    sample: null,
    suggestions: null,
    itemId: '',
    itemIdType: 'string',
    geometry: { encoding: 'none' },
    crs: '',
    properties: [],
    timeField: '',
    titleField: '',
    maxRows: 10000,
    preview: null,
    harness: null,
    meta: { id: '', title: '', description: '', version: '1.0.0', changes: '' },
    idEdited: false,
    saved: null,
  }
}

type SourceFields = 'dialect' | 'connMode' | 'host' | 'port' | 'dbname' | 'user' | 'secretEnv' | 'searchPath' | 'path' | 'query' | 'statementTimeout'
type MappingFields = 'itemId' | 'itemIdType' | 'geometry' | 'crs' | 'properties' | 'timeField' | 'titleField' | 'maxRows'

export type SqlAction =
  | { type: 'goto'; step: SqlStepId }
  | { type: 'source'; patch: Partial<Pick<SqlState, SourceFields>> }
  | { type: 'testSecret'; value: string }
  | { type: 'sample'; result: SqlSampleResult | null }
  | { type: 'mapping'; patch: Partial<Pick<SqlState, MappingFields>> }
  | { type: 'preview'; result: ModeBResult<ProbeData> | null }
  | { type: 'harness'; result: ModeBResult<HarnessData> | null }
  | { type: 'meta'; patch: Partial<Meta>; idEdited?: boolean }
  | { type: 'saved'; saved: SavedProvider }

/** Every change to what the spec says invalidates the preview and the harness. */
const stale = { preview: null, harness: null, saved: null }

function applySuggestions(state: SqlState, s: SqlSuggestions): SqlState {
  return {
    ...state,
    suggestions: s,
    itemId: s.item_id?.from ?? '',
    itemIdType: s.item_id?.type ?? 'string',
    geometry: s.geometry,
    properties: s.properties.map((p) => ({ ...p, include: true })),
    timeField: s.time_field ?? '',
    titleField: s.title_field ?? '',
  }
}

export function sqlReducer(state: SqlState, action: SqlAction): SqlState {
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
    case 'mapping':
      return { ...state, ...action.patch, ...stale }
    case 'preview':
      return { ...state, preview: action.result }
    case 'harness':
      return { ...state, harness: action.result }
    case 'meta': {
      const idEdited = action.idEdited ?? state.idEdited
      return { ...state, meta: nextMeta(state.meta, action.patch, idEdited), idEdited, saved: null }
    }
    case 'saved':
      return { ...state, saved: action.saved }
  }
}

export function connectionOf(state: SqlState): SqlConnection {
  const { dialect, connMode, host, port, dbname, user, secretEnv, testSecret, searchPath, path, statementTimeout } = state
  return { dialect, connMode, host, port, dbname, user, secretEnv, testSecret, searchPath, path, statementTimeout }
}

export function sourceOf(state: SqlState): SqlSource {
  return connectionSource(connectionOf(state))
}

/** The test secret, under the variable name the spec uses. */
export function secretsOf(state: SqlState): Record<string, string> {
  return connectionSecrets(connectionOf(state))
}

export function buildSqlSpec(state: SqlState): SqlSpecDoc {
  const included = state.properties.filter((p) => p.include)
  const geometry = { ...state.geometry } as SqlGeometry
  if (geometry.encoding !== 'none' && state.crs.trim()) geometry.crs = state.crs.trim()
  const spec: SqlSpecDoc = {
    spec_version: 1,
    template: 'sql-features',
    id: state.meta.id || 'new-provider',
    title: state.meta.title || 'New provider',
    ...(state.meta.description ? { description: state.meta.description } : {}),
    ...(state.meta.version ? { version: state.meta.version } : {}),
    source: sourceOf(state),
    query: state.query,
    item_id: { from: state.itemId, type: state.itemIdType },
    geometry,
    properties: included.map((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== 'include')) as SpecProperty),
    limits: { max_rows: state.maxRows },
  }
  if (state.timeField && included.some((p) => p.name === state.timeField)) spec.time_field = state.timeField
  if (state.titleField && included.some((p) => p.name === state.titleField)) spec.title_field = state.titleField
  return spec
}

const Ctx = createContext<{ state: SqlState; dispatch: Dispatch<SqlAction> } | null>(null)

export function SqlProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(sqlReducer, undefined, initialSqlState)
  return <Ctx.Provider value={{ state, dispatch }}>{children}</Ctx.Provider>
}

export function useSql() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useSql outside SqlProvider')
  return ctx
}
