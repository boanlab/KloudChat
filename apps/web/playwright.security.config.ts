import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

/** Settings › 보안: the sign-in list and the access log page against a mocked API. */
export default defineConfig({
  ...base,
  testMatch: ['security-paging.spec.ts'],
  reporter: 'list',
  retries: 0,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5271', trace: 'off' },
  webServer: {
    ...base.webServer,
    command: 'npm run dev -- --host 127.0.0.1 --port 5271 --strictPort',
    url: 'http://127.0.0.1:5271',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, isMobile: false, hasTouch: true } },
  ],
})
