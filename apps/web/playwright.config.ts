import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // Every suite signs in as the same seeded account and mutates the same
  // workspace — one file creating an agent while another picks one from the
  // menu produces failures that have nothing to do with the app. Isolation
  // would mean an account per file, and an account needs admin approval; one
  // worker is the cheaper honest answer.
  fullyParallel: false,
  workers: 1,
  // A persona test walks every need it has; one missing affordance must not
  // starve the rest of the list of time.
  timeout: 180_000,
  expect: { timeout: 3_000 },
  reporter: process.env.CI ? 'github' : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:5173',
    // Unset, this is unbounded: a click blocked by a stray modal backdrop waits
    // out the whole test and reports "timed out" instead of naming what
    // intercepted it. Bounded, the same failure names the element in seconds.
    actionTimeout: 15_000,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    locale: 'ko-KR',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'laptop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
    // iPad geometry on Chromium — one engine keeps `npx playwright install
    // chromium` sufficient to run the whole suite.
    {
      name: 'tablet',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 820, height: 1180 },
        isMobile: false,
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
