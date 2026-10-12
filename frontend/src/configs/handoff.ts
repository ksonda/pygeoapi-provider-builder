// Hands an imported form to the wizard in memory: it may hold values the
// user hasn't reviewed yet, so it never goes into the URL or storage.
import type { FormState } from '../api/client'

export type HandOff = { form: FormState; intent: 'import' }

let pending: HandOff | null = null

export function handOff(h: HandOff): void {
  pending = h
  window.location.hash = '#/configure/import'
}

export function takeHandOff(): HandOff | null {
  const h = pending
  pending = null
  return h
}
