// Types for endpoints that return free-form JSON (catalog, sandbox results).
// Shapes mirror backend/provider_builder/{catalog,service}.py and the harness.

export type ProviderType = 'feature' | 'coverage' | 'record' | 'map' | 'tile' | 'edr' | 'stac'

export interface Availability {
  status: 'ready' | 'missing' | 'unknown'
  missing: string[]
}

export interface CatalogCard {
  id: string
  plugin: string
  provider_type: ProviderType
  title: string
  summary: string
  ogc_api: string[]
  origin: 'core' | 'plugin' | 'created'
  docs?: string | null
  requires: string[]
  capabilities: Record<string, boolean>
  notes: string[]
  verdict?: { status: string; note?: string } | null
  version?: string | null
  availability: Availability
}

export interface UiField {
  key: string
  type: string
  widget: string
  title: string
  help?: string | null
  required: boolean
  default?: unknown
  suggest?: unknown
  choices?: unknown[] | null
  choices_from?: string | null
  field_types?: string[] | null
  extensions?: string[] | null
  advanced: boolean
  unit?: string | null
  min?: number | null
  max?: number | null
  env_name?: string | null
  geoconnex: boolean
  source?: string | string[] | null
}

export interface UiGroup {
  id: 'connect' | 'map'
  title: string
  keys: string[]
  advanced: string[]
}

export interface UiVariant {
  id: string
  title: string
  help?: string | null
  keys: string[]
}

export interface DescriptorDetail extends CatalogCard {
  json_schema: Record<string, unknown>
  ui: { groups: UiGroup[]; variants: UiVariant[]; fields: Record<string, UiField> }
  probe?: { kind: string; copy_data?: boolean } | null
  introspect?: { what: 'csv' | 'geojson' | 'sqlite' | 'sql'; dialect?: string } | null
}

export interface Explanation {
  code: string
  message: string
  hint?: string | null
}

export interface SandboxResult<T> {
  ok: boolean
  result?: T | null
  runner?: string
  ms?: number
  logs?: string
  error?: { type: string; message: string; traceback?: string } | null
  explanation?: Explanation
}

export interface Feature {
  type: 'Feature'
  id?: string | number
  geometry: { type: string; coordinates: unknown } | null
  properties: Record<string, unknown> | null
}

export interface FeatureCollection {
  type: 'FeatureCollection'
  features: Feature[]
  numberMatched?: number | null
  numberReturned?: number | null
}

export interface FieldStats {
  nulls: number
  values: unknown[]
  types: string[]
  distinct: number
}

export interface ProbeData {
  provider_type: string
  provider_class: string
  fields: Record<string, { type: string; [k: string]: unknown }>
  fields_cached: boolean
  sample?: FeatureCollection
  sample_stats?: Record<string, FieldStats>
  extent?: { from: 'full' | 'sample'; bbox: number[] | null; temporal: string[] | null }
  id_stats?: { count: number; nulls: number; duplicates: number }
  timings: Record<string, number>
  note?: string
}

export interface IntrospectColumn {
  name: string
  type: string
  sample?: unknown[]
  geometry?: boolean
  pk?: boolean
}

export interface IntrospectTable {
  name: string
  schema?: string
  kind?: string
  columns: IntrospectColumn[]
}

export interface IntrospectData {
  columns?: IntrospectColumn[]
  tables?: IntrospectTable[]
  features_have_id?: boolean
  count?: number
}

export interface FsEntry {
  name: string
  kind: 'dir' | 'file'
  size: number | null
  server_path: string
}

export interface FsListing {
  root: string
  server_path: string
  parent: string | null
  entries: FsEntry[]
}

export interface Health {
  status: string
  pygeoapi: string
  runner: string
  image: string
  sandbox: { pygeoapi: string; python: string } | null
  warnings: string[]
}

export interface PublicSettings {
  data_roots: { name: string; server_path: string }[]
  targets: { id: string; title: string; kind: string; server_url?: string | null; base_config: string }[]
  geoconnex: boolean
  runner: string
}

export interface VerifyResult {
  ok: boolean
  checks: { url: string; status: number | null; ok: boolean; detail?: string | null }[]
  message?: string
}
