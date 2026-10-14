// J4 with the time-series template, end to end (PLAN.md §12, E2E): the mock API's
// CO-OPS look-alike becomes an EDR provider without code, passes the harness and
// is saved. Needs the dev stack (the mock API); `make e2e` sets E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible, next } from './helpers'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

const PARAMS: [string, string][] = [
  ['product', '{parameter}'],
  ['station', '{location_id}'],
  ['begin_date', '{start:%Y%m%d %H:%M}'],
  ['end_date', '{end:%Y%m%d %H:%M}'],
  ['time_zone', 'gmt'],
  ['format', 'json'],
]

test('a time-series API becomes an EDR provider @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  await page.goto('/#/create/new')
  await page.getByRole('link', { name: 'Start with time series at monitoring locations' }).click()
  await expect(page.getByRole('heading', { name: /Step 1 of 6: Locations/ })).toBeFocused()

  // Locations: the station list, its suggested mapping, then a preview.
  await page.getByRole('textbox', { name: 'Locations URL' }).fill('http://mockapi:8000/coops/mdapi/prod/webapi/stations.json')
  await page.getByRole('button', { name: 'Fetch the locations' }).click()
  await expect(page.getByRole('combobox', { name: /Location id/ })).toHaveValue('id')
  await expect(page.getByRole('combobox', { name: /Longitude/ })).toHaveValue('lng')
  await page.getByRole('button', { name: 'Preview the locations' }).click()
  await expect(page.getByText('6 location(s).')).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Parameters: one, with its unit.
  await next(page)
  await expect(page.getByRole('alert').filter({ hasText: 'Every parameter needs an id' })).toBeFocused()
  await page.getByRole('textbox', { name: /^Id/ }).fill('water_level')
  await page.getByRole('textbox', { name: 'Label' }).fill('Water level')
  await page.getByRole('textbox', { name: /Unit symbol/ }).fill('m')
  await expectAccessible(page)
  await next(page)

  // Observations: the URL template, then one station's series to map time and value.
  await page.getByRole('textbox', { name: 'Observations URL' }).fill('http://mockapi:8000/coops/api/prod/datagetter')
  for (const [name, value] of PARAMS) {
    await page.getByRole('button', { name: 'Add a request parameter' }).click()
    await page.getByRole('textbox', { name: 'Name' }).last().fill(name)
    await page.getByRole('textbox', { name: 'Value' }).last().fill(value)
  }
  await page.getByRole('textbox', { name: /Error message path/ }).fill('$.error')
  await page.getByRole('button', { name: 'Fetch the observations' }).click()
  await expect(page.getByText(/observation\(s\) at \$\.data/)).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Time', exact: true })).toHaveValue('t')
  await expect(page.getByRole('combobox', { name: 'Value', exact: true })).toHaveValue('v')
  await expectAccessible(page)
  await next(page)

  // Time and queries: the defaults; then every contract check passes.
  await next(page)
  await expect(page.getByText(/Every check passed\./)).toBeVisible({ timeout: 120_000 })
  await expect(page.getByText(/Valid CoverageJSON/).first()).toBeVisible()
  await expectAccessible(page)
  await next(page)

  await page.getByRole('textbox', { name: 'Title' }).fill(`E2E water levels (${testInfo.project.name})`)
  await page.getByRole('button', { name: 'Save version 1.0.0' }).click()
  await expect(page.getByRole('link', { name: /Publish a collection with this provider/ })).toBeVisible()
})
