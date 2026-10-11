import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

/** Keyboard, focus and announcement behaviour of forms, dialogs and menus, against fixture data. */
export default defineConfig({
  ...base,
  testMatch: ['ux-keyboard-forms.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  workers: 1,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5361', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5361 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5361',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
})
