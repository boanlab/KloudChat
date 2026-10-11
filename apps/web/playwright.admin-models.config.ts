import { defineConfig } from '@playwright/test'
import base from './playwright.config'

/** System › 모델 and a new account's monthly credits (`admin-models.spec.ts`). */
export default defineConfig({
  ...base,
  testMatch: ['admin-models.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  workers: 1,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5362', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5362 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5362',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, hasTouch: true } },
  ],
})
