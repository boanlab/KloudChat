import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  testMatch: ['calculation-refusal.spec.ts', 'auto-calculation-reason.spec.ts', 'calculator-answer-origin.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5198' },
  webServer: {
    ...base.webServer,
    command: 'npm run dev -- --host 127.0.0.1 --port 5198 --strictPort',
    url: 'http://127.0.0.1:5198',
    reuseExistingServer: false,
  },
  projects: [{ name: 'calculation' }],
})
