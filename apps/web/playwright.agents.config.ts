import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// An agent's 진행 과정 and 묶음, 「에이전트로 저장」 and 설정 › 메모리 against fixture data only;
// nothing reaches a server.
export default defineConfig({
  ...base,
  testMatch: ['agent-process.spec.ts', 'save-as-agent.spec.ts', 'restructure-shots.spec.ts'],
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...base.use, baseURL: 'http://127.0.0.1:5287', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5287 --strictPort',
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: 'http://127.0.0.1:5287',
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'], browserName: 'chromium', viewport: { width: 390, height: 844 } } },
  ],
})
