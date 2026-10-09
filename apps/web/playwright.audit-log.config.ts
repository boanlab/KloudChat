import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

/** The audit log screen against a mocked API. */
export default defineConfig({
  ...base,
  testMatch: ['audit-log.spec.ts'],
  reporter: 'list',
  retries: 0,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5247', trace: 'off' },
  webServer: {
    ...base.webServer,
    command: 'npm run dev -- --host 127.0.0.1 --port 5247 --strictPort',
    url: 'http://127.0.0.1:5247',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, isMobile: false, hasTouch: true } },
  ],
})
