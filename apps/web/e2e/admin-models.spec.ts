import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { at } from './composer-fixture'
import { shellRead } from './shell-mocks'

/**
 * System › 모델 against fixture data: the administrator decides which model does which job —
 * each screen's default and the side work that reads conversations (self-hosted models only) —
 * and whether models think. System › 회원 가입 holds a new account's monthly credits.
 */

const admin = {
  id: 'fixture-admin', email: 'admin@example.test', name: '관리자', role: 'admin', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

const local27 = { id: 'local/qwen3.8-27b', label: 'Qwen · 27B', strictLocal: false }
const flash = { id: 'local/qwen3.8-flash', label: 'Qwen · Flash', strictLocal: false }
const external = { id: 'openai/gpt-x', label: 'OpenAI · GPT', strictLocal: false }

function roles(extra: Record<string, unknown>) {
  return {
    chat: 'local/qwen3.8-27b', report: '', slides: 'local/qwen3.8-27b', image: '', audio: '', video: '',
    title: 'local/qwen3.8-flash', code: 'local/qwen3.8-27b', codeFallback: 'local/qwen3.8-flash',
    selfHostedThinking: false, externalThinking: false, defaultMonthlyCredits: 500000,
    choices: {
      chat: [local27, flash, external], report: [local27, flash, external], slides: [local27, flash, external],
      image: [{ id: 'google/img', label: 'Google · Image', strictLocal: false }], audio: [], video: [],
      helper: [local27, flash],
    },
    ...extra,
  }
}

function settings(modelRoles: Record<string, unknown>) {
  return {
    litellm: { baseUrl: 'http://litellm', baseUrlSource: 'environment', masterKeySet: true, masterKeyPreview: '…abcd',
      masterKeySource: 'environment' },
    smtp: { host: '', port: '', security: 'starttls', username: '', from: '', appBaseUrl: '', passwordSet: false,
      passwordPreview: '', hostSource: 'environment', passwordResetEnabled: false },
    status: 'ok', brand: { name: 'KloudChat', logo: '' }, contact: { email: 'admin@example.test', source: 'admin' },
    signup: { mode: 'approval', modeSource: 'database', domains: [], verifyEmail: false, verificationActive: false },
    enabledKinds: ['report', 'slides'],
    tools: { backendBaseUrl: 'http://gateway:8080', features: [] },
    ocr: { enabled: true, mode: 'both', visionModel: '', maxPages: 300, modeInForce: 'both',
      visionModelInForce: '', engineConfigured: true, visionChoices: [] },
    modelRoles: roles(modelRoles),
    credits: { perUsd: 100000, budgetHeadroom: 0.2 },
    unpricedModels: [],
  }
}

async function adminFixture(page: Page, info: TestInfo) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const puts: Record<string, string>[] = []
  let current = settings({})
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user: admin })
    if (method === 'GET' && path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false })
    if (method === 'GET' && path === '/admin/settings') return json(current)
    if (method === 'PUT' && path === '/admin/settings') {
      const body = request.postDataJSON() as Record<string, string>
      puts.push(body)
      if (body.modelTitle === 'openai/gpt-x') return json({ detail: 'model_title_invalid' }, 422)
      const next = roles({})
      current = settings({
        ...(body.modelChat !== undefined ? {
          chat: body.modelChat, report: body.modelReport, slides: body.modelSlides, image: body.modelImage,
          audio: body.modelAudio, video: body.modelVideo, title: body.modelTitle, code: body.modelCode,
          codeFallback: body.modelCodeFallback, selfHostedThinking: body.thinkingSelfHosted === '1',
          externalThinking: body.thinkingExternal === '1',
        } : {}),
        defaultMonthlyCredits: Number(body.defaultMonthlyCredits ?? next.defaultMonthlyCredits),
      })
      return json(current)
    }
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/models') return json({ litellmAvailable: true, defaultChatModel: '', models: [],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/sessions', '/projects', '/agents', '/skills', '/connectors', '/artifacts', '/designs',
      '/design-templates', '/memory', '/connectors/catalog', '/tools'].includes(path)) {
      return json([])
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { unexpected, puts }
}

test('the administrator decides which model does which job', { tag: '@phone' }, async ({ page }, info) => {
  const { unexpected, puts } = await adminFixture(page, info)
  await page.goto('/admin/system/models')
  const card = page.locator('[data-section="model-roles"]')
  await expect(card).toBeVisible({ timeout: 15_000 })
  // The system pages are links in a named menu: no tabs without tab behaviour.
  const menu = page.getByRole('navigation', { name: '시스템 메뉴' })
  await expect(menu.getByRole('link', { name: '모델', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('tab')).toHaveCount(0)

  // The report default follows the chat default until one is chosen.
  const report = card.getByRole('combobox', { name: /^보고서 기본 모델/ })
  await expect(report).toHaveValue('')
  await expect(report.locator('option').first()).toHaveText('대화 기본 모델을 따름')
  // Side work that reads the conversation offers self-hosted models only.
  const title = card.getByRole('combobox', { name: /^제목·기억·요약 모델/ })
  await expect(title.locator('option')).toHaveText(['대화의 모델을 그대로 사용', 'Qwen · 27B', 'Qwen · Flash'])
  await expect(title).toHaveValue('local/qwen3.8-flash')

  await report.selectOption('local/qwen3.8-flash')
  await card.getByRole('combobox', { name: /^코드 작성 대체 모델/ }).selectOption('')
  await card.getByRole('switch', { name: '자체 호스팅 모델이 답하기 전에 생각' }).click()
  await card.getByRole('button', { name: '저장' }).click()
  await expect(card.getByText('저장했습니다')).toBeVisible()
  expect(puts.at(-1)).toEqual({
    modelChat: 'local/qwen3.8-27b', modelReport: 'local/qwen3.8-flash', modelSlides: 'local/qwen3.8-27b',
    modelImage: '', modelAudio: '', modelVideo: '', modelTitle: 'local/qwen3.8-flash',
    modelCode: 'local/qwen3.8-27b', modelCodeFallback: '', thinkingSelfHosted: '1', thinkingExternal: '0',
  })
  await expect(card.getByRole('switch', { name: '자체 호스팅 모델이 답하기 전에 생각' }))
    .toHaveAttribute('aria-checked', 'true')

  // The card fits a phone width.
  const box = await card.boundingBox()
  expect(box && box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  expect(unexpected).toEqual([])
})

test('the 모델 page shows it is loading until the settings answer', async ({ page }, info) => {
  const { unexpected } = await adminFixture(page, info)
  let release: () => void = () => undefined
  const held = new Promise<void>((resolve) => (release = resolve))
  await page.route('**/api/admin/settings', async (route) => {
    await held
    await route.fallback()
  })
  await page.goto('/admin/system/models')
  await expect(page.getByRole('main').getByRole('status')).toContainText('불러오는 중…', { timeout: 15_000 })
  release()
  await expect(page.locator('[data-section="model-roles"]')).toBeVisible()
  expect(unexpected).toEqual([])
})

test('a refused model is named in words', async ({ page }, info) => {
  const { unexpected } = await adminFixture(page, info)
  await page.goto('/admin/system/models')
  const card = page.locator('[data-section="model-roles"]')
  await expect(card).toBeVisible({ timeout: 15_000 })
  // The server is the authority: a model the list never offered is refused in words.
  const title = card.getByRole('combobox', { name: /^제목·기억·요약 모델/ })
  await title.evaluate((select: HTMLSelectElement) => {
    const option = document.createElement('option')
    option.value = 'openai/gpt-x'
    option.textContent = 'forged'
    select.append(option)
  })
  await title.selectOption('openai/gpt-x')
  await card.getByRole('button', { name: '저장' }).click()
  await expect(card.getByRole('alert')).toHaveText('보조 작업에는 이 인스턴스가 직접 운영하는 모델만 고를 수 있습니다.')
  expect(unexpected).toEqual([])
})

test('a new account’s monthly credits are set with signup', async ({ page }, info) => {
  const { unexpected, puts } = await adminFixture(page, info)
  await page.goto('/admin/system/signup')
  const credits = page.getByRole('spinbutton', { name: /^새 계정의 월 크레딧/ })
  await expect(credits).toHaveValue('500000', { timeout: 15_000 })
  await expect(page.getByText(/약 5달러에 해당/)).toBeVisible()
  await credits.fill('1000000')
  await page.getByRole('button', { name: '저장' }).click()
  await expect.poll(() => puts.at(-1)?.defaultMonthlyCredits).toBe('1000000')
  expect(puts.at(-1)).toMatchObject({ signupMode: 'approval', defaultMonthlyCredits: '1000000' })
  expect(unexpected).toEqual([])
})
