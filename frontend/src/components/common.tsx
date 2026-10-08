import { yaml } from '@codemirror/lang-yaml'
import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { useEffect, useState } from 'react'

import type { Finding } from '../api/client'
import type { Explanation, FeatureCollection } from '../api/types'

function usePrefersDark(): boolean {
  const query = '(prefers-color-scheme: dark)'
  const [dark, setDark] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setDark(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return dark
}

export function YamlView({ text, label }: { text: string; label: string }) {
  const dark = usePrefersDark()
  return (
    <div className="yaml-view">
      <CodeMirror
        value={text}
        extensions={[yaml(), EditorView.lineWrapping, EditorView.contentAttributes.of({ 'aria-label': label, 'aria-readonly': 'true' })]}
        theme={dark ? 'dark' : 'light'}
        editable={false}
        readOnly
        basicSetup={{ foldGutter: false, highlightActiveLine: false }}
      />
    </div>
  )
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        })
      }}
    >
      {done ? 'Copied' : label}
    </button>
  )
}

export function SampleTable({ sample, columns }: { sample: FeatureCollection; columns?: string[] }) {
  const features = sample.features.slice(0, 10)
  const cols = columns?.length ? columns : [...new Set(features.flatMap((f) => Object.keys(f.properties ?? {})))]
  const show = (v: unknown) => (v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Sample items">
      <table>
        <caption className="muted">
          First {features.length} item(s){typeof sample.numberMatched === 'number' ? ` of ${sample.numberMatched}` : ''}
        </caption>
        <thead>
          <tr>
            <th scope="col">id</th>
            {cols.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {features.map((f, i) => (
            <tr key={String(f.id ?? i)}>
              <td className="mono">{show(f.id)}</td>
              {cols.map((c) => (
                <td key={c}>{show(f.properties?.[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ExplainedError({ explanation, error, logs }: {
  explanation?: Explanation
  error?: { type: string; message: string; traceback?: string } | null
  logs?: string
}) {
  return (
    <div className="notice error" role="alert">
      <p>
        <strong>{explanation?.message ?? error?.message ?? 'Something went wrong.'}</strong>
      </p>
      {explanation?.hint && <p>{explanation.hint}</p>}
      {(error?.traceback || logs) && (
        <details>
          <summary>Details</summary>
          {error && (
            <p className="mono small">
              {error.type}: {error.message}
            </p>
          )}
          {error?.traceback && <pre className="small">{error.traceback}</pre>}
          {logs && <pre className="small">{logs}</pre>}
        </details>
      )}
    </div>
  )
}

const LAYER_TITLES: Record<string, string> = {
  descriptor: 'Form checks',
  'pygeoapi-schema': "pygeoapi's config schema",
  'runtime-rules': 'pygeoapi runtime rules',
  probe: 'Provider test',
  openapi: 'OpenAPI generation (loads the provider)',
}

export function FindingList({ findings, onGo }: { findings: Finding[]; onGo?: (f: Finding) => void }) {
  if (!findings.length) return <p className="ok-text">No problems found.</p>
  const layers = [...new Set(findings.map((f) => f.layer))]
  return (
    <div className="findings">
      {layers.map((layer) => (
        <section key={layer}>
          <h3>{LAYER_TITLES[layer] ?? layer}</h3>
          <ul>
            {findings
              .filter((f) => f.layer === layer)
              .map((f, i) => (
                <li key={`${f.code}-${i}`} className={f.severity}>
                  <span className="badge">{f.severity === 'error' ? 'Error' : 'Warning'}</span> {f.message}
                  {f.hint && <span className="muted"> {f.hint}</span>}
                  {f.pointer && onGo && (
                    <>
                      {' '}
                      <button type="button" className="link" onClick={() => onGo(f)}>
                        Go to field
                      </button>
                    </>
                  )}
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

export function ErrorSummary({ errors, onGo }: { errors: [string, string][]; onGo: (key: string) => void }) {
  if (!errors.length) return null
  return (
    <div className="notice error" role="alert" tabIndex={-1} id="error-summary">
      <p>
        <strong>Fix {errors.length === 1 ? 'this' : `these ${errors.length} problems`} to continue:</strong>
      </p>
      <ul>
        {errors.map(([key, msg]) => (
          <li key={key}>
            <button type="button" className="link" onClick={() => onGo(key)}>
              {msg}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
