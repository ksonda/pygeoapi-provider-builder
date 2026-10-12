import { start } from './server'

export default async function globalSetup() {
  if (process.env.E2E_STACK) await start()
}
