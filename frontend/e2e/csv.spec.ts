// The CSV happy path, from the catalog to the export (PLAN.md §12, E2E).
import { expect, test } from '@playwright/test'

import { expectAccessible, next, startWizard } from './helpers'

test('publish a CSV file as a collection @mobile', async ({ page }) => {
  await startWizard(page, /CSV file/)

  // Connect: blocked until the required fields are filled.
  await next(page)
  await expect(page.getByRole('alert').filter({ hasText: 'Fix these' })).toBeFocused()

  await page.getByRole('textbox', { name: 'CSV file', exact: true }).fill('/data/obs.csv')
  await expect(page.getByText('Found 6 column(s)')).toBeVisible()
  await page.getByRole('combobox', { name: 'X (longitude) column', exact: true }).selectOption('long')
  await page.getByRole('combobox', { name: 'Y (latitude) column', exact: true }).selectOption('lat')
  await page.getByRole('combobox', { name: 'Identifier column', exact: true }).selectOption('id')
  await expectAccessible(page)
  await next(page)

  // Test and inspect: pygeoapi loads the provider in the sandbox.
  await expect(page.getByText('pygeoapi loaded the provider')).toBeVisible()
  await expect(page.getByText(/returned 5 item\(s\) of 5/)).toBeVisible()
  await next(page)

  // Map fields: pickers come from the probe; title_field offers text fields only.
  const title = page.getByRole('combobox', { name: 'Title field', exact: true })
  await expect(title.locator('option')).toHaveText(['Choose…', 'id', 'datetime'])
  await page.getByRole('combobox', { name: 'Time field', exact: true }).selectOption('datetime')
  await next(page)

  // Describe.
  await expect(page.getByRole('textbox', { name: 'Collection id *', exact: true })).toHaveValue('obs')
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('Weather observations')
  await page.getByRole('textbox', { name: 'Description *', exact: true }).fill('Observations from five weather stations.')
  await page.getByRole('button', { name: /Use the data extent/ }).click()
  await expect(page.getByRole('spinbutton', { name: 'West', exact: true })).toHaveValue('-122')
  await next(page)

  // Review: every layer passes, including OpenAPI generation in the sandbox.
  await expect(page.getByText('No problems found.')).toBeVisible()
  await expect(page.getByText('pygeoapi generated its OpenAPI document with this collection.')).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Publish (export target).
  await expect(page.getByText('Your collection is ready.')).toBeVisible()
  const yaml = page.locator('.cm-content').first()
  await expect(yaml).toContainText('name: CSV')
  await expect(yaml).toContainText('time_field: datetime')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download pygeoapi.config.yml' }).click()
  expect((await download).suggestedFilename()).toBe('pygeoapi.config.yml')
})

test('the home page is accessible', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Configure' })).toBeVisible()
  await expectAccessible(page)
})
