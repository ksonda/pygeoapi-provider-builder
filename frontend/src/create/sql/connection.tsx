// A SQL connection form (PostgreSQL fields or a DSN in an environment variable,
// or a SQLite/GeoPackage file in a data root), shared by the SQL template and
// the time-series template's SQL sources. The test secret stays in memory.
import { useState } from 'react'

import type { SqlDialect, SqlSource } from '../../api/types'
import { FileBrowser } from '../../forms/FileBrowser'
import { ENV_RE, FieldError, described } from '../shared'

export interface SqlConnection {
  dialect: SqlDialect
  connMode: 'fields' | 'dsn'
  host: string
  port: string
  dbname: string
  user: string
  /** The variable that holds the password (fields) or the connection string (dsn). */
  secretEnv: string
  testSecret: string
  searchPath: string
  path: string
  statementTimeout: number
}

export function initialConnection(secretEnv = 'DB_PASSWORD'): SqlConnection {
  return {
    dialect: 'postgresql',
    connMode: 'fields',
    host: '',
    port: '5432',
    dbname: '',
    user: '',
    secretEnv,
    testSecret: '',
    searchPath: '',
    path: '',
    statementTimeout: 10,
  }
}

const placeholder = (name: string) => '${' + name + '}'

export function connectionSource(c: SqlConnection): SqlSource {
  const timeout = { statement_timeout_s: c.statementTimeout }
  if (c.dialect === 'sqlite') return { dialect: 'sqlite', path: c.path.trim(), ...timeout }
  const searchPath = c.searchPath
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  const extra = { ...(searchPath.length ? { search_path: searchPath } : {}), ...timeout }
  if (c.connMode === 'dsn') return { dialect: 'postgresql', dsn: placeholder(c.secretEnv), ...extra }
  const port = Number(c.port)
  return {
    dialect: 'postgresql',
    connection: {
      host: c.host.trim(),
      ...(c.port.trim() ? { port: Number.isInteger(port) ? port : c.port.trim() } : {}),
      dbname: c.dbname.trim(),
      user: c.user.trim(),
      password: placeholder(c.secretEnv),
    },
    ...extra,
  }
}

export function connectionSecrets(c: SqlConnection): Record<string, string> {
  return c.dialect === 'postgresql' && c.testSecret ? { [c.secretEnv]: c.testSecret } : {}
}

export function connectionErrors(c: SqlConnection, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {}
  if (c.dialect === 'postgresql') {
    if (c.connMode === 'fields') {
      if (!c.host.trim()) out[`${prefix}db-host`] = 'Host is required.'
      if (!c.dbname.trim()) out[`${prefix}db-name`] = 'Database is required.'
      if (!c.user.trim()) out[`${prefix}db-user`] = 'User is required.'
    }
    if (!ENV_RE.test(c.secretEnv)) out[`${prefix}secret-env`] = 'Environment variable: upper-case letters, digits and underscores, starting with a letter.'
  } else if (!c.path.startsWith('/')) {
    out[`${prefix}db-path`] = 'File: the path the pygeoapi server sees, starting with /.'
  }
  return out
}

const DB_EXTENSIONS = ['.sqlite', '.sqlite3', '.db', '.gpkg']

interface Props {
  value: SqlConnection
  onChange: (patch: Partial<SqlConnection>) => void
  errors: Record<string, string>
  showErrors: boolean
  /** Prefixes element ids, for forms with two connections. */
  prefix?: string
  legend?: string
}

export function SqlConnectionFields({ value: c, onChange, errors, showErrors, prefix = '', legend = 'Database' }: Props) {
  const [browsing, setBrowsing] = useState(false)
  const id = (name: string) => `${prefix}${name}`
  const pg = c.dialect === 'postgresql'
  return (
    <>
      <fieldset>
        <legend>{legend}</legend>
        <label className="radio">
          <input type="radio" name={id('dialect')} checked={pg} onChange={() => onChange({ dialect: 'postgresql' })} /> PostgreSQL or PostGIS
        </label>
        <label className="radio">
          <input type="radio" name={id('dialect')} checked={!pg} onChange={() => onChange({ dialect: 'sqlite' })} /> A SQLite or GeoPackage file
        </label>
      </fieldset>
      {pg && (
        <fieldset>
          <legend>Connection</legend>
          <label className="radio">
            <input type="radio" name={id('conn-mode')} checked={c.connMode === 'fields'} onChange={() => onChange({ connMode: 'fields', secretEnv: c.secretEnv.replace(/_DSN$/, '_PASSWORD') })} /> Host and database
          </label>
          <label className="radio">
            <input type="radio" name={id('conn-mode')} checked={c.connMode === 'dsn'} onChange={() => onChange({ connMode: 'dsn', secretEnv: c.secretEnv.replace(/_PASSWORD$/, '_DSN') })} /> A connection string, kept in an environment variable
          </label>
          {c.connMode === 'fields' && (
            <div className="inline-fields">
              <label>
                Host <span aria-hidden="true">*</span>
                <input id={id('db-host')} value={c.host} placeholder="db.example.org" onChange={(e) => onChange({ host: e.target.value })} {...described(id('db-host'), errors, showErrors)} />
              </label>
              <label>
                Port
                <input id={id('db-port')} value={c.port} inputMode="numeric" onChange={(e) => onChange({ port: e.target.value })} />
              </label>
              <label>
                Database <span aria-hidden="true">*</span>
                <input id={id('db-name')} value={c.dbname} onChange={(e) => onChange({ dbname: e.target.value })} {...described(id('db-name'), errors, showErrors)} />
              </label>
              <label>
                User <span aria-hidden="true">*</span>
                <input id={id('db-user')} value={c.user} autoComplete="off" onChange={(e) => onChange({ user: e.target.value })} {...described(id('db-user'), errors, showErrors)} />
              </label>
            </div>
          )}
          <FieldError id={id('db-host')} errors={errors} show={showErrors} />
          <FieldError id={id('db-name')} errors={errors} show={showErrors} />
          <FieldError id={id('db-user')} errors={errors} show={showErrors} />
          <label htmlFor={id('secret-env')}>Environment variable for the {c.connMode === 'dsn' ? 'connection string' : 'password'}</label>
          <p className="muted small" id={`${id('secret-env')}-help`}>
            The spec holds <code>${'{'}
            {c.secretEnv || 'NAME'}
            {'}'}</code>, never the secret. The pygeoapi server must define this variable.
          </p>
          <input id={id('secret-env')} value={c.secretEnv} onChange={(e) => onChange({ secretEnv: e.target.value.toUpperCase() })} {...described(id('secret-env'), errors, showErrors, 'help')} />
          <FieldError id={id('secret-env')} errors={errors} show={showErrors} />
          <label htmlFor={id('secret-test')}>{c.connMode === 'dsn' ? 'Connection string' : 'Password'} to test with</label>
          <p className="muted small" id={`${id('secret-test')}-help`}>
            Kept in this page's memory only, and sent to the sandbox by variable name.
            {c.connMode === 'dsn' && ' For example postgresql://reader:password@db.example.org:5432/gis.'}
          </p>
          <input id={id('secret-test')} type="password" autoComplete="off" value={c.testSecret} onChange={(e) => onChange({ testSecret: e.target.value })} aria-describedby={`${id('secret-test')}-help`} />
          <label htmlFor={id('search-path')}>Schemas to search (optional)</label>
          <p className="muted small" id={`${id('search-path')}-help`}>
            Comma-separated, for tables the query names without a schema.
          </p>
          <input id={id('search-path')} value={c.searchPath} placeholder="public" onChange={(e) => onChange({ searchPath: e.target.value })} aria-describedby={`${id('search-path')}-help`} />
          <p className="notice info small">Connect with a role that can only read. The provider runs every query read-only, with a time limit, but a read-only role is the safest.</p>
        </fieldset>
      )}
      {!pg && (
        <fieldset>
          <legend>Database file</legend>
          <label htmlFor={id('db-path')}>
            File <span aria-hidden="true">*</span>
          </label>
          <p className="muted small" id={`${id('db-path')}-help`}>
            As the pygeoapi server sees it, inside a data root.
          </p>
          <div className="inline-fields">
            <input id={id('db-path')} value={c.path} placeholder="/data/wells.gpkg" onChange={(e) => onChange({ path: e.target.value })} {...described(id('db-path'), errors, showErrors, 'help')} />
            <button type="button" onClick={() => setBrowsing(true)}>
              Browse…
            </button>
          </div>
          <FieldError id={id('db-path')} errors={errors} show={showErrors} />
          <FileBrowser open={browsing} start="/" extensions={DB_EXTENSIONS} onPick={(path) => (onChange({ path }), setBrowsing(false))} onClose={() => setBrowsing(false)} />
        </fieldset>
      )}
    </>
  )
}
