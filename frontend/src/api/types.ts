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
  /** Path fields: a file, a folder, or either. */
  path_kind?: 'file' | 'folder' | 'any' | null
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
  /** The variant's own fields: two variants may define one key differently (a path or a URL). */
  fields?: Record<string, UiField>
}

export interface DescriptorDetail extends CatalogCard {
  json_schema: Record<string, unknown>
  ui: { groups: UiGroup[]; variants: UiVariant[]; fields: Record<string, UiField> }
  probe?: { kind: string; copy_data?: boolean } | null
  introspect?: {
    what: 'csv' | 'geojson' | 'sqlite' | 'sql' | 'ogr' | 'esri'
    dialect?: string
    /** The form key that picks a table or layer (default: table). */
    table_key?: string
    /** The form key the lookup needs filled first (default: data). */
    source_key?: string
  } | null
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
  extent?: { from: 'full' | 'sample' | 'locations'; bbox: number[] | null; temporal: string[] | null }
  id_stats?: { count: number; nulls: number; duplicates: number }
  timings: Record<string, number>
  note?: string
  /** EDR providers: the query types they offer and their parameters. */
  query_types?: string[]
  parameters?: string[]
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


// ------------------------------------------------------------------ Mode B (J4): the SQL template

export type SqlDialect = 'postgresql' | 'sqlite'

export interface PgConnection {
  host: string
  port?: number | string
  dbname: string
  user: string
  password?: string
}

export type SqlSource =
  | { dialect: 'postgresql'; connection: PgConnection; search_path?: string[]; connect_timeout_s?: number; statement_timeout_s?: number }
  | { dialect: 'postgresql'; dsn: string; search_path?: string[]; connect_timeout_s?: number; statement_timeout_s?: number }
  | { dialect: 'sqlite'; path: string; statement_timeout_s?: number }

export type SqlEncoding = 'native' | 'wkb' | 'wkt' | 'geojson' | 'gpkg' | 'xy' | 'none'

export type SqlGeometry =
  | { encoding: 'native' | 'wkb' | 'wkt' | 'geojson' | 'gpkg'; column: string; crs?: string }
  | { encoding: 'xy'; x: string; y: string; crs?: string }
  | { encoding: 'none' }

export type SqlIdType = 'string' | 'integer' | 'number'

export interface SqlSpecDoc {
  spec_version: 1
  template: 'sql-features'
  id: string
  title: string
  description?: string
  version?: string
  source: SqlSource
  query: string
  item_id: { from: string; type?: SqlIdType }
  geometry: SqlGeometry
  properties: SpecProperty[]
  time_field?: string
  title_field?: string
  limits?: { max_rows?: number }
}

export interface SqlColumn {
  name: string
  kinds: string[]
  db_type: string | null
  geometry: SqlEncoding | null
  nulls: number
  distinct: number
  unique: boolean
  examples: unknown[]
  type?: PropertyType
  format?: string | null
}

export interface SqlSuggestions {
  record_count: number
  columns: SqlColumn[]
  item_id: { from: string; type: SqlIdType; candidates: string[] } | null
  geometry: SqlGeometry
  properties: SpecProperty[]
  time_field: string | null
  title_field: string | null
  query: string | null
  notes: string[]
}

export interface SqlSampleData {
  columns: SqlColumn[]
  rows: unknown[][]
  row_count: number
  query: string
  ms: number
}

export type SqlSampleResult = Omit<ModeBResult<SqlSampleData>, 'suggestions'> & { suggestions?: SqlSuggestions }

// ------------------------------------------------------------------ Mode B (J4): the time-series template

export type EdrQueryType = 'locations' | 'position' | 'area' | 'cube'

export interface TsParameter {
  id: string
  label: string
  description?: string
  unit: { symbol: string; label?: string }
  observed_property?: string
  unit_uri?: string
  upstream?: string
}

export interface TsHttpLocations {
  source: SourceSpec
  records: string
  item_id: { from: string }
  geometry: GeometrySpec
  properties: SpecProperty[]
  title_field?: string
}

export interface TsSqlLocations {
  source: SqlSource
  query: string
  item_id: { from: string; type?: SqlIdType }
  geometry: SqlGeometry
  properties: SpecProperty[]
  title_field?: string
}

export interface TsHttpObservations {
  url: string
  query?: Record<string, string>
  auth?: AuthSpec
  records: string
  error_path?: string
  time: { from: string; format?: string }
  value: { from: string }
}

export interface TsSqlObservations {
  source: SqlSource
  query: string
  time: { from: string; format?: string }
  value: { from: string }
}

export interface TsSpecDoc {
  spec_version: 1
  template: 'timeseries-edr'
  id: string
  title: string
  description?: string
  version?: string
  locations: { from: 'http'; http: TsHttpLocations } | { from: 'sql'; sql: TsSqlLocations }
  parameters: TsParameter[]
  observations: ({ from: 'http'; http: TsHttpObservations } | { from: 'sql'; sql: TsSqlObservations }) & {
    default_window?: string
    max_window?: string
  }
  query_types?: EdrQueryType[]
  position?: { snap_radius_km: number }
  limits?: { max_locations?: number; max_observations?: number }
}

export interface ObservationSuggestions {
  records: string | null
  record_count: number
  columns: SuggestColumn[]
  time?: { from: string; format: string } | null
  value?: { from: string } | null
  notes: string[]
}

export type ObservationSampleResult = Omit<ModeBResult<SampleData & SqlSampleData>, 'suggestions'> & {
  suggestions?: ObservationSuggestions & Partial<SqlSuggestions>
  request?: { url: string; query: Record<string, string> }
}

// ------------------------------------------------------------------ Mode B (J5): the code workspace

export type CodeProviderType = 'feature' | 'record' | 'coverage' | 'edr' | 'tile' | 'map'

export interface CodeSummary {
  id: string
  title: string | null
  provider_type: CodeProviderType | null
  versions: string[]
  descriptor: string
}

export interface CodeDetail {
  id: string
  title: string | null
  provider_type: CodeProviderType | null
  package: string
  class_name: string
  plugin: string
  descriptor: string
  python_path: string
  files: string[]
  changed: string[]
  versions: string[]
  remote: string | null
  env: string[]
  /** Set when the package was ejected from a template-built provider's spec. */
  ejected_from: { spec: string; version: string; template: string; date: string } | null
}

/** A template-built provider (a spec in the workspace), as GET /api/workspace lists it. */
export interface SpecProviderSummary {
  id: string
  title: string | null
  template: string
  versions: string[]
  descriptor: string
}

export interface Ejection {
  version: string
  code_id: string
  descriptor: string
  date: string
}

export interface SpecProviderDetail {
  id: string
  descriptor: string
  template: string
  ejected: Ejection[]
  changelog: string
  versions: { version: string; data: string; spec: string; harness: { ok?: boolean } | null }[]
}

export interface Diagnostic {
  code: string | null
  message: string
  line: number | null
  column: number | null
  end_line: number | null
  fixable: boolean
}

/** A test image build (J6): docker build, then an offline smoke test in the image. */
export interface ImageResult {
  ok: boolean
  tag: string
  id?: string
  size?: number | null
  stage: 'build' | 'smoke' | 'done'
  smoke?: string
  log: string
  run?: string
}

export interface PushResult {
  remote: string
  branch: string
  commit: string
  tags: string[]
  compare_url: string | null
}

export interface UsageResult {
  collections: {
    target: string
    target_title: string
    resource_id: string
    title: string
    version: string | null
    via: 'spec path' | 'package'
  }[]
  errors: { target: string; error: string }[]
  history: { id: string; time: string; target: string; action: string; resource_id: string; providers: string[]; status: string; rolled_back_by?: string | null }[]
}
