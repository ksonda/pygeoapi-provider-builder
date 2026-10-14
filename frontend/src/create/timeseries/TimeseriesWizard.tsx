// The J4 wizard frame for the timeseries-edr template: the same step list,
// focus management and "Next" gating as the other templates.
import { useEffect, useMemo, useRef, useState } from 'react'

import { ErrorSummary } from '../../components/common'
import { StepList, metaErrors } from '../shared'
import { connectionErrors } from '../sql/connection'
import { TS_STEPS, useTs, type TsState, type TsStepId } from './state'
import { TsCheckStep, TsLocationsStep, TsObservationsStep, TsParametersStep, TsQueriesStep, TsSaveStep } from './steps'

const URL_RE = /^https?:\/\/[^/?#\s]+/
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const DURATION_RE = /^P(\d+W|(\d+D)?(T(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?)$/

/** Where the error summary sends focus for errors that aren't one input. */
const FOCUS: Record<string, string> = {
  'loc-sample': 'loc-url',
  'loc-preview': 'loc-id',
  'loc-geometry': 'loc-id',
  parameters: 'param-id-0',
  'obs-sample': 'obs-location',
  'query-types': 'default-window',
  harness: 'step-heading',
}

function durationOk(text: string): boolean {
  return DURATION_RE.test(text) && !['P', 'PT'].includes(text) && !text.endsWith('T')
}

function stepErrors(state: TsState, step: TsStepId): Record<string, string> {
  const out: Record<string, string> = {}
  switch (step) {
    case 'locations': {
      const http = state.locKind === 'http'
      if (http && !URL_RE.test(state.locHttp.url.trim())) out['loc-url'] = 'Locations URL: enter an http or https address.'
      if (!http) Object.assign(out, connectionErrors(state.locSql, 'loc-'))
      if (!http && !state.locQuery.trim()) out['loc-query'] = 'Write the SELECT query that lists the locations.'
      const sampled = http ? state.locHttpSample?.ok : state.locSqlSample?.ok
      if (!sampled) {
        out['loc-sample'] = 'Read the locations successfully before you continue.'
        return out
      }
      if (!state.locId) out['loc-id'] = 'Location id: choose the field that identifies each location.'
      const g = state.locHttpGeometry
      const none = http ? g.type !== 'point' || !g.x || !g.y : state.locSqlGeometry.encoding === 'none'
      if (none) out['loc-geometry'] = 'Locations need a position: choose the longitude and latitude, or a geometry.'
      if (!state.locPreview?.ok) out['loc-preview'] = 'Preview the locations before you continue.'
      return out
    }
    case 'parameters': {
      const ids = state.parameters.map((p) => p.id)
      if (!ids.length) out.parameters = 'Add at least one parameter.'
      else if (ids.some((id) => !NAME_RE.test(id))) out.parameters = 'Every parameter needs an id: letters, digits and underscores, starting with a letter.'
      else if (new Set(ids).size !== ids.length) out.parameters = 'Parameter ids must be different.'
      else if (state.parameters.some((p) => !p.symbol.trim())) out.parameters = 'Every parameter needs a unit symbol, such as m or Cel.'
      return out
    }
    case 'observations': {
      const http = state.obsKind === 'http'
      if (http && !URL_RE.test(state.obsHttp.url.trim())) out['obs-url'] = 'Observations URL: enter an http or https address.'
      if (!http) Object.assign(out, connectionErrors(state.obsSql, 'obs-'))
      if (!state.obsSample?.ok) out['obs-sample'] = 'Fetch observations for one location before you continue.'
      else {
        if (!state.obsTime) out['obs-time'] = 'Time: choose the field with each observation’s time.'
        if (!state.obsValue) out['obs-value'] = 'Value: choose the field with each observation’s value.'
      }
      return out
    }
    case 'queries':
      if (!durationOk(state.defaultWindow)) out['default-window'] = 'Default window: a duration such as P7D or PT6H.'
      if (!durationOk(state.maxWindow)) out['max-window'] = 'Maximum window: a duration such as P31D.'
      if (!state.queryTypes.length) out['query-types'] = 'Offer at least one EDR query.'
      return out
    case 'check':
      if (!state.harness?.ok || !state.harness.result?.ok) out.harness = 'Every contract check must pass (warnings are fine) before you save.'
      return out
    case 'save':
      return metaErrors(state.meta)
  }
}

export function TimeseriesWizard() {
  const { state, dispatch } = useTs()
  const heading = useRef<HTMLHeadingElement>(null)
  const [showErrors, setShowErrors] = useState(false)
  const index = TS_STEPS.findIndex((s) => s.id === state.step)
  const step = TS_STEPS[index] ?? TS_STEPS[0]
  const errors = useMemo(() => stepErrors(state, state.step), [state])

  useEffect(() => heading.current?.focus(), [state.step])

  const next = () => {
    if (Object.keys(errors).length) {
      setShowErrors(true)
      setTimeout(() => document.getElementById('error-summary')?.focus(), 0)
      return
    }
    const nxt = TS_STEPS[index + 1]
    setShowErrors(false)
    if (nxt) dispatch({ type: 'goto', step: nxt.id })
  }
  const back = () => {
    const prev = TS_STEPS[index - 1]
    setShowErrors(false)
    if (prev) dispatch({ type: 'goto', step: prev.id })
  }
  const props = { errors, showErrors }

  return (
    <div className="wizard">
      <StepList steps={TS_STEPS} index={index} />
      <section className="step" aria-labelledby="step-heading">
        <h2 id="step-heading" ref={heading} tabIndex={-1}>
          <span className="muted">Step {index + 1} of {TS_STEPS.length}:</span> {step.title}
          <span className="muted small"> · Create a time-series provider</span>
        </h2>
        {showErrors && <ErrorSummary errors={Object.entries(errors)} onGo={(key) => document.getElementById(FOCUS[key] ?? key)?.focus()} />}
        {state.step === 'locations' && <TsLocationsStep {...props} />}
        {state.step === 'parameters' && <TsParametersStep {...props} />}
        {state.step === 'observations' && <TsObservationsStep {...props} />}
        {state.step === 'queries' && <TsQueriesStep {...props} />}
        {state.step === 'check' && <TsCheckStep {...props} />}
        {state.step === 'save' && <TsSaveStep {...props} />}
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
