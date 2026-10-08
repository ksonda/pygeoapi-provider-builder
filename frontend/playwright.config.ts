import { defineConfig, devices } from '@playwright/test'

// E2E runs against the backend serving the built frontend (npm run build),
// with the real Docker sandbox. PostGIS specs also need the dev stack
// (make stack-up); `make e2e` sets E2E_POSTGIS=1 when it is running.
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
    command: `cd .. && .venv/bin/python -m provider_builder --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
