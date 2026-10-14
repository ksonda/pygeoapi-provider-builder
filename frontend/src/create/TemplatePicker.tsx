// Mode B's first choice (PLAN.md §7): which template to build a provider from.
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
]

export function TemplatePicker() {
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
      </ul>
    </div>
  )
}
