import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// 「문의·신고」 screens against fixture data only; nothing reaches a server.
export default defineConfig({
  ...base,
  testMatch: ['issue-reports.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5211', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5211 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5211',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'dark', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 }, colorScheme: 'dark' } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
  ],
})
