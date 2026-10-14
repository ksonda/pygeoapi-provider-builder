// State for J4 with the timeseries-edr template (PLAN.md §7.2, §7.4): locations
// from an HTTP API or SQL, declared parameters, and observations from an HTTP URL
// template or SQL. Test secrets stay in memory; the spec holds ${VAR} placeholders.
import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'

import type {
  AuthSpec,
  EdrQueryType,
  GeometrySpec,
  HarnessData,
  ModeBResult,
  ObservationSampleResult,
  ProbeData,
  SampleData,
  SavedProvider,
  SpecProperty,
  SqlGeometry,
  SqlIdType,
  SqlSampleResult,
  Suggestions,
  TsParameter,
  TsSpecDoc,
} from '../../api/types'
import { nextMeta, type Meta } from '../shared'
import { connectionSecrets, connectionSource, initialConnection, type SqlConnection } from '../sql/connection'
import type { KeyValue } from '../state'

export const TS_STEPS = [
  { id: 'locations', title: 'Locations' },
  { id: 'parameters', title: 'Parameters' },
  { id: 'observations', title: 'Observations' },
  { id: 'queries', title: 'Time and queries' },
  { id: 'check', title: 'Check' },
  { id: 'save', title: 'Save' },
] as const

export type TsStepId = (typeof TS_STEPS)[number]['id']
export type AuthKind = 'none' | 'header' | 'query' | 'bearer'

export interface HttpDraft {
  url: string
  query: KeyValue[]
  authType: AuthKind
  authName: string
  authEnv: string
  testSecret: string
}

export interface PropertyDraft extends SpecProperty {
  include: boolean
}

export interface ParameterDraft {
  id: string
  label: string
  symbol: string
  unitLabel: string
  upstream: string
  observedProperty: string
  unitUri: string
}

export interface TsState {
  step: TsStepId
  // locations
  locKind: 'http' | 'sql'
  locHttp: HttpDraft
  locSql: SqlConnection
  locQuery: string
  locHttpSample: ModeBResult<SampleData> | null
  locSqlSample: SqlSampleResult | null
  locRecords: string
  locId: string
  locIdType: SqlIdType
  locHttpGeometry: GeometrySpec
  locSqlGeometry: SqlGeometry
  locName: string
  locProperties: PropertyDraft[]
  locPreview: ModeBResult<ProbeData> | null
  // parameters
  parameters: ParameterDraft[]
  // observations
  obsKind: 'http' | 'sql'
  obsHttp: HttpDraft
  obsRecords: string
  obsErrorPath: string
  obsSql: SqlConnection
  obsQuery: string
  obsTime: string
  obsTimeFormat: string
  obsValue: string
  obsLocation: string
  obsParameter: string
  obsWindow: string
  obsSample: ObservationSampleResult | null
  // time and queries
  defaultWindow: string
  maxWindow: string
  queryTypes: EdrQueryType[]
  snapKm: number
  maxLocations: number
  maxObservations: number
  // check and save
  harness: ModeBResult<HarnessData> | null
  meta: Meta
  idEdited: boolean
  saved: SavedProvider | null
}

function httpDraft(env: string): HttpDraft {
  return { url: '', query: [], authType: 'none', authName: 'X-API-Key', authEnv: env, testSecret: '' }
}

export function initialTsState(): TsState {
  return {
    step: 'locations',
    locKind: 'http',
    locHttp: httpDraft('LOCATIONS_API_KEY'),
    locSql: initialConnection('LOCATIONS_DB_PASSWORD'),
    locQuery: '',
    locHttpSample: null,
    locSqlSample: null,
    locRecords: '',
    locId: '',
    locIdType: 'string',
    locHttpGeometry: { type: 'none' },
    locSqlGeometry: { encoding: 'none' },
    locName: '',
    locProperties: [],
    locPreview: null,
    parameters: [{ id: '', label: '', symbol: '', unitLabel: '', upstream: '', observedProperty: '', unitUri: '' }],
    obsKind: 'http',
    obsHttp: httpDraft('OBSERVATIONS_API_KEY'),
    obsRecords: '',
    obsErrorPath: '',
    obsSql: initialConnection('OBSERVATIONS_DB_PASSWORD'),
    obsQuery: '',
    obsTime: '',
    obsTimeFormat: 'iso8601',
    obsValue: '',
    obsLocation: '',
    obsParameter: '',
    obsWindow: 'P1D',
    obsSample: null,
    defaultWindow: 'P7D',
    maxWindow: 'P31D',
    queryTypes: ['locations', 'position', 'area', 'cube'],
    snapKm: 1,
    maxLocations: 50,
    maxObservations: 10000,
    harness: null,
    meta: { id: '', title: '', description: '', version: '1.0.0', changes: '' },
    idEdited: false,
    saved: null,
  }
}

type Patchable = Omit<TsState, 'step' | 'harness' | 'meta' | 'idEdited' | 'saved'>

export type TsAction =
  | { type: 'goto'; step: TsStepId }
  /** A change to what the spec says: the harness result and the saved version are stale. */
  | { type: 'set'; patch: Partial<Patchable> }
  | { type: 'harness'; result: ModeBResult<HarnessData> | null }
  | { type: 'meta'; patch: Partial<Meta>; idEdited?: boolean }
  | { type: 'saved'; saved: SavedProvider }

export function tsReducer(state: TsState, action: TsAction): TsState {
  switch (action.type) {
    case 'goto':
      return { ...state, step: action.step }
    case 'set':
      return { ...state, ...action.patch, harness: null, saved: null }
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

/** The HTTP locations sample's suggestions, applied (records, id, geometry, name, properties). */
export function fromHttpSuggestions(s: Suggestions): Partial<Patchable> {
  return {
    locRecords: s.records ?? '',
    locId: s.item_id?.from ?? '',
    locHttpGeometry: s.geometry ?? { type: 'none' },
    locName: s.title_field ?? '',
    locProperties: (s.properties ?? []).map((p) => ({ ...p, include: true })),
    locPreview: null,
  }
}

const placeholder = (name: string) => '${' + name + '}'

function pairs(list: KeyValue[]): Record<string, string> | undefined {
  const out = Object.fromEntries(list.filter((kv) => kv.key.trim()).map((kv) => [kv.key.trim(), kv.value]))
  return Object.keys(out).length ? out : undefined
}

function authOf(d: HttpDraft): AuthSpec | undefined {
  if (d.authType === 'header' || d.authType === 'query') return { type: d.authType, name: d.authName, value: placeholder(d.authEnv) }
  if (d.authType === 'bearer') return { type: 'bearer', value: placeholder(d.authEnv) }
  return undefined
}

function httpSecrets(d: HttpDraft): Record<string, string> {
  return d.authType !== 'none' && d.testSecret ? { [d.authEnv]: d.testSecret } : {}
}

export function locationsSource(state: TsState) {
  const d = state.locHttp
  const query = pairs(d.query)
  const auth = authOf(d)
  return { url: d.url.trim(), ...(query ? { query } : {}), ...(auth ? { auth } : {}) }
}

function properties(state: TsState): SpecProperty[] {
  return state.locProperties
    .filter((p) => p.include)
    .map((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== 'include')) as SpecProperty)
}

export function locationsBlock(state: TsState): TsSpecDoc['locations'] {
  const props = properties(state)
  const title = state.locName && props.some((p) => p.name === state.locName && p.type === 'string') ? { title_field: state.locName } : {}
  if (state.locKind === 'sql') {
    return {
      from: 'sql',
      sql: {
        source: connectionSource(state.locSql),
        query: state.locQuery,
        item_id: { from: state.locId, type: state.locIdType },
        geometry: state.locSqlGeometry,
        properties: props.length ? props : [{ name: state.locId || 'id', type: 'string' }],
        ...title,
      },
    }
  }
  return {
    from: 'http',
    http: {
      source: locationsSource(state),
      records: state.locRecords || '$',
      item_id: { from: state.locId },
      geometry: state.locHttpGeometry,
      properties: props.length ? props : [{ name: 'id', type: 'string', from: state.locId || 'id' }],
      ...title,
    },
  }
}

/** A features spec of the locations alone, to preview them with the features engine. */
export function locationsSpec(state: TsState): Record<string, unknown> {
  const block = locationsBlock(state)
  const body = block.from === 'http' ? block.http : block.sql
  return {
    spec_version: 1,
    template: block.from === 'http' ? 'http-features' : 'sql-features',
    id: `${(state.meta.id || 'new-provider').slice(0, 50)}-locations`,
    title: `${state.meta.title || 'New provider'} (locations)`,
    ...body,
  }
}

export function parametersOf(state: TsState): TsParameter[] {
  return state.parameters.map((p) => ({
    id: p.id,
    label: p.label || p.id,
    unit: { symbol: p.symbol, ...(p.unitLabel ? { label: p.unitLabel } : {}) },
    ...(p.upstream && p.upstream !== p.id ? { upstream: p.upstream } : {}),
    ...(p.observedProperty ? { observed_property: p.observedProperty } : {}),
    ...(p.unitUri ? { unit_uri: p.unitUri } : {}),
  }))
}

export function observationsBlock(state: TsState): TsSpecDoc['observations'] {
  const windows = { default_window: state.defaultWindow, max_window: state.maxWindow }
  const time = { from: state.obsTime, ...(state.obsTimeFormat && state.obsTimeFormat !== 'iso8601' ? { format: state.obsTimeFormat } : {}) }
  if (state.obsKind === 'sql') {
    return { from: 'sql', sql: { source: connectionSource(state.obsSql), query: state.obsQuery, time, value: { from: state.obsValue } }, ...windows }
  }
  const d = state.obsHttp
  const query = pairs(d.query)
  const auth = authOf(d)
  return {
    from: 'http',
    http: {
      url: d.url.trim(),
      ...(query ? { query } : {}),
      ...(auth ? { auth } : {}),
      records: state.obsRecords || '$',
      ...(state.obsErrorPath ? { error_path: state.obsErrorPath } : {}),
      time,
      value: { from: state.obsValue },
    },
    ...windows,
  }
}

export function buildTsSpec(state: TsState): TsSpecDoc {
  return {
    spec_version: 1,
    template: 'timeseries-edr',
    id: state.meta.id || 'new-provider',
    title: state.meta.title || 'New provider',
    ...(state.meta.description ? { description: state.meta.description } : {}),
    ...(state.meta.version ? { version: state.meta.version } : {}),
    locations: locationsBlock(state),
    parameters: parametersOf(state),
    observations: observationsBlock(state),
    query_types: state.queryTypes,
    position: { snap_radius_km: state.snapKm },
    limits: { max_locations: state.maxLocations, max_observations: state.maxObservations },
  }
}

export function locationSecrets(state: TsState): Record<string, string> {
  return state.locKind === 'http' ? httpSecrets(state.locHttp) : connectionSecrets(state.locSql)
}

export function observationSecrets(state: TsState): Record<string, string> {
  return state.obsKind === 'http' ? httpSecrets(state.obsHttp) : connectionSecrets(state.obsSql)
}

export function secretsOf(state: TsState): Record<string, string> {
  return { ...locationSecrets(state), ...observationSecrets(state) }
}

const Ctx = createContext<{ state: TsState; dispatch: Dispatch<TsAction> } | null>(null)

export function TsProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(tsReducer, undefined, initialTsState)
  return <Ctx.Provider value={{ state, dispatch }}>{children}</Ctx.Provider>
}

export function useTs() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useTs outside TsProvider')
  return ctx
}
