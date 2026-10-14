// Mode B's first choice (PLAN.md §7): which template to build a provider from, or Python.
import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { CodeSummary, SpecProviderSummary } from '../api/types'

export interface TemplateCard {
  id: string
  href: string
  title: string
  api: string
  summary: string
}

export const TEMPLATES: TemplateCard[] = [
  {
    id: 'http-features',
    href: '#/create/http',
    title: 'An HTTP JSON API',
    api: 'OGC API - Features',
    summary: "An agency's REST API that returns JSON or GeoJSON: map its records, paging and filters, without code.",
  },
  {
    id: 'sql-features',
    href: '#/create/sql',
    title: 'A SQL query',
    api: 'OGC API - Features',
    summary: 'A join, a view or computed columns over PostgreSQL/PostGIS, SQLite or a GeoPackage, run read-only.',
  },
  {
    id: 'timeseries-edr',
    href: '#/create/timeseries',
    title: 'Time series at monitoring locations',
    api: 'OGC API - EDR',
    summary: 'Stations, gages or wells with observations over time, from an HTTP API or SQL, as CoverageJSON point series.',
  },
]

export function TemplatePicker() {
  const [code, setCode] = useState<CodeSummary[]>([])
  const [specs, setSpecs] = useState<SpecProviderSummary[]>([])
  useEffect(() => {
    api
      .GET('/api/code')
      .then((r) => setCode((r.data as unknown as CodeSummary[]) ?? []))
      .catch(() => undefined)
    api
      .GET('/api/workspace')
      .then((r) => setSpecs((r.data as unknown as SpecProviderSummary[]) ?? []))
      .catch(() => undefined)
  }, [])
  const names = Object.fromEntries(TEMPLATES.map((t) => [t.id, t.title]))
  return (
    <div className="home">
      <h1>Create a provider</h1>
      <p className="lead">Build a provider for a source pygeoapi can't read today. Pick what you have.</p>
      <ul className="cards template-cards">
        {TEMPLATES.map((t) => (
          <li key={t.id} className="card">
            <h2 className="h4">{t.title}</h2>
            <p className="muted small">→ {t.api}</p>
            <p>{t.summary}</p>
            <p className="card-foot">
              <a className="button primary" href={t.href} aria-label={`Start with ${t.title.toLowerCase()}`}>
                Start
              </a>
            </p>
          </li>
        ))}
        <li className="card">
          <h2 className="h4">Write Python</h2>
          <p className="muted small">→ any OGC API pygeoapi serves</p>
          <p>A package with every method pygeoapi calls, for features, records, coverages, EDR, tiles or maps, and the contract checks the builder runs.</p>
          <p className="card-foot">
            <a className="button" href="#/create/code" aria-label="Start a provider in Python">
              Start
            </a>
          </p>
        </li>
      </ul>
      {specs.length > 0 && (
        <section aria-labelledby="specs-heading">
          <h2 id="specs-heading">Your template-built providers</h2>
          <ul>
            {specs.map((p) => (
              <li key={p.id}>
                <a href={`#/provider/${encodeURIComponent(p.id)}`}>{p.title ?? p.id}</a>
                <span className="muted small">
                  {' '}
                  · {names[p.template] ?? p.template} · version {p.versions[0]} ·{' '}
                </span>
                <a className="small" href={`#/eject/${encodeURIComponent(p.id)}`} aria-label={`Eject ${p.title ?? p.id} to code`}>
                  Eject to code…
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
      {code.length > 0 && (
        <section aria-labelledby="code-heading">
          <h2 id="code-heading">Your code-built providers</h2>
          <ul>
            {code.map((c) => (
              <li key={c.id}>
                <a href={`#/code/${encodeURIComponent(c.id)}`}>{c.title ?? c.id}</a>
                <span className="muted small">
                  {' '}
                  · {c.provider_type} · {c.versions.length ? `version ${c.versions[0]}` : 'no version yet'}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
