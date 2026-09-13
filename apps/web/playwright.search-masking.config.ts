import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: 'search-masking-origin.spec.ts',
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5321', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5321 --strictPort',
    url: 'http://127.0.0.1:5321',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [{ name: 'search-masking' }],
})
