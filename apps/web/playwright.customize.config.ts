import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// 「사용자 지정」 hub, its forms and 체험 against fixture data only; nothing reaches a server.
export default defineConfig({
  ...base,
  testMatch: ['customize-hub.spec.ts', 'customize-forms.spec.ts', 'customize-try.spec.ts', 'customize-shots.spec.ts', 'customize-templates.spec.ts', 'skill-upload.spec.ts', 'skill-scripts-and-gaps.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5262', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5262 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5262',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
})
