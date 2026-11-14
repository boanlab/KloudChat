import { defineConfig, devices, type PlaywrightTestProject } from '@playwright/test'
import base from './playwright.config'
import shardWeights from './e2e/shard-weights.json' with { type: 'json' }

/**
 * The fixture suite CI runs: every spec here answers the API with `page.route`
 * fixtures (`e2e/*-fixture.ts`, `e2e/shell-mocks.ts`, `e2e/fixtures/`), so it needs no
 * stack and no account. One production build, served once by `vite preview`, for
 * every spec; `API_BASE_URL` points at an unused port so an unmocked call fails fast.
 *
 * Each group below is one surface: its spec files and the viewports it is checked at. A
 * `phone`/`mobile` project runs only tests tagged `@phone`.
 * A project is `<group>/<viewport>`, e.g. `npx playwright test -c playwright.ci.config.ts
 * --project='customize/*'`. CI splits the projects over its e2e jobs with `E2E_SHARD=k/n`
 * (`shardOf` below), by measured time rather than by count.
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
  'super-agent': {
    files: ['super-agent.spec.ts', 'reasoning-chip.spec.ts'],
    projects: { desktop: baseProfile('desktop'), phone: touchPhone },
  },
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
    files: ['composer-plus.spec.ts', 'composer-races.spec.ts', 'share-sign-in.spec.ts', 'file-box.spec.ts', 'home-layout.spec.ts', 'context-gauge.spec.ts'],
    projects: { desktop, phone: touchPhone },
  },
  'i18n-gates': { files: ['i18n-audit-gates.spec.ts'], projects: { default: { ...devices['Desktop Chrome'] } } },
  'report-table': { files: ['report-table-roundtrip.spec.ts', 'report-title-block.spec.ts', 'report-broken-picture.spec.ts', 'report-follows-a-dropped-stream.spec.ts', 'report-edits-in-place.spec.ts'], projects: { default: desktop } },
  // the web report as one Markdown document: one editor, 「Markdown 원문」, sections split back by heading
  'report-markdown': { files: ['report-markdown-editor.spec.ts'], projects: { default: desktop } },
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
  // a strict-local turn's search card: allow, edit, material only, reload, phone; the badge
  'search-consent': { files: ['search-consent.spec.ts'], projects: { desktop, phone: touchPhone } },
  'artifact-source': { files: ['artifact-source-download.spec.ts'], projects: { default: desktop } },
  'audit-log': { files: ['audit-log.spec.ts'], projects: { desktop: desktopChrome, phone: desktopChromePhone } },
  security: { files: ['security-paging.spec.ts'], projects: { desktop: desktopChrome, phone: desktopChromePhone } },
  pipeline: {
    files: ['pipeline-stages.spec.ts', 'user-pipelines.spec.ts'],
    projects: { desktop, phone: pixelNative },
  },
  chain: { files: ['chain-canvas.spec.ts'], projects: { desktop, phone: pixelNative } },
  issues: { files: ['issue-reports.spec.ts'], projects: { desktop } },
  session: { files: ['session-renewal.spec.ts'], projects: { desktop } },
  'serving-guards': { files: ['serving-guards.spec.ts'], projects: { desktop } },
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
    files: [
      'agent-process.spec.ts',
      'save-as-agent.spec.ts',
      'restructure-shots.spec.ts',
      'agent-skill-surface.spec.ts',
      'agent-from-skill.spec.ts',
      'agent-default-model.spec.ts',
    ],
    projects: { desktop },
  },
  results: { files: ['results.spec.ts', 'panel-header.spec.ts'], projects: { desktop, phone: pixel } },
  // the web slides as the PowerPoint editor: header, toolbar, rail, notes, keys, saves, phone
  'deck-editor': { files: ['deck-editor.spec.ts'], projects: { desktop, phone: touchPhone } },
  search: { files: ['search-dialog.spec.ts'], projects: { desktop, phone: pixel } },
  publishing: { files: ['publishing.spec.ts'], projects: { desktop, phone: pixel } },
  ocr: { files: ['ocr.spec.ts'], projects: { desktop, phone: touchPhone } },
  video: { files: ['video-moments.spec.ts'], projects: { desktop, phone: touchPhone } },
  ux: { files: ['ux-keyboard-forms.spec.ts'], projects: { desktop, phone: pixel } },
  'admin-models': { files: ['admin-models.spec.ts'], projects: { desktop, phone: touchPhone } },
  'admin-users': { files: ['admin-users-paging.spec.ts'], projects: { desktop, phone: touchPhone } },
  // sortable tables, CSV exports, strict-local priority, 404, tab titles, expired sign-in
  'ui-polish': { files: ['ui-polish.spec.ts'], projects: { desktop, phone: touchPhone } },
  'share-and-index': {
    files: ['share-one-artifact.spec.ts', 'project-knowledge-reindex.spec.ts'],
    projects: { desktop },
  },
  'failed-saves': { files: ['saves-that-fail.spec.ts'], projects: { desktop } },
  // 지식 서가: sources panel, source viewer, studio, quiz player, notes
  'knowledge-shelf': { files: ['knowledge-shelf.spec.ts'], projects: { desktop, phone: touchPhone } },
  // HWP panel against a stand-in editor; the real studio (built by rhwp/build-studio.sh into
  // dist/rhwp) only where it was built, else that spec skips.
  'hwp-panel': { files: ['hwp-panel.spec.ts'], projects: { desktop, phone: touchPhone } },
  'hwp-studio': { files: ['hwp-studio.spec.ts'], projects: { desktop } },
  // the real Word editor (open-docx-editor), a chunk of the bundle, against a mocked API
  'docx-panel': { files: ['docx-panel.spec.ts'], projects: { desktop, phone: touchPhone } },
  // a report asked for as an HWP or Word file: written by the report screen, opened as the file
  'report-as-office-file': { files: ['report-as-office-file.spec.ts'], projects: { desktop } },
  // the real PowerPoint editor (pptx-react-viewer under our chrome), a chunk of its own
  'pptx-panel': { files: ['pptx-panel.spec.ts', 'pptx-bullets.spec.ts'], projects: { desktop, phone: touchPhone } },
  // HWP, Word and PowerPoint files in the file box: drawn by their editors, 편집 opens a copy
  'file-box-office': { files: ['file-box-office.spec.ts'], projects: { desktop, phone: touchPhone } },
  // unsaved office edits never dropped unasked; phone read only, retries, share, reopen, artifacts page
  'office-safety': { files: ['office-edit-safety.spec.ts'], projects: { desktop, phone: touchPhone } },
  // every toolbar, rail, notes and menu action against the saved file; a manual check
  // (PPTX_ACTIONS=1), as it was when the editor was chosen
  'pptx-actions': { files: ['pptx-actions.spec.ts'], projects: { desktop } },
}

const projects: PlaywrightTestProject[] = Object.entries(groups).flatMap(([group, { files, grep, projects }]) =>
  Object.entries(projects).map(([viewport, use]) => ({
    name: `${group}/${viewport}`,
    testMatch: files,
    // A phone-width project runs only what is about the phone (`@phone`: layout, drawer,
    // touch, widths); everything else is checked once, at desktop width.
    ...(phoneWidths.includes(viewport) ? { grep: /@phone/ } : grep ? { grep } : {}),
    use,
  })),
)

/** Seconds a project took on CI (`.github/ci/e2e-weights.py`); a project not measured yet counts this. */
const UNMEASURED_SEC = 20
const weights: Record<string, number> = shardWeights

/**
 * The projects of shard `k` of `n` (`E2E_SHARD=k/n`): the longest project first, each to the
 * shard with the least time so far, so the shards finish close together (Playwright's own
 * `--shard` splits by test count, and one project here takes 20 times another). The split
 * depends only on the project list and the weights, so every job computes the same one and
 * each project runs in exactly one shard.
 */
function shardOf(all: PlaywrightTestProject[], spec: string | undefined): PlaywrightTestProject[] {
  if (!spec) return all
  const [k, n] = spec.split('/').map(Number)
  if (!Number.isInteger(k) || !Number.isInteger(n) || k < 1 || k > n) {
    throw new Error(`E2E_SHARD must be k/n with 1 <= k <= n, not ${spec}`)
  }
  const cost = (p: PlaywrightTestProject) => weights[p.name!] ?? UNMEASURED_SEC
  const load = new Array<number>(n).fill(0)
  const owner = new Map<string, number>()
  const longestFirst = [...all].sort((a, b) => cost(b) - cost(a) || a.name!.localeCompare(b.name!))
  for (const project of longestFirst) {
    const least = load.indexOf(Math.min(...load))
    owner.set(project.name!, least)
    load[least] += cost(project)
  }
  return all.filter((p) => owner.get(p.name!) === k - 1)
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
  projects: shardOf(projects, process.env.E2E_SHARD),
})
