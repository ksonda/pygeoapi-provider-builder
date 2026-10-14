// Eject, end to end (PLAN.md §7.6): a saved time-series spec becomes a Python
// package in the code workspace, after the one-way warning, and the package
// passes the contract checks in the sandbox. Needs the dev stack (the mock API);
// `make e2e` sets E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible } from './helpers'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

const MOCK = 'http://mockapi:8000'

function gauges(id: string) {
  return {
    spec_version: 1,
    template: 'timeseries-edr',
    id,
    title: `E2E gauges ${id}`,
    locations: {
      from: 'http',
      http: {
        source: { url: `${MOCK}/coops/mdapi/prod/webapi/stations.json`, query: { type: 'waterlevels' } },
        records: '$.stations',
        item_id: { from: 'id' },
        geometry: { type: 'point', x: 'lng', y: 'lat' },
        properties: [{ name: 'name', type: 'string' }],
        title_field: 'name',
      },
    },
    parameters: [{ id: 'water_level', label: 'Water level', unit: { symbol: 'm' } }],
    observations: {
      from: 'http',
      http: {
        url: `${MOCK}/coops/api/prod/datagetter`,
        query: {
          product: '{parameter}',
          station: '{location_id}',
          begin_date: '{start:%Y%m%d %H:%M}',
          end_date: '{end:%Y%m%d %H:%M}',
          datum: 'MLLW',
          units: 'metric',
          time_zone: 'gmt',
          format: 'json',
        },
        records: '$.data',
        error_path: '$.error',
        time: { from: 't', format: '%Y-%m-%d %H:%M' },
        value: { from: 'v' },
      },
      default_window: 'P1D',
      max_window: 'P31D',
    },
    position: { snap_radius_km: 5 },
  }
}

test('a saved spec ejects to a package that passes the contract @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const id = `e2e-gauges-${testInfo.project.name}`
  const saved = await page.request.post('/api/workspace', { headers: { 'X-Provider-Builder': '1' }, data: { spec: gauges(id) } })
  expect(saved.ok(), await saved.text()).toBeTruthy()

  await page.goto('/#/create/new')
  await page.getByRole('link', { name: `Eject E2E gauges ${id} to code` }).click()
  await expect(page.getByRole('heading', { name: `Eject ${id} to code` })).toBeFocused()
  await expect(page.getByRole('note')).toContainText('Eject is one way')
  await expectAccessible(page)

  // It asks for the one-way confirmation first.
  await page.getByRole('button', { name: /^Eject version .* to code$/ }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Confirm that you understand eject is one way.' })).toBeFocused()
  await page.getByRole('checkbox', { name: 'I understand that eject is one way.' }).check()
  await page.getByRole('button', { name: /^Eject version .* to code$/ }).click()

  // The package opens in the code workspace and passes the contract checks.
  await expect(page).toHaveURL(new RegExp(`#/code/${id}-code$`))
  await expect(page.getByText(/^Ejected from the spec/)).toBeVisible()
  await expect(page.getByRole('heading', { name: /src\/.*\/provider\.py/ })).toBeVisible()
  await page.getByRole('button', { name: 'Run the contract checks' }).click()
  await expect(page.getByText(/Every check passed\./)).toBeVisible({ timeout: 180_000 })
  await expectAccessible(page)

  // The spec remembers where it went.
  await page.goto(`/#/eject/${id}`)
  await expect(page.getByRole('link', { name: `${id}-code` })).toBeVisible()
})
