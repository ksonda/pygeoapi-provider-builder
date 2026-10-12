import { stop } from './server'

export default function globalTeardown() {
  if (process.env.E2E_STACK) stop()
}
