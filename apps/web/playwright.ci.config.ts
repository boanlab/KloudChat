import { defineConfig, devices, type PlaywrightTestProject } from '@playwright/test'
import base from './playwright.config'

/**
 * The fixture suite CI runs: every spec here answers the API with `page.route`
 * fixtures (`e2e/*-fixture.ts`, `e2e/shell-mocks.ts`, `e2e/fixtures/`), so it needs no
 * stack and no account. One production build, served once by `vite preview`, for
 * every spec; `API_BASE_URL` points at an unused port so an unmocked call fails fast.
 *
 * Each group below is one surface: its spec files and the viewports it is checked at. A
 * `phone`/`mobile` project runs only tests tagged `@phone`.
 * A project is `<group>/<viewport>`, e.g. `npx playwright test -c playwright.ci.config.ts
 * --project='customize/*'`. CI splits the projects over four jobs with `--shard`.
 *
 * The live-stack suite (a running app at 5173 and a seeded account) stays in
 * `playwright.config.ts`.
 */

const port = Number(process.env.E2E_PORT ?? 5390)
const baseURL = `http://127.0.0.1:${port}`

const desktop = { browserName: 'chromium' as const, viewport: { width: 1440, height: 900 } }
// A phone viewport on desktop Chromium with touch: media queries and touch, desktop user agent.
const touchPhone = { browserName: 'chromium' as const, viewport: { width: 390, height: 844 }, hasTouch: true }
// A phone viewport with no touch.
const narrow = { viewport: { width: 390, height: 844 } }
// A real phone profile (mobile user agent, device pixel ratio, `isMobile`).
const pixel = { ...devices['Pixel 7'], browserName: 'chromium' as const, viewport: { width: 390, height: 844 } }
const pixelNative = { ...devices['Pixel 7'], browserName: 'chromium' as const }
const desktopChrome = { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }
const desktopChromePhone = {
  ...devices['Desktop Chrome'],
  viewport: { width: 390, height: 844 },
  isMobile: false,
  hasTouch: true,
}
// The live suite's desktop and laptop profiles.
const baseProfile = (name: string) => base.projects!.find((p) => p.name === name)!.use

/** Projects at phone width; they run only `@phone` tests. */
const phoneWidths = ['phone', 'mobile']

type Group = {
  files: string[]
  grep?: RegExp
  projects: Record<string, PlaywrightTestProject['use']>
}

const groups: Record<string, Group> = {
  freshness: { files: ['freshness-abstention.spec.ts', 'grounded-answer.spec.ts'], projects: { default: {} } },
  'edit-resend': { files: ['edit-resend-message.spec.ts'], projects: { desktop: { viewport: desktop.viewport }, mobile: narrow } },
  'auto-badge': {
    files: ['auto-routing.spec.ts'],
    grep: /라우팅 기준에 따라|품질 라우팅 결과는 영어|품질 상향 응답은|처리 내역의 전체 모델 정보/,
    projects: { desktop: baseProfile('desktop'), laptop: baseProfile('laptop') },
  },
  presentation: {
    files: ['slides-presentation.spec.ts'],
    projects: {
      portrait: { viewport: { width: 390, height: 844 } },
      landscape: { viewport: { width: 844, height: 390 } },
      desktop: { viewport: { width: 1440, height: 900 } },
    },
  },
  calculation: {
    files: ['calculation-refusal.spec.ts', 'auto-calculation-reason.spec.ts', 'calculator-answer-origin.spec.ts'],
    projects: { default: {} },
  },
  phone: { files: ['phone-variant.spec.ts'], projects: { default: { browserName: 'chromium' } } },
  composer: {
    files: ['composer-plus.spec.ts', 'composer-races.spec.ts', 'share-sign-in.spec.ts'],
    projects: { desktop, phone: touchPhone },
  },
  'i18n-gates': { files: ['i18n-audit-gates.spec.ts'], projects: { default: { ...devices['Desktop Chrome'] } } },
  'report-table': { files: ['report-table-roundtrip.spec.ts', 'report-title-block.spec.ts'], projects: { default: desktop } },
  'session-hydration': {
    files: [
      'session-hydration.spec.ts',
      'composer-paste.spec.ts',
      'media-templates-retired.spec.ts',
      'answer-outcomes.spec.ts',
    ],
    projects: { desktop: { viewport: desktop.viewport } },
  },
  'report-audit': { files: ['report-panel-audit.spec.ts', 'sign-in-landing.spec.ts'], projects: { default: desktop } },
  'search-masking': { files: ['search-masking-origin.spec.ts'], projects: { default: {} } },
  'artifact-source': { files: ['artifact-source-download.spec.ts'], projects: { default: desktop } },
  'audit-log': { files: ['audit-log.spec.ts'], projects: { desktop: desktopChrome, phone: desktopChromePhone } },
  security: { files: ['security-paging.spec.ts'], projects: { desktop: desktopChrome, phone: desktopChromePhone } },
  pipeline: {
    files: ['pipeline-stages.spec.ts', 'user-pipelines.spec.ts'],
    projects: { desktop, phone: pixelNative },
  },
  issues: { files: ['issue-reports.spec.ts'], projects: { desktop } },
  customize: {
    files: [
      'customize-hub.spec.ts',
      'customize-forms.spec.ts',
      'customize-try.spec.ts',
      'customize-shots.spec.ts',
      'customize-templates.spec.ts',
      'skill-upload.spec.ts',
      'skill-scripts-and-gaps.spec.ts',
      'templates-settings.spec.ts',
    ],
    projects: { desktop, phone: pixel },
  },
  agents: {
    files: ['agent-process.spec.ts', 'save-as-agent.spec.ts', 'restructure-shots.spec.ts'],
    projects: { desktop },
  },
  results: { files: ['results.spec.ts', 'panel-header.spec.ts'], projects: { desktop, phone: pixel } },
  search: { files: ['search-dialog.spec.ts'], projects: { desktop, phone: pixel } },
  publishing: { files: ['publishing.spec.ts'], projects: { desktop, phone: pixel } },
  ocr: { files: ['ocr.spec.ts'], projects: { desktop, phone: touchPhone } },
  ux: { files: ['ux-keyboard-forms.spec.ts'], projects: { desktop, phone: pixel } },
  'admin-models': { files: ['admin-models.spec.ts'], projects: { desktop, phone: touchPhone } },
  'admin-users': { files: ['admin-users-paging.spec.ts'], projects: { desktop, phone: touchPhone } },
}

export default defineConfig({
  ...base,
  // Specs within a file share fixtures and run in order; files run one at a time per job.
  fullyParallel: false,
  workers: 1,
  // One retry on CI, for an environment hiccup only: a test that passes on retry is
  // reported as flaky (a warning annotation and the HTML report), never silently green.
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['github'], ['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: { ...base.use, baseURL, trace: 'on-first-retry' },
  webServer: {
    // CI builds once before the tests; locally the run builds what it serves.
    command: `${process.env.E2E_PREBUILT ? '' : 'npx vite build && '}npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
    env: { API_BASE_URL: 'http://127.0.0.1:59999' },
    url: baseURL,
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: Object.entries(groups).flatMap(([group, { files, grep, projects }]) =>
    Object.entries(projects).map(([viewport, use]) => ({
      name: `${group}/${viewport}`,
      testMatch: files,
      // A phone-width project runs only what is about the phone (`@phone`: layout, drawer,
      // touch, widths); everything else is checked once, at desktop width.
      ...(phoneWidths.includes(viewport) ? { grep: /@phone/ } : grep ? { grep } : {}),
      use,
    })),
  ),
})
