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
  /** Variables the provider reads itself (a created provider's spec placeholders). */
  env?: { name: string; secret?: boolean; help?: string }[]
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

// ------------------------------------------------------------------ targets (J2, J3, publish)

export interface TargetInfo {
  id: string
  title: string
  kind: 'export' | 'file' | 'admin'
  server_url?: string | null
  config: string
  reload: 'none' | 'command' | 'signal' | 'watch'
  writes_openapi: boolean
}

export interface ResourceSummary {
  id: string
  type: string | null
  title: string
  providers: { type: string | null; name: string | null; descriptor: string | null; default: boolean; version: string | null }[]
  editable: boolean
  warnings: string[]
}

export interface LoadedResource {
  etag: string
  yaml: string
  form: import('./client').FormState
  editable: boolean
  warnings: string[]
}

export interface ImportedResource {
  resource_id: string
  form: import('./client').FormState
  editable: boolean
  warnings: string[]
}

export interface PublishPlan {
  target: string
  kind: string
  action: 'add' | 'update' | 'delete'
  resource_id: string
  changed: boolean
  etag: string
  etag_after: string
  diff: string
  notes: { path: string; message: string }[]
  findings: import('./client').Finding[]
  blocking: boolean
  env: import('./client').EnvVar[]
  warnings: string[]
}

export interface PublishStepResult {
  name: string
  ok: boolean
  message: string
  detail?: unknown
}

export interface LogEntry {
  id: string
  time: string
  target: string
  action: 'add' | 'update' | 'delete' | 'rollback'
  resource_id: string
  providers: string[]
  status: 'published' | 'written' | 'verify-failed' | 'failed'
  config_hash_before: string
  config_hash_after?: string | null
  backup?: string | null
  openapi_backup?: string | null
  rollback_of?: string | null
  message?: string | null
  rolled_back_by?: string | null
}

export interface PublishResult {
  ok: boolean
  steps: PublishStepResult[]
  entry: LogEntry | null
  plan?: PublishPlan
  conflict?: boolean
}

export interface VerifyResult {
  ok: boolean
  checks: { url: string; status: number | null; ok: boolean; detail?: string | null }[]
  message?: string
}

// ------------------------------------------------------------------ Mode B (J4)

export type PropertyType = 'string' | 'integer' | 'number' | 'boolean' | 'datetime'

export interface SpecProperty {
  name: string
  type: PropertyType
  from?: string
  format?: string
}

export type GeometrySpec =
  | { type: 'point'; x: string; y: string; crs?: string }
  | { type: 'wkt' | 'geojson'; field: string; crs?: string }
  | { type: 'none' }

export type PaginationStyle = 'none' | 'offset' | 'page' | 'next_link' | 'cursor'

export interface Pagination {
  style: PaginationStyle
  offset_param?: string
  limit_param?: string
  max_page_size?: number
  page?: { page_param: string; page_start?: number; size_param?: string }
  next_link?: { next: string }
  cursor?: { cursor_param: string; cursor_path: string }
  total?: string
}

export interface Filters {
  bbox?: { param: string; format: string }
  datetime?: { param?: string; start_param?: string; end_param?: string; format?: string }
  properties?: Record<string, string>
  sortby?: { param: string; format?: string; properties: string[] }
  q?: { param: string }
}

export type AuthSpec =
  | { type: 'header' | 'query'; name: string; value: string }
  | { type: 'bearer'; value: string }
  | { type: 'basic'; username: string; password: string }

export interface SourceSpec {
  url: string
  query?: Record<string, string>
  headers?: Record<string, string>
  auth?: AuthSpec
}

export interface SpecDoc {
  spec_version: 1
  template: 'http-features'
  id: string
  title: string
  description?: string
  version?: string
  source: SourceSpec
  records: string
  item_id: { from: string }
  geometry: GeometrySpec
  properties: SpecProperty[]
  time_field?: string
  title_field?: string
  pagination?: Pagination
  filters?: Filters
  limits?: { max_records_scanned?: number; max_pages?: number }
}

export interface SuggestColumn {
  name: string
  path: string
  type: string
  format: string | null
  nulls: number
  distinct: number
  unique: boolean
  examples: unknown[]
}

export interface Suggestions {
  records: string | null
  record_count: number
  columns: SuggestColumn[]
  item_id?: { from: string; candidates: string[] } | null
  geometry?: GeometrySpec
  properties?: SpecProperty[]
  time_field?: string | null
  title_field?: string | null
  pagination?: Pagination & { hints: string[] }
  notes: string[]
}

export interface SampleData {
  status: number
  url: string
  headers: Record<string, string>
  bytes: number
  ms: number
  json?: unknown
  text?: string
}

export interface Problem {
  pointer: string
  message: string
  hint?: string | null
}

export interface ModeBResult<T> extends SandboxResult<T> {
  suggestions?: Suggestions
  problems?: Problem[]
  egress?: string[]
}

export type CheckStatus = 'pass' | 'fail' | 'warn' | 'skip' | 'unsupported'

export interface HarnessCheck {
  id: string
  level: 'L1' | 'L2' | 'L3'
  title: string
  status: CheckStatus
  message: string
  hint?: string | null
  details?: unknown
  ms: number
}

export interface HarnessData {
  checks: HarnessCheck[]
  summary: Record<CheckStatus, number>
  ok: boolean
  ms: number
}

export interface SpecCheck {
  ok: boolean
  problems: Problem[]
  text: string
  id?: string
  version?: string | null
  env?: string[]
  hosts?: string[]
}

export interface SavedProvider {
  id: string
  version: string
  data: string
  descriptor: string
}

