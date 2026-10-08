import { defineConfig, devices } from '@playwright/test'

// E2E runs against the backend serving the built frontend (npm run build),
// with the real Docker sandbox and e2e/settings.yaml (a throwaway workspace).
// Specs that use PostGIS or the mock API need the dev stack (make stack-up);
// `make e2e` starts it and sets E2E_STACK=1.
const PORT = 8766

export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, grep: /@mobile/ },
  ],
  webServer: {
    command: `cd .. && rm -rf .e2e && PROVIDER_BUILDER_SETTINGS=frontend/e2e/settings.yaml .venv/bin/python -m provider_builder --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
