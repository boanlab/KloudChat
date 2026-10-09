import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

/** Result cards, the panels' reading header and download, reuse — against a mocked API. */
export default defineConfig({
  ...base,
  testMatch: ['results.spec.ts', 'panel-header.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  retries: 0,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5341', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5341 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5341',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
})
