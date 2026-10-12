// Review and Publish for file and Admin API targets (PLAN.md §6.4): the plan's
// diff and findings, then the publish job (OpenAPI, write, reload, verify).
import { useEffect, useMemo, useRef, useState } from 'react'

import { api, unwrap, waitForJob, type Finding } from '../api/client'
import type { PublishPlan, PublishResult, SandboxResult } from '../api/types'
import { FindingList, Spinner } from '../components/common'
import { ConflictError, DiffView, planPublish, PublishOutcome, runPublish, type PublishBody } from '../configs/publishing'
import { probeSummaries, type StepProps } from './steps'
import { runSecrets, useWizard, type WizardState } from './state'

const ACK_SECRETS = 'admin-secrets'

function body(state: WizardState): PublishBody {
  const edit = state.mode.kind === 'edit' ? state.mode : null
  return {
    target: state.target,
    action: edit ? 'update' : 'add',
    resource_id: edit ? edit.resourceId : state.form.resource_id,
    form: state.form,
    etag: edit ? edit.etag : null,
    probes: probeSummaries(state),
    acknowledge: state.acknowledge,
  }
}

function hasSecrets(state: WizardState): boolean {
  return state.form.providers.some((p) => Object.keys(p.secrets ?? {}).length > 0 || JSON.stringify(p.extra ?? {}).includes('${'))
}

export function PlanReview({ settings, goToField, onBlocking }: StepProps & { onBlocking: (b: boolean | null) => void }) {
  const { state, dispatch } = useWizard()
  const [plan, setPlan] = useState<PublishPlan | null>(null)
  const [error, setError] = useState<{ message: string; diff: string | null } | null>(null)
  const target = settings.targets.find((t) => t.id === state.target)
  const adding = state.mode.kind === 'new'
  const [openapi, setOpenapi] = useState<SandboxResult<{ paths: string[] }> | 'running' | null>(adding ? 'running' : null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    planPublish(body(state))
      .then(setPlan)
      .catch((e: unknown) => setError({ message: e instanceof Error ? e.message : String(e), diff: e instanceof ConflictError ? e.diff : null }))
    // A new collection can be checked now; an edit is checked by the publish itself.
    if (adding) {
      api
        .POST('/api/openapi-check', { body: { form: state.form, target: state.target, test_secrets: runSecrets(state) } })
        .then((r) => waitForJob<SandboxResult<{ paths: string[] }>>(unwrap(r).job_id))
        .then(setOpenapi)
        .catch((e: unknown) => setOpenapi({ ok: false, explanation: { code: 'client', message: String(e) } }))
    }
  }, [state, adding])

  const findings: Finding[] = useMemo(() => {
    const out = [...(plan?.findings ?? [])]
    if (openapi && openapi !== 'running' && !openapi.ok) {
      out.push({
        layer: 'openapi',
        severity: 'error',
        code: openapi.explanation?.code ?? 'openapi',
        message: `pygeoapi couldn't build its OpenAPI document with this collection: ${openapi.explanation?.message ?? 'unknown error'}`,
        hint: openapi.explanation?.hint ?? null,
        pointer: null,
        source: null,
        details: null,
      })
    }
    return out
  }, [plan, openapi])

  const needsAck = target?.kind === 'admin' && hasSecrets(state)
  const acked = state.acknowledge.includes(ACK_SECRETS)
  const blocking =
    error !== null
      ? true
      : plan
        ? plan.blocking || !plan.changed || (openapi !== null && openapi !== 'running' && !openapi.ok) || (needsAck && !acked)
        : null
  useEffect(() => onBlocking(openapi === 'running' ? null : blocking), [blocking, openapi, onBlocking])

  const go = (f: Finding) => {
    const ptr = f.pointer ?? ''
    const m = /^\/providers\/(\d+)\/values\/(.+)$/.exec(ptr)
    if (m?.[2] && Number(m[1]) === state.index) {
      const key = m[2]
      const connect = state.detail?.ui.groups.find((g) => g.id === 'connect')
      const inConnect = connect?.keys.includes(key) || connect?.advanced.includes(key) || state.detail?.ui.variants.some((v) => v.keys.includes(key))
      goToField(inConnect ? 'connect' : 'fields', key)
    } else if (ptr === '/resource_id' || ptr.startsWith('/resource')) {
      goToField('describe', ptr === '/resource_id' ? 'resource_id' : (ptr.split('/')[2] ?? 'title'))
    }
  }

  if (error)
    return (
      <div role="alert">
        <p className="error-text">{error.message}</p>
        {error.diff && <DiffView diff={error.diff} label="What changed on disk since you loaded it" />}
        {error.diff && (
          <p>
            <a href="#/configs">Back to the collections</a> to load the current config.
          </p>
        )}
      </div>
    )
  if (!plan) return <Spinner label="Working out the change and checking it against pygeoapi's rules…" />
  return (
    <>
      <h3>Checks</h3>
      <FindingList findings={findings} onGo={go} />
      {openapi === 'running' && <Spinner label="Generating pygeoapi's OpenAPI document with this collection (this loads the provider)…" />}
      {openapi && openapi !== 'running' && openapi.ok && <p className="ok-text">pygeoapi generated its OpenAPI document with this collection.</p>}
      {!adding && <p className="muted small">Publishing generates pygeoapi's OpenAPI document with the new config first, and stops if it fails.</p>}
      {plan.warnings.map((w) => (
        <p key={w} className="notice warning">
          {w}
        </p>
      ))}
      {needsAck && (
        <p className="notice warning">
          <label>
            <input type="checkbox" checked={acked} onChange={(e) => dispatch({ type: 'acknowledge', value: ACK_SECRETS, on: e.target.checked })} /> I understand that this
            server's Admin API publishes its resolved config, secrets included, in <code>/openapi</code>.
          </label>
        </p>
      )}
      <h3>Changes to {target?.base_config}</h3>
      {plan.changed ? (
        <>
          <p>Only these lines change. A backup is taken first, so you can roll back.</p>
          <DiffView diff={plan.diff} label={`Changes to ${target?.base_config ?? 'the config'}`} />
          {plan.notes.length > 0 && (
            <>
              <p>These blocks are rewritten in the file's style, so their layout may change slightly:</p>
              <ul>
                {plan.notes.map((n) => (
                  <li key={n.path}>
                    <code>{n.path}</code>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      ) : (
        <p className="notice info">Nothing to publish: the config already says this.</p>
      )}
      {plan.env.length > 0 && (
        <>
          <h3>Environment variables the server needs</h3>
          <ul>
            {plan.env.map((e) => (
              <li key={e.name}>
                <code>{e.name}</code> {e.secret ? '(secret)' : ''} for {e.used_by.join(', ')}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}

export function PublishRun({ settings }: StepProps) {
  const { state } = useWizard()
  const [result, setResult] = useState<PublishResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const target = settings.targets.find((t) => t.id === state.target)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    runPublish({ ...body(state), test_secrets: runSecrets(state) })
      .then(setResult)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [state])

  if (error)
    return (
      <p className="error-text" role="alert">
        {error}
      </p>
    )
  if (!result)
    return <Spinner label={`Publishing to ${target?.title ?? 'the server'}: generating OpenAPI, writing the config, reloading pygeoapi and checking the collection…`} />
  return (
    <>
      <PublishOutcome result={result} serverUrl={target?.server_url} />
      <p>
        <a href="#/configs">See all collections on {target?.title}</a>
      </p>
    </>
  )
}
