// The J1 wizard frame: step list, focus management, and "Next" gating.
// Focus moves to the step heading on every step change (WCAG 2.4.3), and
// blocked "Next" presses show an error summary linked to the fields.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, unwrap } from '../api/client'
import type { DescriptorDetail, PublicSettings } from '../api/types'
import { ErrorSummary } from '../components/common'
import { fieldId } from '../forms/DescriptorForm'
import { validateProvider } from '../forms/validate'
import {
  BackendStep,
  ConnectStep,
  DescribeStep,
  FieldsStep,
  PublishStep,
  ReviewStep,
  StartStep,
  TestStep,
  type StepProps,
} from './steps'
import { STEPS, useWizard, type StepId, type WizardState } from './state'

const ID_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/

function stepErrors(state: WizardState, step: StepId): Record<string, string> {
  const d = state.detail
  const p = state.form.providers[0]
  switch (step) {
    case 'backend':
      return d ? {} : { backend: 'Choose a backend.' }
    case 'connect':
    case 'fields': {
      if (!d || !p) return {}
      const all = validateProvider(d.json_schema, d.ui.fields, p)
      const group = d.ui.groups.find((g) => g.id === (step === 'connect' ? 'connect' : 'map'))
      const keys = new Set([...(group?.keys ?? []), ...(group?.advanced ?? [])])
      if (step === 'connect') for (const v of d.ui.variants) if (v.id === (p.variant ?? d.ui.variants[0]?.id)) v.keys.forEach((k) => keys.add(k))
      return Object.fromEntries(Object.entries(all).filter(([k]) => keys.has(k)))
    }
    case 'test':
      return state.probe?.ok ? {} : { probe: 'The provider must load and return items before you continue.' }
    case 'describe': {
      const r = state.form.resource
      const out: Record<string, string> = {}
      if (!ID_RE.test(state.form.resource_id)) out.resource_id = 'Collection id: use letters, digits, ".", "_" or "-", starting and ending with a letter or digit.'
      if (!(typeof r.title === 'string' ? r.title.trim() : r.title)) out.title = 'Title is required.'
      if (!(typeof r.description === 'string' ? r.description.trim() : r.description)) out.description = 'Description is required.'
      return out
    }
    default:
      return {}
  }
}

export function Wizard({ settings, initialDescriptor }: { settings: PublicSettings; initialDescriptor?: string | null }) {
  const { state, dispatch } = useWizard()
  const heading = useRef<HTMLHeadingElement>(null)
  const [showErrors, setShowErrors] = useState(false)
  const [reviewBlocking, setReviewBlocking] = useState<boolean | null>(null)
  const pendingFocus = useRef<string | null>(null)
  const index = STEPS.findIndex((s) => s.id === state.step)
  const step = STEPS[index] ?? STEPS[0]
  const errors = useMemo(() => stepErrors(state, state.step), [state])

  // Opened from J4's "Publish a collection with this provider": start at Connect.
  useEffect(() => {
    if (!initialDescriptor) return
    let live = true
    api
      .GET('/api/catalog/{descriptor_id}', { params: { path: { descriptor_id: initialDescriptor } } })
      .then((r) => {
        if (!live) return
        dispatch({ type: 'descriptor', detail: unwrap(r) as unknown as DescriptorDetail })
        dispatch({ type: 'goto', step: 'connect' })
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [initialDescriptor, dispatch])

  useEffect(() => {
    const key = pendingFocus.current
    pendingFocus.current = null
    const el = key ? document.getElementById(['resource_id', 'title', 'description'].includes(key) ? key : fieldId(key)) : null
    ;(el ?? heading.current)?.focus()
  }, [state.step])

  const goToField = useCallback(
    (target: StepId, key: string) => {
      pendingFocus.current = key
      setShowErrors(false)
      dispatch({ type: 'goto', step: target })
    },
    [dispatch],
  )

  const next = () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    const nxt = STEPS[index + 1]
    setShowErrors(false)
    if (nxt) dispatch({ type: 'goto', step: nxt.id })
  }
  const back = () => {
    const prev = STEPS[index - 1]
    setShowErrors(false)
    if (prev) dispatch({ type: 'goto', step: prev.id })
  }

  const props: StepProps = { settings, showErrors, errors, goToField }
  const onBlocking = useCallback((b: boolean | null) => setReviewBlocking(b), [])
  const nextDisabled = state.step === 'review' && reviewBlocking !== false

  return (
    <div className="wizard">
      <nav aria-label="Steps" className="steps">
        <ol>
          {STEPS.map((s, i) => (
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
          <span className="muted">Step {index + 1} of {STEPS.length}:</span> {step.title}
          {state.detail && index > 1 ? <span className="muted small"> · {state.detail.title}</span> : null}
        </h2>
        {showErrors && (
          <ErrorSummary
            errors={Object.entries(errors)}
            onGo={(key) => document.getElementById(['resource_id', 'title', 'description'].includes(key) ? key : fieldId(key))?.focus()}
          />
        )}
        {state.step === 'start' && <StartStep {...props} />}
        {state.step === 'backend' && <BackendStep />}
        {state.step === 'connect' && state.detail && <ConnectStep {...props} />}
        {state.step === 'test' && <TestStep />}
        {state.step === 'fields' && state.detail && <FieldsStep {...props} />}
        {state.step === 'describe' && <DescribeStep {...props} />}
        {state.step === 'review' && <ReviewStep {...props} onBlocking={onBlocking} />}
        {state.step === 'publish' && <PublishStep {...props} />}
        <div className="nav-buttons">
          {index > 0 && (
            <button type="button" onClick={back}>
              Back
            </button>
          )}
          {state.step !== 'publish' && (
            <button type="button" className="primary" onClick={next} disabled={nextDisabled}>
              {state.step === 'review' ? 'Publish' : 'Next'}
            </button>
          )}
        </div>
      </section>
    </div>
  )
}
