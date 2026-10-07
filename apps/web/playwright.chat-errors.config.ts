import { defineConfig } from '@playwright/test'
import base from './playwright.config'

/** Against the running app (its login); only the turn's stream is faked. */
export default defineConfig({
  ...base,
  testMatch: ['chat-error-audit.spec.ts'],
  reporter: 'line',
  workers: 1,
  projects: (base.projects ?? []).filter((p) => p.name === 'desktop'),
})
