// The PostGIS path against the dev stack, including a wrong password
// (PLAN.md §12, E2E). Needs the dev stack; `make e2e` sets E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible, next, startWizard } from './helpers'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

test('a wrong password gets a friendly error, then the table publishes', async ({ page }) => {
  await startWizard(page, /PostgreSQL/)
  await page.getByRole('textbox', { name: 'Host', exact: true }).fill('postgis')
  await page.getByRole('textbox', { name: 'Database', exact: true }).fill('test')
  await page.getByRole('textbox', { name: 'User', exact: true }).fill('postgres')
  await page.getByRole('textbox', { name: 'Schemas to search', exact: true }).fill('osm')
  await page.locator('input[type=password]').fill('not-the-password')

  const error = page.getByText('The database rejected the user name or password for user postgres.')
  await expect(error).toBeVisible()
  await expect(page.getByText('not-the-password')).toHaveCount(0)
  await expectAccessible(page)

  await page.locator('input[type=password]').fill('postgres')
  // 2 tables, or 3 once docker/postgis/21-demo-sql.sql has added osm.waterway_classes.
  await expect(page.getByText(/^Found [23] table\(s\)/)).toBeVisible()
  await page.getByRole('combobox', { name: 'Table or view', exact: true }).selectOption('hotosm_bdi_waterways')
  await page.getByRole('combobox', { name: 'Identifier column', exact: true }).selectOption('osm_id')
  await page.getByRole('combobox', { name: 'Geometry column', exact: true }).selectOption('foo_geom')
  await expect(page.getByRole('textbox', { name: 'Written to the config as the environment variable', exact: true })).toHaveValue(
    'PYGEOAPI_HOTOSM_BDI_WATERWAYS_DB_PASSWORD',
  )
  await next(page)

  await expect(page.getByText(/returned 10 item\(s\) of 14776/)).toBeVisible()
  await next(page)
  await next(page)
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('Waterways of Burundi')
  await page.getByRole('textbox', { name: 'Description *', exact: true }).fill('HOT OSM waterways, from pygeoapi test data.')
  await next(page)
  await expect(page.getByText('No problems found.')).toBeVisible()
  await expect(page.getByText('pygeoapi generated its OpenAPI document with this collection.')).toBeVisible()
  await expect(page.getByText('PYGEOAPI_HOTOSM_BDI_WATERWAYS_DB_PASSWORD').first()).toBeVisible()
  await next(page)
  const yaml = page.locator('.cm-content').first()
  await expect(yaml).toContainText('password: ${PYGEOAPI_HOTOSM_BDI_WATERWAYS_DB_PASSWORD}')
  await expect(yaml).not.toContainText('password: postgres')
})
