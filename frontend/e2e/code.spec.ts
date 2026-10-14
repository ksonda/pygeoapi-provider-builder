// J5, the code workspace, end to end (PLAN.md §7.5, §12): scaffold a feature
// provider, write it in the browser editor, pass the contract checks in the
// sandbox and save a version. Uses no external services.
import { expect, test } from '@playwright/test'

import { expectAccessible } from './helpers'

const PROVIDER = `"""Five items in memory: the scaffold, implemented in the browser."""

from __future__ import annotations

from typing import Any

from pygeoapi.provider.base import BaseProvider, ProviderItemNotFoundError

ITEMS = [
    {
        'type': 'Feature',
        'id': f'item-{n}',
        'geometry': {'type': 'Point', 'coordinates': [float(n), float(n)]},
        'properties': {'name': f'Item {n}'},
    }
    for n in range(1, 6)
]


class E2eItemsProvider(BaseProvider):
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {'name': {'type': 'string'}}

    def get_fields(self) -> dict[str, dict[str, Any]]:
        return self._fields

    def query(self, offset=0, limit=10, resulttype='results', skip_geometry=False, select_properties=None, **kwargs) -> dict[str, Any]:
        page = [] if resulttype == 'hits' else [dict(f) for f in ITEMS[offset : offset + limit]]
        for f in page:
            if skip_geometry:
                f['geometry'] = None
        return {'type': 'FeatureCollection', 'features': page, 'numberMatched': len(ITEMS), 'numberReturned': len(page)}

    def get(self, identifier, **kwargs) -> dict[str, Any]:
        for f in ITEMS:
            if f['id'] == str(identifier):
                return f
        raise ProviderItemNotFoundError(user_msg='item not found')
`

test('a provider written in the code workspace passes the contract @mobile', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const id = `e2e-items-${testInfo.project.name}`
  await page.goto('/#/create/new')
  await page.getByRole('link', { name: 'Start a provider in Python' }).click()
  await page.getByRole('radio', { name: 'Files' }).check()
  await page.getByRole('textbox', { name: 'Title' }).fill(`E2E items ${testInfo.project.name}`)
  await page.getByRole('textbox', { name: 'Provider id' }).fill(id)
  await expectAccessible(page)
  await page.getByRole('button', { name: 'Create the package' }).click()
  await expect(page).toHaveURL(new RegExp(`#/code/${id}$`))
  await expect(page.getByRole('heading', { name: /src\/.*\/provider\.py/ })).toBeVisible()

  // The stubs fail the contract with NotImplementedError.
  await page.getByRole('button', { name: 'Run the contract checks' }).click()
  await expect(page.getByText(/Some checks failed\./)).toBeVisible({ timeout: 120_000 })
  await expect(page.getByText(/NotImplementedError/).first()).toBeVisible()

  // Write the provider in the editor, check it with ruff and save it.
  const editor = page.getByRole('textbox', { name: /Contents of src\/.*\/provider\.py/ })
  await editor.fill(PROVIDER.replace('E2eItemsProvider', id.split('-').map((p) => p[0]?.toUpperCase() + p.slice(1)).join('') + 'Provider'))
  await page.getByRole('button', { name: 'Format with ruff' }).click()
  await expect(page.getByText('Formatted with ruff. Save the file to keep it.')).toBeVisible()
  await page.getByRole('button', { name: 'Check with ruff' }).click()
  await expect(page.getByText('ruff found nothing to fix.')).toBeVisible()
  await page.getByRole('button', { name: 'Save file' }).click()
  await expect(page.getByText(/^Saved src\/.*\/provider\.py\.$/)).toBeVisible()

  // Now the contract passes, and a version can be saved.
  await page.getByRole('button', { name: 'Run the contract checks' }).click()
  await expect(page.getByText(/Every check passed\./)).toBeVisible({ timeout: 120_000 })
  await expectAccessible(page)
  await page.getByRole('textbox', { name: 'What changed' }).fill('Five items in memory.')
  await page.getByRole('button', { name: 'Save version 1.0.0' }).click()
  await expect(page.getByText('Saved version 1.0.0 (Git tag v1.0.0).')).toBeVisible()
  await expect(page.getByText(/versions 1\.0\.0/)).toBeVisible()
  await expect(page.getByRole('link', { name: /Publish a collection with this provider/ })).toBeVisible()
})
