// J4 with the SQL template, end to end (PLAN.md §12, E2E): a join of two PostGIS
// tables becomes a provider without code, passes the harness and is saved. Needs
// the dev stack (PostGIS with docker/postgis/21-demo-sql.sql); `make e2e` sets
// E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible, next } from './helpers'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

const JOIN = `SELECT w.osm_id, w.name, w.foo_geom, w.waterway, c.label AS waterway_label, c.reviewed_at
FROM osm.hotosm_bdi_waterways AS w JOIN osm.waterway_classes AS c ON c.waterway = w.waterway`

test('a SQL join becomes a provider @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  await page.goto('/#/create/new')
  await page.getByRole('link', { name: 'Start with a sql query' }).click()
  await expect(page.getByRole('heading', { name: /Step 1 of 4: Database and query/ })).toBeFocused()

  // Database and query: required fields first, then the guard rejects a write.
  await next(page)
  await expect(page.getByRole('alert').filter({ hasText: 'Fix' })).toBeFocused()
  await page.getByRole('textbox', { name: 'Host' }).fill('postgis')
  await page.getByRole('textbox', { name: 'Database' }).fill('test')
  await page.getByRole('textbox', { name: 'User' }).fill('pb_reader')
  await page.getByRole('textbox', { name: 'Environment variable for the password' }).fill('PB_READER_PASSWORD')
  await page.getByLabel('Password to test with').fill('reader-password') // docker/postgis/21-demo-sql.sql
  // fill(), not typing: Enter would accept the editor's autocompletion rather than add a line.
  const editor = page.getByRole('textbox', { name: 'SQL query' })
  await editor.fill('DELETE FROM osm.waterway_classes')
  await page.getByRole('button', { name: 'Run the query', exact: true }).click()
  await expect(page.getByText('Only a SELECT query is allowed, not DELETE.')).toBeVisible()
  await editor.fill(JOIN)
  await page.getByRole('button', { name: 'Run the query', exact: true }).click()
  await expect(page.getByText(/50 row\(s\)/)).toBeVisible()
  await expect(page.getByText(/through the egress gateway to postgis:5432/)).toBeVisible()
  await expect(page.getByRole('cell', { name: /^MultiLineString, \d+ points$/ }).first()).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Map fields: the suggestions, then a preview in the sandbox.
  await expect(page.getByRole('combobox', { name: /Item id/ })).toHaveValue('osm_id')
  await expect(page.getByRole('combobox', { name: /Where each item's location comes from/ })).toHaveValue('native')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText(/returned 10 item\(s\) of 14768/)).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Check: every contract check passes, through to a live server in the sandbox.
  await expect(page.getByText(/Every check passed\./)).toBeVisible({ timeout: 120_000 })
  await expectAccessible(page)
  await next(page)

  // Save: one provider per project, since saved versions never change.
  await page.getByRole('textbox', { name: 'Title' }).fill(`E2E waterways (${testInfo.project.name})`)
  await expect(page.getByText('Hosts it may reach: postgis:5432 · environment variables: PB_READER_PASSWORD')).toBeVisible()
  await page.getByRole('button', { name: 'Save version 1.0.0' }).click()
  await expect(page.getByRole('link', { name: /Publish a collection with this provider/ })).toBeVisible()
  await expectAccessible(page)
})
