import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * 「이 아티팩트만 공유」: the artifact panel makes a link to one artifact without its conversation,
 * and the shared page shows pictures, sound, clips, charts and HTML with the app's own views.
 */

test.use({ serviceWorkers: 'block' })

const sessionId = '11111111111111111111111111111111'
const artifactId = '22222222222222222222222222222222'
const fileId = '33333333333333333333333333333333'
const at = '2026-11-10T00:00:00.000Z'
const user = {
  id: 'fixture-user', name: '공유 검증', email: 'fixture@example.test', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}
const config = { brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report'],
  privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false }
// 1×1 transparent PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
)

async function owner(page: Page, info: TestInfo) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const created: unknown[] = []
  const revoked: string[] = []
  let live: Record<string, unknown> | null = null
  const artifact = { id: artifactId, title: '스터디 일정', kind: 'html', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'html', content: '<h1>스터디 일정</h1>' } }
  const session = { id: sessionId, title: '스터디 일정', kind: 'chat', model: 'fixture/base',
    routingMode: 'manual', projectId: null, agentId: null, artifactId, pinned: false, messages: [],
    messageCount: 0, made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') {
      return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    }
    if (method === 'GET' && path === '/auth/config') return json(config)
    if (method === 'GET' && path === '/models') return json({ models: [], defaultChatModel: '',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } })
    if (method === 'GET' && path === '/credits') {
      return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    }
    if (method === 'GET' && path === '/sessions') return json([session])
    if (method === 'GET' && path === `/sessions/${sessionId}`) return json(session)
    if (method === 'GET' && path === '/artifacts') return json([artifact])
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return json(artifact)
    if (method === 'GET' && path === '/artifacts/counts') {
      return json({ counts: { html: 1 }, total: 1 })
    }
    if (method === 'GET' && path === '/shares') return json(live ? [live] : [])
    if (method === 'POST' && path === '/shares') {
      const asked = request.postDataJSON() as {
        artifactId?: string
        sessionId?: string
        scope: string
      }
      created.push(asked)
      live = { id: 'share-1', token: 'tok-artifact', artifactId: asked.artifactId ?? null,
        sessionId: asked.sessionId ?? null, scope: asked.scope, views: 0, createdAt: at }
      return json(live, 201)
    }
    if (method === 'GET' && path === '/shares/share-1/views') return json([])
    if (method === 'DELETE' && path === '/shares/share-1') {
      revoked.push('share-1')
      live = null
      return route.fulfill({ status: 204 })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`,
      '/projects', '/skills', '/memory', '/agents', '/tools', '/connectors', '/connectors/catalog',
      '/jobs', '/designs', '/design-templates'].includes(path)) return json([])
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  return { unexpected, created, revoked }
}

test('the panel shares the artifact alone, not its conversation', async ({ page }, info) => {
  const state = await owner(page, info)
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '이 아티팩트만 공유' }).click()
  const dialog = page.getByRole('dialog', { name: '이 아티팩트만 공유' })
  await expect(dialog).toContainText('대화는 빼고 이 아티팩트만 보입니다.')
  await dialog.getByRole('button', { name: /링크가 있는 사람/ }).click()
  await dialog.getByRole('button', { name: '링크 만들기' }).click()
  await expect(dialog.getByRole('textbox', { name: '공유 링크' })).toHaveValue(/\/share\/tok-artifact$/)
  expect(state.created).toEqual([{ artifactId, scope: 'link' }])

  // The conversation's own 공유 does not take the artifact's link for its own.
  await dialog.getByRole('button', { name: '완료' }).click()
  await page.getByRole('button', { name: '공유', exact: true }).click()
  const conversation = page.getByRole('dialog', { name: '공유' })
  await expect(conversation.getByRole('button', { name: '링크 만들기' })).toBeVisible()
  await conversation.getByRole('button', { name: '완료' }).click()

  // Opened again, the artifact's link is there and can be revoked.
  await panel.getByRole('button', { name: '이 아티팩트만 공유' }).click()
  await dialog.getByRole('button', { name: '링크 철회' }).click()
  await expect(dialog.getByRole('button', { name: '링크 만들기' })).toBeVisible()
  expect(state.revoked).toEqual(['share-1'])
  expect(state.unexpected).toEqual([])
})

/** The shared page for one payload; media answers on the share's own route. */
async function shared(page: Page, info: TestInfo, payload: unknown, signedIn = false) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const media: string[] = []
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') {
      return signedIn
        ? json({ accessToken: 'fixture-only', expiresIn: 3600, user })
        : json({ detail: 'not_signed_in' }, 401)
    }
    if (path === '/auth/config') return json(config)
    if (path === '/shared/tok-1') return json(payload)
    if (path.startsWith('/shared/tok-1/files/')) {
      media.push(`${path}${url.search}`)
      return route.fulfill({ body: PNG, headers: { 'Content-Type': 'image/png' } })
    }
    return json(route.request().method() === 'GET' ? [] : {})
  })
  await page.goto('/share/tok-1')
  return { media }
}

const one = (artifactKind: string, data: Record<string, unknown>) => ({
  kind: 'artifact', title: '공유 자료', artifactKind, data, updatedAt: at,
})
const src = `/api/shared/tok-1/files/${fileId}`

test('a shared picture comes from the share route, account or not', async ({ page }, info) => {
  const state = await shared(page, info, one('image', { kind: 'image', src, prompt: '바다 위 등대',
    aspect: '1:1', style: '', model: 'fixture/image' }))
  const picture = page.locator('[data-shared-kind="image"] img')
  await expect(picture).toHaveAttribute('src', src)
  await expect.poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1)
  expect(state.media).toEqual([`/shared/tok-1/files/${fileId}`])
  await expect(page.getByText('이 종류의 아티팩트는 아직')).toHaveCount(0)
})

test('a members-only share asks for its media with the reader\'s token', async ({ page }, info) => {
  const state = await shared(page, info, one('image', { kind: 'image', src, prompt: '등대',
    aspect: '1:1', style: '', model: 'fixture/image' }), true)
  const picture = page.locator('[data-shared-kind="image"] img')
  await expect.poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1)
  expect(state.media).toEqual([`/shared/tok-1/files/${fileId}?t=fixture-only`])
})

test('shared sound plays in the app\'s own player, with its script', async ({ page }, info) => {
  await shared(page, info, one('audio', { kind: 'audio', src, prompt: '안내 방송',
    audioKind: 'narration', durationSec: 12, model: 'fixture/tts', waveform: [],
    transcript: '안녕하세요.' }))
  await expect(page.locator('[data-shared-kind="audio"] audio')).toHaveAttribute('src', src)
  await expect(page.getByText('안녕하세요.')).toBeVisible()
})

test('a shared clip plays in the app\'s own player', async ({ page }, info) => {
  await shared(page, info, one('video', { kind: 'video', src, posterSrc: '', prompt: '파도',
    aspect: '16:9', durationSec: 4, model: 'fixture/video' }))
  await expect(page.locator('[data-shared-kind="video"] video')).toHaveAttribute('src', src)
})

test('a shared chart is drawn, not printed as data', async ({ page }, info) => {
  await shared(page, info, one('chart', { kind: 'chart', chartType: 'bar', caption: '분기별 매출',
    xLabel: '분기', yLabel: '억 원', sourceFile: '',
    table: { columns: ['분기', '매출'], rows: [['1분기', 3]] },
    series: [{ name: '매출', color: '#2563eb', points: [{ x: '1분기', y: 3 }, { x: '2분기', y: 5 }] }] }))
  await expect(page.locator('[data-shared-kind="chart"] svg').first()).toBeVisible()
  await expect(page.getByText('분기별 매출')).toBeVisible()
})

test('shared HTML runs in the app preview\'s sandbox, never as source', async ({ page }, info) => {
  await shared(page, info, one('html', { kind: 'html',
    content: '<h1 id="t">미리보기</h1>'
      + '<script>document.getElementById("t").textContent = "스크립트 실행"</script>' }))
  const frame = page.locator('[data-shared-kind="html"] iframe')
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
  const inside = page.frameLocator('[data-shared-kind="html"] iframe')
  await expect(inside.locator('#t')).toHaveText('스크립트 실행')
  // Not the source in a code block.
  await expect(page.locator('pre').filter({ hasText: '<h1' })).toHaveCount(0)
  // An opaque origin: the frame cannot reach the page that holds it.
  const reach = await inside.locator('body').evaluate(() => {
    try {
      return String(window.parent.document.title)
    } catch {
      return 'blocked'
    }
  })
  expect(reach).toBe('blocked')
})
