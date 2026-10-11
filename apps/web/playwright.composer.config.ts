import { defineConfig } from '@playwright/test'
import base from './playwright.config'

/** The composer's 「+」 menu and phone layout against fixture data (`composer-fixture.ts`). */
export default defineConfig({
  ...base,
  testMatch: ['composer-plus.spec.ts', 'composer-races.spec.ts', 'share-sign-in.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  workers: 1,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5352', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5352 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5352',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, hasTouch: true } },
  ],
})
