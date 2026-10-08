// J4 end to end (PLAN.md §12, E2E): the mock JSON API becomes a provider without
// code, passes the harness, is saved, and is published as a collection through
// J1. Needs the dev stack (the mock API); `make e2e` sets E2E_STACK=1.
import { expect, test } from '@playwright/test'

import { expectAccessible, next } from './helpers'

test.skip(!process.env.E2E_STACK, 'needs the dev stack (make stack-up), E2E_STACK=1')

test('a JSON API becomes a provider, then a collection @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  // Projects share one backend and saved versions never change: one provider per project.
  const id = `e2e-wells-${testInfo.project.name}`
  await page.goto('/')
  await page.getByRole('button', { name: 'Create a provider' }).click()
  await expect(page.getByRole('heading', { name: /Step 1 of 6: Source/ })).toBeFocused()

  // Source: Next is blocked until a sample with records has been fetched.
  await next(page)
  await expect(page.getByRole('alert').filter({ hasText: 'Fix' })).toBeFocused()
  await page.getByRole('textbox', { name: 'API URL' }).fill('http://mockapi:8000/v2/wells')
  for (const [name, value] of [['offset', '0'], ['limit', '25']] as const) {
    await page.getByRole('button', { name: 'Add a query parameter' }).click()
    await expect(page.getByRole('textbox', { name: 'Name' }).last()).toBeFocused()
    await page.keyboard.type(name)
    await page.getByRole('textbox', { name: 'Value' }).last().fill(value)
  }
  await page.getByRole('button', { name: 'Fetch a sample', exact: true }).click()
  await expect(page.getByText(/Found 25 record\(s\) at \$\.data\.items/)).toBeVisible()
  await expect(page.getByText(/through the egress gateway to mockapi:8000/)).toBeVisible()
  await next(page)

  // Sample: the record list is chosen in a keyboard-operable tree.
  const tree = page.getByRole('tree', { name: 'The sampled response' })
  await expect(tree.getByRole('treeitem', { selected: true })).toContainText('items')
  await tree.getByRole('treeitem').first().focus()
  await page.keyboard.press('ArrowDown')
  await expect(tree.getByRole('treeitem', { name: /data/ }).first()).toBeFocused()
  await expectAccessible(page)
  await next(page)

  // Map: suggestions are filled in; the preview runs in the sandbox.
  await expect(page.getByRole('combobox', { name: /Item id/ })).toHaveValue('well_id')
  await next(page)
  await expect(page.getByRole('alert').filter({ hasText: 'Preview the spec in the sandbox before you continue.' })).toBeFocused()
  await page.getByRole('checkbox', { name: 'Publish wkt' }).uncheck()
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByText(/returned 10 item\(s\) of 120/)).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Paging and filters: the suggested offset paging; the API filters by bbox.
  await expect(page.getByRole('combobox', { name: /How the API splits/ })).toHaveValue('offset')
  await page.getByRole('checkbox', { name: 'Bounding box' }).check()
  await next(page)

  // Check: every contract check passes, through to a live server in the sandbox.
  await expect(page.getByText('Every check passed.')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByText(/gunicorn started/)).toBeVisible()
  await expectAccessible(page)
  await next(page)

  // Save, then publish a collection with it.
  await page.getByRole('textbox', { name: /^Title/ }).fill(`E2E wells ${testInfo.project.name}`)
  await expect(page.getByRole('textbox', { name: /^Provider id/ })).toHaveValue(id)
  await expect(page.locator('.cm-content').first()).toContainText('url: http://mockapi:8000/v2/wells')
  await page.getByRole('button', { name: 'Save version 1.0.0' }).click()
  await expect(page.getByText(`Saved ${id} 1.0.0.`)).toBeVisible()
  await page.getByRole('link', { name: /Publish a collection with this provider/ }).click()

  await expect(page.getByRole('heading', { name: /Step 3 of 8: Connect/ })).toBeVisible()
  await expect(page.getByRole('combobox', { name: /Provider version/ })).toHaveValue(`/specs/${id}/1.0.0/spec.yaml`)
  await next(page)
  await expect(page.getByText(/returned 10 item\(s\) of 120/)).toBeVisible()
  await next(page)
  await next(page)
  await expect(page.getByRole('textbox', { name: 'Collection id *', exact: true })).toHaveValue(id)
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('Wells')
  await page.getByRole('textbox', { name: 'Description *', exact: true }).fill('Wells from the mock API.')
  await next(page)
  await expect(page.getByText('No problems found.')).toBeVisible()
  await next(page)
  const yaml = page.locator('.cm-content').first()
  await expect(yaml).toContainText('name: pygeoapi_spec_providers.http.HTTPFeatureProvider')
  await expect(yaml).toContainText(`data: /specs/${id}/1.0.0/spec.yaml`)
})
