// J6, end to end (PLAN.md §7.8): a version saved without contract checks is
// checked on its page, then downloaded as a package. Needs the dev stack (the
// mock API); `make e2e` sets E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible } from './helpers'
import { gauges } from './specs'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

test('a saved version is checked, then delivered as a package @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const id = `e2e-deliver-${testInfo.project.name}`
  const saved = await page.request.post('/api/workspace', { headers: { 'X-Provider-Builder': '1' }, data: { spec: gauges(id) } })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  const version = ((await saved.json()) as { version: string }).version

  await page.goto('/#/create/new')
  await page.getByRole('link', { name: `E2E gauges ${id}`, exact: true }).click()
  await expect(page.getByRole('heading', { name: id, level: 1 })).toBeFocused()
  await expect(page.getByRole('cell', { name: 'Not recorded' })).toBeVisible()
  await expectAccessible(page)

  // The gate: run the checks on the saved version; a pass is recorded with it.
  await page.getByRole('button', { name: 'Run the contract checks' }).click()
  await expect(page.getByText(/Every check passed\./)).toBeVisible({ timeout: 180_000 })
  await expect(page.getByRole('cell', { name: 'Passed' })).toBeVisible()

  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: `Download version ${version} (.zip)` }).click()
  expect((await download).suggestedFilename()).toBe(`${id}-spec-${version}.zip`)
  await expectAccessible(page)
})
