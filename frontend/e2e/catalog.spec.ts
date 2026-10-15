// Catalog breadth (Phase 5): J1 for provider types beyond features, the OGR
// lookup, and the JSON-LD preview (PLAN.md §12, E2E). The data is pygeoapi's
// own test data, which `make fixtures` copies to docker/data.
import { expect, test } from '@playwright/test'

import { expectAccessible, next, startWizard } from './helpers'

test('a raster as a coverage, previewed from its CoverageJSON', async ({ page }) => {
  await startWizard(page, 'Raster file (rasterio/GDAL)')
  await page.getByRole('textbox', { name: 'Raster file', exact: true }).fill('/data/CMC_glb_TMP_TGL_2_latlon.15x.15_2020081000_P000.grib2')
  await page.getByRole('textbox', { name: 'Native format', exact: true }).fill('GRIB')
  await page.getByRole('textbox', { name: 'Native media type', exact: true }).fill('application/x-grib2')
  await next(page)

  // Test and inspect: a window of the first band, drawn with a legend; no Map fields step.
  await expect(page.getByRole('heading', { name: /Step 4 of 7: Test and inspect/ })).toBeFocused()
  await expect(page.getByText(/returned a 201×201 window of TMP as CoverageJSON/)).toBeVisible()
  await expect(page.getByRole('img', { name: /TMP over the window queried/ })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Grid' })).toContainText('2400 × 1201')
  await expectAccessible(page)
  await next(page)

  await expect(page.getByRole('heading', { name: /Step 5 of 7: Describe/ })).toBeFocused()
  await page.getByRole('textbox', { name: 'Collection id *', exact: true }).fill('cmc-temperature')
  await page.getByRole('textbox', { name: 'Title *', exact: true }).fill('CMC 2 m air temperature')
  await page.getByRole('textbox', { name: 'Description *', exact: true }).fill('A global analysis.')
  await page.getByRole('button', { name: /Use the data extent/ }).click()
  await expect(page.getByRole('spinbutton', { name: 'West', exact: true })).toHaveValue('-180')
  await next(page)
  await expect(page.getByText('No problems found.')).toBeVisible()
  await next(page)
  await expect(page.locator('.cm-content').first()).toContainText('name: rasterio')
})

test('pre-rendered vector tiles from a folder, on a map of the probe’s tiles', async ({ page }) => {
  await startWizard(page, 'Pre-rendered vector tiles (tippecanoe)')
  await page.getByRole('button', { name: 'Browse…' }).click()
  const browser = page.getByRole('dialog', { name: 'Choose a folder' })
  await browser.getByRole('button', { name: '📁 tiles/' }).click()
  await browser.getByRole('button', { name: 'Use the folder ne_110m_lakes' }).click()
  await expect(page.getByRole('textbox', { name: 'Tiles folder', exact: true })).toHaveValue('/data/tiles/ne_110m_lakes')
  await page.getByRole('spinbutton', { name: 'Lowest zoom level', exact: true }).fill('0')
  await page.getByRole('spinbutton', { name: 'Highest zoom level', exact: true }).fill('5')
  await next(page)

  await expect(page.getByText(/and served \d+ tile\(s\)/)).toBeVisible()
  await expect(page.getByRole('group', { name: 'Map of the tiles the probe fetched' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Tile layers' })).toContainText('ne_110m_lakes')
  await expectAccessible(page)
})

test('an OGR GeoPackage: the lookup fills the layer and the identifier', async ({ page }) => {
  await startWizard(page, 'GDAL/OGR vector source')
  await page.getByRole('combobox', { name: 'File format', exact: true }).selectOption('GPKG')
  await page.getByRole('textbox', { name: 'File', exact: true }).fill('/data/dutch_addresses_4326.gpkg')
  await expect(page.getByText('Found 1 layer(s)')).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Layer', exact: true })).toHaveValue('OGRGeoJSON')
  await expect(page.getByRole('combobox', { name: 'Identifier field', exact: true })).toHaveValue('fid')
  await next(page)
  await expect(page.getByText(/returned 10 item\(s\)/)).toBeVisible()
})

test('a Geoconnex JSON-LD template, previewed as RDF', async ({ page }) => {
  await startWizard(page, 'CSV file')
  await page.getByRole('textbox', { name: 'CSV file', exact: true }).fill('/data/obs.csv')
  await expect(page.getByText('Found 6 column(s)')).toBeVisible()
  await page.getByRole('combobox', { name: 'X (longitude) column', exact: true }).selectOption('long')
  await page.getByRole('combobox', { name: 'Y (latitude) column', exact: true }).selectOption('lat')
  await page.getByRole('combobox', { name: 'Identifier column', exact: true }).selectOption('id')
  await next(page)
  await expect(page.getByText('pygeoapi loaded the provider')).toBeVisible()
  await next(page)
  await next(page)

  // Describe: choose the template, fill its slots, then preview.
  await page.getByRole('combobox', { name: 'JSON-LD for each item', exact: true }).selectOption({ label: 'Geoconnex hydrologic location' })
  await page.getByRole('combobox', { name: 'Name', exact: true }).selectOption('stn_id')
  await page.getByRole('textbox', { name: 'Within', exact: true }).fill('https://geoconnex.us/ref/states/08')
  await page.getByRole('button', { name: 'Preview an item as RDF' }).click()
  await expect(page.getByText(/gives \d+ triple\(s\)/)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Triples' })).toContainText('https://schema.org/geoWithin')
  await expectAccessible(page)
})
