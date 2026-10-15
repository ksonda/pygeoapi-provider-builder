import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

export async function next(page: Page) {
  await page.getByRole('button', { name: /^(Next|Publish)$/ }).click()
}

export async function startWizard(page: Page, backend: RegExp) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Create a collection' }).click()
  await expect(page.getByRole('heading', { name: /Step 1 of 8: Start/ })).toBeFocused()
  await next(page)
  const card = page.getByRole('listitem').filter({ has: page.getByRole('heading', { name: backend }) })
  await card.getByRole('button', { name: 'Use this' }).click()
  await expect(card.getByRole('button', { name: 'Selected' })).toBeVisible()
  await next(page)
  await expect(page.getByRole('heading', { name: /Step 3 of \d: Connect/ })).toBeFocused()
}

/** No serious or critical WCAG 2.1 A/AA violations on the current page. */
export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .exclude('.maplibregl-canvas')
    .analyze()
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
}
