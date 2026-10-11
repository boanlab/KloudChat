import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { at, chatId, composerFixture } from './composer-fixture'
import { shellRead } from './shell-mocks'

/**
 * Text from scans against fixture data: an attached scanned PDF shows its pages being read and
 * then how it was read (with the pages to check), and the administrator chooses how pages are
 * read and which strict-local vision model may see them.
 */

const fileId = 'f0000000000000000000000000000ocr'

function fileRow(extra: Record<string, unknown>) {
  return {
    id: fileId, name: '스캔 규정.pdf', size: 52000, mime: 'application/pdf', tokens: 0, projectId: null,
    sessionId: null, preview: '', error: null, indexed: false, createdAt: at,
    ocrState: 'queued', ocrTotal: 3, ocrDone: 0, ocrReview: 0, ...extra,
  }
}

test('an attached scan shows its pages being read, then what to check', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  // Each poll reads one more page; the third answer is the finished file.
  const answers = [
    fileRow({ ocrState: 'running', ocrDone: 1 }),
    fileRow({ ocrState: 'running', ocrDone: 2 }),
    fileRow({ ocrState: 'done', ocrDone: 3, ocrReview: 1, tokens: 900, preview: '[페이지 1]\n제1조' }),
  ]
  let polls = 0
  await page.route('**/api/files**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.slice(4)
    if (request.method() === 'POST' && path === '/files') return route.fulfill({ status: 201, json: fileRow({}) })
    if (request.method() === 'GET' && path === `/files/${fileId}`) {
      const row = answers[Math.min(polls, answers.length - 1)]
      polls += 1
      return route.fulfill({ json: row })
    }
    return route.fallback()
  })
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.getByLabel('파일 선택').setInputFiles({
    name: '스캔 규정.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 scan'),
  })
  const chip = page.locator('[data-ocr]').filter({ hasText: '스캔 규정.pdf' })
  await expect(chip).toContainText('글자 읽는 중 0/3쪽')
  await expect(chip).toContainText('글자 읽는 중 2/3쪽', { timeout: 15_000 })
  await expect(chip).toContainText('OCR로 읽음 · 확인 필요 1쪽', { timeout: 15_000 })
  await expect(chip).toHaveAttribute('data-ocr', 'done')
  // Once read, nobody asks again.
  const settled = polls
  await page.waitForTimeout(4000)
  expect(polls).toBe(settled)
  // The chip fits a phone width.
  const box = await chip.boundingBox()
  expect(box && box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  expect(unexpected).toEqual([])
})

const admin = {
  id: 'fixture-admin', email: 'admin@example.test', name: '관리자', role: 'admin', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

function settings(ocr: Record<string, unknown>) {
  return {
    litellm: { baseUrl: 'http://litellm', baseUrlSource: 'environment', masterKeySet: true, masterKeyPreview: '…abcd',
      masterKeySource: 'environment' },
    smtp: { host: '', port: '', security: 'starttls', username: '', from: '', appBaseUrl: '', passwordSet: false,
      passwordPreview: '', hostSource: 'environment', passwordResetEnabled: false },
    status: 'ok', brand: { name: 'KloudChat', logo: '' }, contact: { email: 'admin@example.test', source: 'admin' },
    signup: { mode: 'approval', modeSource: 'environment', domains: [], verifyEmail: false, verificationActive: false },
    enabledKinds: ['report', 'slides'],
    tools: {
      backendBaseUrl: 'http://gateway:8080',
      features: [
        { key: 'index', label: '자료 검색', url: 'http://gateway:8080/tools/index', source: 'backend' },
        { key: 'ocr', label: '글자 읽기(OCR)', url: 'http://gateway:8080/tools/ocr', source: 'backend' },
      ],
    },
    ocr: {
      enabled: true, mode: 'both', visionModel: '', maxPages: 300, modeInForce: 'both',
      visionModelInForce: 'strict-local/qwen3.8-27b', engineConfigured: true,
      visionChoices: [{ id: 'strict-local/qwen3.8-27b', label: 'Qwen 27B (내부)' }], ...ocr,
    },
    credits: { perUsd: 1000, budgetHeadroom: 1.2 },
    unpricedModels: [],
  }
}

async function adminFixture(page: Page, info: TestInfo) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const puts: Record<string, unknown>[] = []
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
      current = settings({ enabled: body.ocrEnabled !== '0', mode: body.ocrMode, visionModel: body.ocrVisionModel,
        maxPages: Number(body.ocrMaxPages) })
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

test('the administrator picks how pages are read, and only a strict-local vision model', async ({ page }, info) => {
  const { unexpected, puts } = await adminFixture(page, info)
  await page.goto('/admin/system/features')
  const card = page.locator('[data-section="ocr"]')
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.getByRole('status')).toContainText('지금 쓰는 방식: OCR 엔진 + 비전 모델')
  // The vision model list holds the strict-local one only.
  const models = card.getByRole('combobox', { name: /^비전 모델/ })
  await expect(models.locator('option')).toHaveText(['자동 (Qwen 27B (내부))', 'Qwen 27B (내부)'])
  await card.getByLabel('읽는 방식').selectOption('engine')
  await models.selectOption('strict-local/qwen3.8-27b')
  await card.getByLabel('문서 하나에서 읽는 최대 쪽 수').fill('120')
  await card.getByRole('button', { name: '저장' }).click()
  await expect(card.getByText('저장했습니다')).toBeVisible()
  expect(puts.at(-1)).toEqual({ ocrEnabled: '1', ocrMode: 'engine', ocrVisionModel: 'strict-local/qwen3.8-27b',
    ocrMaxPages: '120' })
  // The address sits with the other features.
  await expect(page.getByLabel('글자 읽기(OCR) 주소')).toBeVisible()
  expect(unexpected).toEqual([])
})
