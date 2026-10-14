// The J4 wizard frame: the same step list, focus management and "Next" gating as J1.
import { useEffect, useMemo, useRef, useState } from 'react'

import { ErrorSummary } from '../components/common'
import { CREATE_STEPS, useCreate, type CreateState, type CreateStepId } from './state'
import { ENV_RE } from './shared'
import { CheckStep, MapStep, PagingStep, SampleStep, SaveStep, SourceStep } from './steps'

const ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$/

/** Where the error summary sends focus for errors that aren't one input. */
const FOCUS: Record<string, string> = { sample: 'url', preview: 'item-id', harness: 'step-heading', properties: 'item-id' }

function stepErrors(state: CreateState, step: CreateStepId): Record<string, string> {
  const out: Record<string, string> = {}
  switch (step) {
    case 'source': {
      if (!/^https?:\/\/[^/?#\s]+/.test(state.url.trim())) out.url = 'API URL: enter an http or https address.'
      if (state.auth.type !== 'none' && !ENV_RE.test(state.auth.env)) out['auth-env'] = 'Environment variable: upper-case letters, digits and underscores, starting with a letter.'
      const ok = state.sample?.ok && (state.sample.result?.status ?? 500) < 300 && state.suggestions?.records
      if (!ok) out.sample = 'Fetch a sample that returns a list of records before you continue.'
      return out
    }
    case 'map': {
      if (!state.itemId) out['item-id'] = 'Item id: choose the field that identifies each record.'
      const names = state.properties.filter((p) => p.include).map((p) => p.name)
      if (!names.length) out.properties = 'Publish at least one property.'
      else if (new Set(names).size !== names.length) out.properties = 'Property names must be different.'
      if (!state.preview?.ok) out.preview = 'Preview the spec in the sandbox before you continue.'
      return out
    }
    case 'check':
      if (!state.harness?.ok || !state.harness.result?.ok) out.harness = 'Every contract check must pass (warnings are fine) before you save.'
      return out
    case 'save':
      if (!state.meta.title.trim()) out.title = 'Title is required.'
      if (!ID_RE.test(state.meta.id)) out['provider-id'] = 'Provider id: lower-case letters, digits and hyphens.'
      if (!SEMVER.test(state.meta.version)) out.version = 'Version: a semantic version such as 1.0.0.'
      return out
    default:
      return out
  }
}

export function CreateWizard() {
  const { state, dispatch } = useCreate()
  const heading = useRef<HTMLHeadingElement>(null)
  const [showErrors, setShowErrors] = useState(false)
  const index = CREATE_STEPS.findIndex((s) => s.id === state.step)
  const step = CREATE_STEPS[index] ?? CREATE_STEPS[0]
  const errors = useMemo(() => stepErrors(state, state.step), [state])

  useEffect(() => heading.current?.focus(), [state.step])

  const next = () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    const nxt = CREATE_STEPS[index + 1]
    setShowErrors(false)
    if (nxt) dispatch({ type: 'goto', step: nxt.id })
  }
  const back = () => {
    const prev = CREATE_STEPS[index - 1]
    setShowErrors(false)
    if (prev) dispatch({ type: 'goto', step: prev.id })
  }
  const props = { errors, showErrors }

  return (
    <div className="wizard">
      <nav aria-label="Steps" className="steps">
        <ol>
          {CREATE_STEPS.map((s, i) => (
            <li key={s.id} className={i === index ? 'current' : i < index ? 'done' : ''} aria-current={i === index ? 'step' : undefined}>
              <span className="step-num" aria-hidden="true">
                {i < index ? '✓' : i + 1}
              </span>
              <span>{s.title}</span>
            </li>
          ))}
        </ol>
      </nav>
      <section className="step" aria-labelledby="step-heading">
        <h2 id="step-heading" ref={heading} tabIndex={-1}>
          <span className="muted">Step {index + 1} of {CREATE_STEPS.length}:</span> {step.title}
          <span className="muted small"> · Create a provider</span>
        </h2>
        {showErrors && <ErrorSummary errors={Object.entries(errors)} onGo={(key) => document.getElementById(FOCUS[key] ?? key)?.focus()} />}
        {state.step === 'source' && <SourceStep {...props} />}
        {state.step === 'sample' && <SampleStep />}
        {state.step === 'map' && <MapStep {...props} />}
        {state.step === 'paging' && <PagingStep />}
        {state.step === 'check' && <CheckStep {...props} />}
        {state.step === 'save' && <SaveStep {...props} />}
        <div className="nav-buttons">
          {index > 0 && (
            <button type="button" onClick={back}>
              Back
            </button>
          )}
          {state.step !== 'save' && (
            <button type="button" className="primary" onClick={next}>
              Next
            </button>
          )}
        </div>
      </section>
    </div>
  )
}
