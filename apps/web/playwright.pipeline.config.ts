import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// Pipeline stage screens against fixture data only; nothing reaches a server.
export default defineConfig({
  ...base,
  testMatch: ['pipeline-stages.spec.ts', 'user-pipelines.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5204', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5204 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5204',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'dark', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 }, colorScheme: 'dark' } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
  ],
})
