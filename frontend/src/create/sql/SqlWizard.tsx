// The J4 wizard frame for the sql-features template: the same step list,
// focus management and "Next" gating as the HTTP template.
import { useEffect, useMemo, useRef, useState } from 'react'

import { ErrorSummary } from '../../components/common'
import { StepList, metaErrors } from '../shared'
import { connectionErrors } from './connection'
import { SQL_STEPS, connectionOf, useSql, type SqlState, type SqlStepId } from './state'
import { SqlCheckStep, SqlMapStep, SqlSaveStep, SqlSourceStep } from './steps'

/** Where the error summary sends focus for errors that aren't one input. */
const FOCUS: Record<string, string> = { sample: 'sql-query', preview: 'item-id', harness: 'step-heading', properties: 'item-id' }

function stepErrors(state: SqlState, step: SqlStepId): Record<string, string> {
  const out: Record<string, string> = {}
  switch (step) {
    case 'source': {
      Object.assign(out, connectionErrors(connectionOf(state)))
      if (!state.query.trim()) out['sql-query'] = 'Write the SELECT query to publish.'
      else if (!state.sample?.ok) out.sample = 'Run the query successfully before you continue.'
      return out
    }
    case 'map': {
      if (!state.itemId) out['item-id'] = 'Item id: choose the column that identifies each row.'
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
      return metaErrors(state.meta)
  }
}

export function SqlWizard() {
  const { state, dispatch } = useSql()
  const heading = useRef<HTMLHeadingElement>(null)
  const [showErrors, setShowErrors] = useState(false)
  const index = SQL_STEPS.findIndex((s) => s.id === state.step)
  const step = SQL_STEPS[index] ?? SQL_STEPS[0]
  const errors = useMemo(() => stepErrors(state, state.step), [state])

  useEffect(() => heading.current?.focus(), [state.step])

  const next = () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    const nxt = SQL_STEPS[index + 1]
    setShowErrors(false)
    if (nxt) dispatch({ type: 'goto', step: nxt.id })
  }
  const back = () => {
    const prev = SQL_STEPS[index - 1]
    setShowErrors(false)
    if (prev) dispatch({ type: 'goto', step: prev.id })
  }
  const props = { errors, showErrors }

  return (
    <div className="wizard">
      <StepList steps={SQL_STEPS} index={index} />
      <section className="step" aria-labelledby="step-heading">
        <h2 id="step-heading" ref={heading} tabIndex={-1}>
          <span className="muted">Step {index + 1} of {SQL_STEPS.length}:</span> {step.title}
          <span className="muted small"> · Create a provider from a SQL query</span>
        </h2>
        {showErrors && <ErrorSummary errors={Object.entries(errors)} onGo={(key) => document.getElementById(FOCUS[key] ?? key)?.focus()} />}
        {state.step === 'source' && <SqlSourceStep {...props} />}
        {state.step === 'map' && <SqlMapStep {...props} />}
        {state.step === 'check' && <SqlCheckStep {...props} />}
        {state.step === 'save' && <SqlSaveStep {...props} />}
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
