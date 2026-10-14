import { useEffect, useState } from 'react'

import { api, unwrap } from './api/client'
import type { Health, PublicSettings } from './api/types'
import { Configs } from './configs/Configs'
import { takeHandOff } from './configs/handoff'
import { CreateWizard } from './create/CreateWizard'
import { SqlProvider } from './create/sql/state'
import { SqlWizard } from './create/sql/SqlWizard'
import { CreateProvider } from './create/state'
import { TemplatePicker } from './create/TemplatePicker'
import { CodeWorkspace } from './create/code/CodeWorkspace'
import { EjectPage } from './create/EjectPage'
import { SpecProviderPage } from './create/SpecProviderPage'
import { NewCodeProvider } from './create/code/NewCodeProvider'
import { TsProvider } from './create/timeseries/state'
import { TimeseriesWizard } from './create/timeseries/TimeseriesWizard'
import { WizardProvider } from './wizard/state'
import { Wizard, type WizardLoad } from './wizard/Wizard'

type Route = { hash: string; load: WizardLoad | null; loadKey: string }

/** The route, and what the wizard opens with (J2/J3 or an imported snippet). */
function routeFor(hash: string): Route {
  if (hash.startsWith('#/configure/edit')) {
    const q = new URLSearchParams(hash.split('?')[1] ?? '')
    const intent = q.get('intent')
    const load: WizardLoad = {
      kind: 'target',
      target: q.get('target') ?? '',
      id: q.get('id') ?? '',
      intent: intent === 'provider' || intent === 'duplicate' ? intent : 'edit',
    }
    return { hash, load, loadKey: hash }
  }
  if (hash.startsWith('#/configure/import')) {
    const h = takeHandOff()
    return { hash, load: h ? { kind: 'import', form: h.form } : null, loadKey: `import:${Date.now()}` }
  }
  return { hash, load: null, loadKey: '' }
}

function useRoute(): [Route, (h: string) => void] {
  const [route, setRoute] = useState(() => routeFor(window.location.hash || '#/'))
  useEffect(() => {
    const on = () => setRoute(routeFor(window.location.hash || '#/'))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return [route, (h: string) => (window.location.hash = h)]
}

function Home({
  health,
  settings,
  onConfigure,
  onCreate,
}: {
  health: Health | null
  settings: PublicSettings | null
  onConfigure: () => void
  onCreate: () => void
}) {
  const writable = settings?.targets.find((t) => t.kind !== 'export')
  return (
    <div className="home">
      <p className="lead">Publish data with pygeoapi without writing YAML or Python.</p>
      <div className="mode-cards">
        <section className="card mode">
          <h2>Configure</h2>
          <p>Create a collection from a CSV, GeoJSON or GeoPackage file, or a PostGIS table, using pygeoapi's built-in providers.</p>
          <button type="button" className="primary" onClick={onConfigure}>
            Create a collection
          </button>
        </section>
        <section className="card mode">
          <h2>Create</h2>
          <p>Build a provider for a source pygeoapi can't read today, such as an agency's JSON API or a SQL query, from a template and without code.</p>
          <button type="button" className="primary" onClick={onCreate}>
            Create a provider
          </button>
        </section>
      </div>
      {settings && settings.targets.length > 0 && (
        <section className="card mode">
          <h2>Manage</h2>
          <p>
            See the collections a server publishes, edit them, add a provider, delete them, import YAML, and roll back a publish
            {writable ? ` on ${writable.title}` : ''}.
          </p>
          <a className="button" href="#/configs">
            Manage collections
          </a>
        </section>
      )}
      {health && (
        <p className="muted small">
          pygeoapi {health.pygeoapi} · tests run{' '}
          {health.runner === 'docker' ? `in ${health.image} (sandbox pygeoapi ${health.sandbox?.pygeoapi ?? '…'})` : 'in a local subprocess'}
        </p>
      )}
      {health?.warnings.map((w) => (
        <p key={w} className="notice warning">
          {w}
        </p>
      ))}
    </div>
  )
}

export function App() {
  const [{ hash, load, loadKey }, go] = useRoute()
  const [settings, setSettings] = useState<PublicSettings | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [session, setSession] = useState(0)

  useEffect(() => {
    api
      .GET('/api/settings')
      .then((r) => setSettings(unwrap(r) as unknown as PublicSettings))
      .catch((e: unknown) => setError(`The backend isn't reachable: ${String(e)}`))
    api
      .GET('/api/health')
      .then((r) => setHealth(unwrap(r) as unknown as Health))
      .catch(() => undefined)
  }, [])

  const inWizard = hash.startsWith('#/configure')
  const inCreate = hash.startsWith('#/create')
  const createRoute = hash.startsWith('#/create/http')
    ? 'http'
    : hash.startsWith('#/create/sql')
      ? 'sql'
      : hash.startsWith('#/create/timeseries')
        ? 'timeseries'
        : hash.startsWith('#/create/code')
          ? 'code'
          : 'pick'
  const codeId = hash.startsWith('#/code/') ? decodeURIComponent(hash.slice('#/code/'.length).split('?')[0] ?? '') : null
  const ejectId = hash.startsWith('#/eject/') ? decodeURIComponent(hash.slice('#/eject/'.length).split('?')[0] ?? '') : null
  const specId = hash.startsWith('#/provider/') ? decodeURIComponent(hash.slice('#/provider/'.length).split('?')[0] ?? '') : null
  const inConfigs = hash.startsWith('#/configs')
  const query = new URLSearchParams(hash.split('?')[1] ?? '')
  const descriptor = query.get('descriptor')
  const loadTarget = load?.kind === 'target' ? load.target : null
  const wizardTarget = loadTarget ?? settings?.targets[0]?.id
  return (
    <>
      <header className="app-header">
        <a href="#/" className="brand">
          pygeoapi Provider Builder
        </a>
        <nav aria-label="Main" className="main-nav">
          {!inConfigs && settings && settings.targets.length > 0 && (
            <a href="#/configs" className="small">
              Collections
            </a>
          )}
          {(inWizard || inCreate || ejectId) && (
            <a href="#/" className="small">
              Leave the wizard
            </a>
          )}
        </nav>
      </header>
      <main id="main">
        {error && <p className="notice error">{error}</p>}
        {inConfigs && <Configs initialTarget={query.get('target')} />}
        {!inWizard && !inCreate && !inConfigs && !codeId && !ejectId && !specId && (
          <Home
            health={health}
            settings={settings}
            onConfigure={() => (setSession((s) => s + 1), go('#/configure/new'))}
            onCreate={() => (setSession((s) => s + 1), go('#/create/new'))}
          />
        )}
        {inWizard && settings && wizardTarget && (hash.startsWith('#/configure/import') ? load?.kind === 'import' : true) && (
          <WizardProvider key={`${session}:${descriptor ?? ''}:${loadKey}`} target={wizardTarget}>
            <Wizard settings={settings} initialDescriptor={descriptor} load={load} />
          </WizardProvider>
        )}
        {hash.startsWith('#/configure/import') && load?.kind !== 'import' && (
          <p className="notice info">
            Nothing to import. <a href="#/configs">Import YAML from the collections page.</a>
          </p>
        )}
        {inCreate && createRoute === 'pick' && <TemplatePicker />}
        {inCreate && createRoute === 'http' && (
          <CreateProvider key={session}>
            <CreateWizard />
          </CreateProvider>
        )}
        {inCreate && createRoute === 'sql' && (
          <SqlProvider key={session}>
            <SqlWizard />
          </SqlProvider>
        )}
        {inCreate && createRoute === 'timeseries' && (
          <TsProvider key={session}>
            <TimeseriesWizard />
          </TsProvider>
        )}
        {inCreate && createRoute === 'code' && <NewCodeProvider />}
        {codeId && <CodeWorkspace key={codeId} id={codeId} />}
        {ejectId && <EjectPage key={ejectId} id={ejectId} />}
        {specId && <SpecProviderPage key={specId} id={specId} />}
        {inWizard && settings && !settings.targets.length && <p className="notice error">No publish targets are configured.</p>}
      </main>
    </>
  )
}
