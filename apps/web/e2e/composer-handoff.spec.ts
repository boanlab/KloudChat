import { expect, test, type Page } from '@playwright/test'

const id = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const otherId = 'cccccccccccccccccccccccccccccccc'
const reportId = 'dddddddddddddddddddddddddddddddd'
const otherChatId = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
const at = '2026-09-08T00:00:00.000Z'

async function handoffFixture(page: Page) {
  const sessions: Record<string, unknown>[] = [
    { id: otherId, title: '다른 대화', kind: 'chat' },
    { id: reportId, title: '다른 보고서', kind: 'report' },
    { id: otherChatId, title: '별도 대화', kind: 'chat' },
  ].map((row) => ({ model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: [], messageCount: 0, made: null, createdAt: at, updatedAt: at, ...row }))
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const models = ['base', 'economy', 'quality'].map((name) => ({
    id: `fixture/${name}`, label: `Fixture ${name}`, name: `Fixture ${name}`,
    vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
    dataBoundary: 'external', strictLocal: false, privacyOnly: false,
    creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000,
  }))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api', '')
    const method = request.method()
    if (path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-token', expiresIn: 3600,
        user: { id: 'fixture-user', name: '복원 검증', email: 'fixture@example.test',
          role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
          avatarColor: '#168267', allowedModels: [], createdAt: at,
          preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    }
    if (path === '/auth/config') {
      return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false },
        passwordResetEnabled: false, dictationEnabled: false } })
    }
    if (path === '/models') {
      return route.fulfill({ json: { models, defaultChatModel: 'fixture/base',
        litellmAvailable: true, autoRouting: { enabled: true, available: true,
          economyModelIds: ['fixture/economy'], qualityEnabled: true,
          qualityAvailable: true, qualityModelIds: ['fixture/quality'] } } })
    }
    if (path === '/credits') {
      return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    }
    if (path === '/sessions' && method === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, body })
      const row = { id, title: '복원 검증', kind: 'chat', model: 'fixture/base',
        routingMode: 'manual', projectId: null, agentId: null, artifactId: null,
        pinned: false, messages: [], messageCount: 0, createdAt: at, updatedAt: at, ...body }
      sessions.push(row)
      return route.fulfill({ status: 201, json: row })
    }
    if (path === '/sessions' && method === 'GET') return route.fulfill({ json: sessions })
    if (/^\/sessions\/[^/]+$/.test(path) && method === 'GET') {
      const session = sessions.find((row) => row.id === path.split('/')[2])
      return route.fulfill({ status: session ? 200 : 404, json: session ?? {} })
    }
    if (/^\/sessions\/[^/]+\/messages$/.test(path) && method === 'GET') {
      return route.fulfill({ json: [] })
    }
    if (path === '/files' && method === 'POST') {
      return route.fulfill({ status: 201, json: { id: 'fixture-file', name: 'draft.txt',
        mime: 'text/plain', size: 5, tokens: 2, preview: 'draft', error: null,
        projectId: null, sessionId: null, createdAt: at } })
    }
    if (method === 'GET' && ['/projects', '/artifacts', '/skills', '/memory', '/agents',
      '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/prompt-templates'].includes(path)) {
      return route.fulfill({ json: [] })
    }
    if (method !== 'GET') writes.push({ path, body: request.postDataJSON() ?? {} })
    return route.abort('blockedbyclient')
  })
  return writes
}

async function openSavedSession(page: Page, name: string) {
  const button = page.getByRole('button', { name, exact: true })
  const close = page.locator('button[aria-label="사이드바 닫기"]')
  const narrow = await close.count() > 0
  if (narrow && await close.getAttribute('aria-hidden') === 'true') {
    await page.getByRole('button', { name: '사이드바 토글', exact: true }).click()
  }
  await expect(button).toBeInViewport()
  await button.click()
  if (narrow) {
    await expect(close).toHaveAttribute('aria-hidden', 'true')
    await expect(button).not.toBeInViewport()
  }
}

async function attachDraft(page: Page) {
  await page.getByLabel('프롬프트 입력').fill('전환 후에도 보존할 초안')
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'draft.txt', mimeType: 'text/plain', buffer: Buffer.from('draft'),
  })
  await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toBeVisible()
  await page.getByRole('button', { name: '웹 검색: 자동', exact: true }).click()
}

for (const mode of ['auto', 'auto_quality'] as const) {
  for (const search of ['켬', '끔']) {
    test(`${mode} 새 세션 전환은 첨부와 검색 ${search} 상태를 한 번 복원한다`, async ({ page }, testInfo) => {
      const writes = await handoffFixture(page)
      await page.goto('/new/chat')
      await attachDraft(page)
      if (search === '끔') await page.getByRole('button', { name: '웹 검색: 켬', exact: true }).click()
      await page.getByRole('button', { name: /Fixture base/ }).click()
      await page.getByRole('button', { name: mode === 'auto' ? /Auto · 비용 절약/ : /Auto · 품질 우선/ }).click()
      await expect(page).toHaveURL(`/s/${id}`)
      await expect(page.getByLabel('프롬프트 입력')).toHaveValue('전환 후에도 보존할 초안')
      await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toBeVisible()
      await expect(page.getByRole('button', { name: `웹 검색: ${search}`, exact: true })).toBeVisible()
      expect(writes).toEqual([{ path: '/sessions', body: expect.objectContaining({ routingMode: mode }) }])
      // Repeated renders must not consume or reset the restored state again.
      await page.getByLabel('프롬프트 입력').fill('전환 후 다시 수정한 초안')
      await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toBeVisible()
      await expect(page.getByRole('button', { name: `웹 검색: ${search}`, exact: true })).toBeVisible()
      if (process.env.QA_CAPTURE_HANDOFF === '1' && mode === 'auto_quality' && search === '켬') {
        await page.evaluate(() => document.fonts.ready)
        await expect.poll(() => page.evaluate(() => document.getAnimations()
          .filter((animation) => animation.playState === 'running'
            && Number.isFinite(animation.effect?.getComputedTiming().endTime)).length)).toBe(0)
        await page.screenshot({ path: testInfo.outputPath('restored-draft.png'), fullPage: false })
      }
      await openSavedSession(page, '다른 대화')
      await expect(page).toHaveURL(`/s/${otherId}`)
      await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toHaveCount(0)
      await expect(page.getByRole('button', { name: '웹 검색: 자동', exact: true })).toBeVisible()
      await openSavedSession(page, '복원 검증')
      await expect(page.getByLabel('프롬프트 입력')).toHaveValue('전환 후 다시 수정한 초안')
      await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toHaveCount(0)
      await expect(page.getByRole('button', { name: '웹 검색: 자동', exact: true })).toBeVisible()
    })
  }
}

for (const target of [{ id: reportId, title: '다른 보고서' }, { id: otherChatId, title: '별도 대화' }]) {
  test(`기존 대화에서 ${target.title} 이동 시 첨부와 검색을 초기화한다`, async ({ page }) => {
    await handoffFixture(page)
    await page.goto(`/s/${otherId}`)
    await attachDraft(page)
    await openSavedSession(page, target.title)
    await expect(page).toHaveURL(`/s/${target.id}`)
    await expect(page.getByRole('button', { name: 'draft.txt 제거' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '웹 검색: 자동', exact: true })).toBeVisible()
    await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  })
}
