// Publishing to a file target, J2 and J3 (PLAN.md §6.4-6.5, Phase 3 exit):
// publish a CSV collection, edit it with a one-line diff and roll back,
// delete it, and see why a second feature provider can't be added. The
// target is the throwaway pygeoapi from global-setup.ts.
import { expect, test, type Page } from '@playwright/test'

import { catalogCard, expectAccessible, next } from './helpers'
import { URL } from './server'

test.skip(!process.env.E2E_STACK, 'needs Docker and the sandbox (make stack-up), E2E_STACK=1')
test.describe.configure({ mode: 'serial' })

const LIVE = { timeout: 150_000 }

async function title(id: string): Promise<string | number> {
  const r = await fetch(`${URL}/collections/${id}?f=json`)
  return r.ok ? ((await r.json()) as { title: string }).title : r.status
}

async function collections(page: Page) {
  await page.goto('/#/configs?target=e2e-file')
  await expect(page.getByRole('heading', { name: 'Collections on a server' })).toBeVisible()
}

test('publish a CSV collection to a file target', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Create a collection' }).click()
  await page.getByRole('combobox', { name: 'Publish to' }).selectOption({ label: 'E2E file target' })
  await expect(page.getByText(/Publishing edits that file in place/)).toBeVisible()
  await next(page)
  const card = catalogCard(page, 'CSV file')
  await card.getByRole('button', { name: 'Use this' }).click()
  await next(page)
  await page.getByRole('textbox', { name: 'CSV file', exact: true }).fill('/data/obs.csv')
  await expect(page.getByText('Found 6 column(s)')).toBeVisible()
  await page.getByRole('combobox', { name: 'X (longitude) column', exact: true }).selectOption('long')
  await page.getByRole('combobox', { name: 'Y (latitude) column', exact: true }).selectOption('lat')
  await page.getByRole('combobox', { name: 'Identifier column', exact: true }).selectOption('id')
  await next(page)
  await expect(page.getByText('pygeoapi loaded the provider')).toBeVisible()
  await next(page)
  await next(page)
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('Weather observations')
  await page.getByRole('textbox', { name: 'Description *', exact: true }).fill('Observations from five weather stations.')
  await next(page)

  // Review: the plan's diff only adds lines.
  await expect(page.getByText('No problems found.')).toBeVisible()
  const diff = page.getByRole('region', { name: /Changes to pygeoapi.config.yml/ })
  await expect(diff.locator('.diff-add', { hasText: 'obs:' })).toHaveCount(1)
  await expect(diff.locator('.diff-del')).toHaveCount(0)
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Publish to E2E file target' }).click()

  await expect(page.getByText('Published. The server serves the change.')).toBeVisible(LIVE)
  await expect(page.getByText(/1 feature\(s\) returned/)).toBeVisible()
  expect(await title('obs')).toBe('Weather observations')
})

test('edit a collection with a one-line diff, then roll back', async ({ page }) => {
  await collections(page)
  const row = page.getByRole('region', { name: 'Collections' }).getByRole('row').filter({ hasText: 'Weather observations' })
  await expect(row.getByText('served')).toBeVisible()
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Edit obs' }).click()

  await expect(page.getByRole('heading', { name: /Step 4 of 6: Describe/ })).toBeVisible()
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('Station observations')
  await next(page)
  const diff = page.getByRole('region', { name: /Changes to pygeoapi.config.yml/ })
  await expect(diff.locator('.diff-del')).toHaveCount(1)
  await expect(diff.locator('.diff-del')).toContainText('title: Weather observations')
  await expect(diff.locator('.diff-add')).toHaveCount(1)
  await expect(diff.locator('.diff-add')).toContainText('title: Station observations')
  await page.getByRole('button', { name: 'Publish to E2E file target' }).click()
  await expect(page.getByText('Published. The server serves the change.')).toBeVisible(LIVE)
  expect(await title('obs')).toBe('Station observations')

  await page.getByRole('button', { name: 'Roll back this publish' }).click()
  await expect(page.getByText('Rolled back: the previous config is back and served.')).toBeVisible(LIVE)
  expect(await title('obs')).toBe('Weather observations')
})

test('J3: a second feature provider is explained, not offered', async ({ page }) => {
  await collections(page)
  await page.getByRole('button', { name: 'Add a provider to obs' }).click()
  await expect(page.getByRole('heading', { name: /Step 1 of 7: Choose a backend/ })).toBeVisible()
  await expect(page.getByText(/pygeoapi uses the first provider of each type/).first()).toBeVisible()
  const card = catalogCard(page, 'GeoJSON file')
  await expect(card.getByText('This collection already has a feature provider')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Use this' })).toBeDisabled()
})

test('delete a collection, then the publish log rolls it back', async ({ page }) => {
  await collections(page)
  await page.getByRole('button', { name: 'Delete obs' }).click()
  const panel = page.getByRole('region', { name: 'Delete obs' })
  const diff = panel.getByRole('region', { name: /Changes to/ })
  await expect(diff.locator('.diff-del', { hasText: 'obs:' })).toHaveCount(1)
  await expect(diff.locator('.diff-add')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Delete obs from E2E file target' }).click()
  await expect(panel.getByText('Published. The server serves the change.')).toBeVisible(LIVE)
  expect(await title('obs')).toBe(404)
  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('button', { name: 'Edit obs' })).toHaveCount(0)

  const log = page.getByRole('region', { name: 'Publish log' })
  const deleted = log.getByRole('row').filter({ hasText: 'delete obs' })
  await deleted.getByRole('button', { name: 'Roll back' }).click()
  await expect(deleted.getByText('Rolled back')).toBeVisible(LIVE)
  expect(await title('obs')).toBe('Weather observations')
})
