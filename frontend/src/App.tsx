import { useEffect, useState } from 'react'

import { api, unwrap } from './api/client'
import type { Health, PublicSettings } from './api/types'
import { CreateWizard } from './create/CreateWizard'
import { CreateProvider } from './create/state'
import { WizardProvider } from './wizard/state'
import { Wizard } from './wizard/Wizard'

function useHash(): [string, (h: string) => void] {
  const [hash, setHash] = useState(() => window.location.hash || '#/')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return [hash, (h: string) => (window.location.hash = h)]
}

function Home({ health, onConfigure, onCreate }: { health: Health | null; onConfigure: () => void; onCreate: () => void }) {
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
          <p>Build a provider for a source pygeoapi can't read today, such as an agency's JSON API, from a template and without code.</p>
          <button type="button" className="primary" onClick={onCreate}>
            Create a provider
          </button>
        </section>
      </div>
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
  const [hash, go] = useHash()
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
  const descriptor = new URLSearchParams(hash.split('?')[1] ?? '').get('descriptor')
  return (
    <>
      <header className="app-header">
        <a href="#/" className="brand">
          pygeoapi Provider Builder
        </a>
        {(inWizard || inCreate) && (
          <a href="#/" className="small">
            Leave the wizard
          </a>
        )}
      </header>
      <main id="main">
        {error && <p className="notice error">{error}</p>}
        {!inWizard && !inCreate && (
          <Home
            health={health}
            onConfigure={() => (setSession((s) => s + 1), go('#/configure/new'))}
            onCreate={() => (setSession((s) => s + 1), go('#/create/new'))}
          />
        )}
        {inWizard && settings && settings.targets[0] && (
          <WizardProvider key={`${session}:${descriptor ?? ''}`} target={settings.targets[0].id}>
            <Wizard settings={settings} initialDescriptor={descriptor} />
          </WizardProvider>
        )}
        {inCreate && (
          <CreateProvider key={session}>
            <CreateWizard />
          </CreateProvider>
        )}
        {inWizard && settings && !settings.targets.length && <p className="notice error">No publish targets are configured.</p>}
      </main>
    </>
  )
}
