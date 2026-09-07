import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: 'slides-presentation.spec.ts',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5199' },
  webServer: {
    ...base.webServer,
    command: 'npm run dev -- --host 127.0.0.1 --port 5199 --strictPort',
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'portrait', use: { viewport: { width: 390, height: 844 } } },
    { name: 'landscape', use: { viewport: { width: 844, height: 390 } } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
  ],
})
