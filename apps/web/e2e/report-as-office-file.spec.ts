import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A report asked for as an HWP or Word file, the API mocked: the chat hands it to the report
 * screen, the written report arrives as that file in its editor (no HTML report panel), and a
 * follow-up in that conversation is a chat turn whose tools save the file's next version.
 * A short document in a file (a 공문) stays with the editor beside the chat.
 */

test.use({ serviceWorkers: 'block' })

const hwpx = readFileSync(new URL('./fixtures/hwp-official.hwpx', import.meta.url))
const docx = readFileSync(new URL('./fixtures/word-official.docx', import.meta.url))
const studio = readFileSync(new URL('./fixtures/fake-rhwp.html', import.meta.url), 'utf8')
const at = '2026-11-13T00:00:00.000Z'
const artifactId = 'cccccccccccccccccccccccccccccccc'

type Kind = 'hwp' | 'docx'

async function fixture(page: Page, kind: Kind) {
  const unexpected: string[] = []
  const created: Record<string, unknown>[] = []
  const sent: Record<string, unknown>[] = []
  const file = kind === 'hwp' ? hwpx : docx
  const mime = kind === 'hwp' ? 'application/hwp+zip'
    : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  let artifact: Record<string, unknown> | null = null
  let session: Record<string, unknown> | null = null
  let messages: Record<string, unknown>[] = []
  await page.route('**/rhwp/**', (route) => route.fulfill({ contentType: 'text/html', body: studio }))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: 'Office', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/base',
      models: [{
        id: 'fixture/base', label: 'Fixture', name: 'Fixture', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat', dataBoundary: 'external',
        creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000,
      }],
      autoRouting: { enabled: false, available: false },
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && method === 'POST') {
      const body = request.postDataJSON()
      created.push(body)
      session = {
        id: body.id, title: '', kind: body.kind, model: 'fixture/base', routingMode: 'manual',
        projectId: null, agentId: null, artifactId: null, pinned: false, messages: [],
        messageCount: 0, made: null, createdAt: at, updatedAt: at,
      }
      return json(session)
    }
    if (path === '/sessions' && method === 'GET') return json(session ? [session] : [])
    if (session && path === `/sessions/${session.id}` && method === 'GET') return json(session)
    if (session && path === `/sessions/${session.id}/messages` && method === 'GET') return json(messages)
    if (session && path === `/sessions/${session.id}/messages` && method === 'POST') {
      const body = request.postDataJSON()
      sent.push(body)
      const first = !artifact
      artifact = {
        id: artifactId, title: '2027년 사업계획', kind, version: first ? 1 : 2, partial: false,
        sessionId: session.id, projectId: null, createdAt: at, updatedAt: at,
        data: { format: kind === 'hwp' ? 'hwpx' : 'docx', size: file.length, text: '2027년 사업계획' },
      }
      const answer = first
        ? '6개 섹션의 보고서를 한글 문서로 만들었습니다. 옆 편집기에서 바로 고치거나, 고칠 곳을 말해 주세요.'
        : '3장을 두 문단으로 줄였습니다.'
      messages = [
        ...messages,
        { id: `q${messages.length}`, role: 'user', content: String(body.content), createdAt: at },
        { id: `a${messages.length}`, role: 'assistant', content: answer, createdAt: at },
      ]
      Object.assign(session, { artifactId, messages, messageCount: messages.length, title: '2027년 사업계획' })
      // the report run: steps, then the stored file; a follow-up: the chat turn's tools
      const events = first
        ? [
            { type: 'step', id: 'research', label: '자료 조사', status: 'done' },
            { type: 'step', id: 'write', label: '본문 작성', status: 'done' },
            { type: 'artifact', artifactId },
            { type: 'usage', inputTokens: 10, outputTokens: 5, credits: 1 },
            { type: 'done' },
          ]
        : [
            { type: 'step', id: 'h1_0', label: '문서 확인', status: 'done' },
            { type: 'step', id: 'h2_0', label: '문서 편집', status: 'done', detail: '1건',
              artifactId, artifactVersion: 2 },
            { type: 'delta', text: answer },
            { type: 'artifact', artifactId, deliberate: true },
            { type: 'usage', inputTokens: 10, outputTokens: 5, credits: 1 },
            { type: 'done' },
          ]
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
        body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      })
    }
    if (path === '/artifacts' && method === 'GET') return json(artifact ? [artifact] : [])
    if (path === `/artifacts/${artifactId}` && method === 'GET') return json(artifact)
    if (path === `/artifacts/${artifactId}/versions`) return json([])
    if (path === `/artifacts/${artifactId}/${kind}` && method === 'GET') {
      return route.fulfill({
        body: file, contentType: mime,
        headers: { 'X-Artifact-Version': String(artifact?.version ?? 1) },
      })
    }
    if (path === '/artifacts/counts') return json({ counts: artifact ? { [kind]: 1 } : {}, total: artifact ? 1 : 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/skills', '/memory', '/agents', '/tools', '/connectors',
      '/connectors/catalog', '/jobs', '/designs', '/design-templates', '/shares'].includes(path)) {
      return json([])
    }
    if (method === 'GET' && session && path === `/sessions/${session.id}/jobs`) return json([])
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  await page.goto('/new/chat')
  return { unexpected, created, sent }
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
/** The HTML report's panel, by its web/page view switch. */
const reportPanel = (page: Page) => page.getByRole('group', { name: '보기 방식' })

async function ask(page: Page, text: string) {
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill(text)
  await page.keyboard.press('Enter')
}

test('a report asked for as an HWP file is written by the report screen and opens as that file', async ({ page }) => {
  const state = await fixture(page, 'hwp')
  await ask(page, '한글 문서로 2027 사업계획 보고서 써 줘')
  // handed to the report screen, which writes it
  await expect.poll(() => state.created.map((body) => body.kind)).toEqual(['report'])
  await expect(page.locator('[data-hwp-panel]')).toBeVisible({ timeout: 30_000 })
  await expect(page.frameLocator('[data-hwp-panel] iframe').locator('#doc'))
    .toHaveText(`document.hwpx ${hwpx.length}`)
  // the file, not an HTML report beside it
  await expect(reportPanel(page)).toHaveCount(0)
  await expect(panel(page).locator('header').getByText('v1', { exact: true })).toBeVisible()

  // a follow-up edits the file: a chat turn, its tools' version shown in the same editor
  await ask(page, '3장 줄여 줘')
  await expect(page.getByText('3장을 두 문단으로 줄였습니다.').first()).toBeVisible()
  await expect(panel(page).locator('header').getByText('v2', { exact: true })).toBeVisible()
  expect(state.sent.map((body) => body.content)).toEqual(['한글 문서로 2027 사업계획 보고서 써 줘', '3장 줄여 줘'])
  await expect(reportPanel(page)).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('a report asked for as a Word file opens in the Word editor', async ({ page }) => {
  const state = await fixture(page, 'docx')
  await ask(page, 'Word 파일로 3분기 실적 보고서 작성해 줘')
  await expect.poll(() => state.created.map((body) => body.kind)).toEqual(['report'])
  await expect(page.locator('[data-docx-panel] .odx-content').first()).toBeVisible({ timeout: 30_000 })
  await expect(reportPanel(page)).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('a short document asked for as a file stays with the editor beside the chat', async ({ page }) => {
  const state = await fixture(page, 'hwp')
  await ask(page, '직원 연수 참석을 요청하는 공문을 한글 문서로 작성해 줘')
  await expect.poll(() => state.created.map((body) => body.kind)).toEqual(['chat'])
})
