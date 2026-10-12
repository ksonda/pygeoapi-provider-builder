// The throwaway pygeoapi that publish.spec.ts publishes to (settings.yaml,
// target e2e-file). Its config folder is .e2e/config, a fresh copy of the
// tests' pristine config; restarting the container regenerates OpenAPI.
import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

export const NAME = 'pb-e2e-pygeoapi'
export const URL = 'http://127.0.0.1:5081'
const REPO = resolve(import.meta.dirname, '../..')

export function stop() {
  try {
    execFileSync('docker', ['rm', '-f', NAME], { stdio: 'ignore' })
  } catch {
    // not running
  }
}

export async function start() {
  stop()
  const config = resolve(REPO, '.e2e/config')
  rmSync(config, { recursive: true, force: true })
  mkdirSync(config, { recursive: true })
  copyFileSync(resolve(REPO, 'backend/tests/fixtures/pygeoapi.config.yml'), resolve(config, 'pygeoapi.config.yml'))
  execFileSync('docker', [
    'run', '-d', '--name', NAME,
    '-p', '127.0.0.1:5081:80',
    '-v', `${config}:/config`,
    '-v', `${resolve(REPO, 'docker/data')}:/data`,
    '-e', 'PYGEOAPI_CONFIG=/config/pygeoapi.config.yml',
    'geopython/pygeoapi:0.24.0',
  ])
  for (let i = 0; i < 90; i++) {
    try {
      if ((await fetch(`${URL}/collections`)).ok) return
    } catch {
      // starting
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new Error(`${NAME} did not start`)
}
