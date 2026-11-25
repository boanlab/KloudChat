import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'
import { shellRead } from './shell-mocks'

/** 「읽어 주기」: a chat answer and a report are read block by block; the text on the screen stays as it is. */

test.use({ serviceWorkers: 'block' })

const at = '2026-11-11T07:00:00.000Z'

const reply = [
  '네, 세 가지로 정리해 드릴게요.',
  '',
  '- 신청은 10월부터입니다.',
  '- 대상은 설립 7년 이내 기업입니다.',
  '',
  '| 항목 | 내용 |',
  '| --- | --- |',
  '| 규모 | 2억 원 |',
  '',
  '이상입니다.',
].join('\n')

const model = (id: string, kinds: string[], extra: Record<string, unknown> = {}) => ({
  id, label: id, name: id, vendor: 'Fixture', provider: 'fixture', kinds, modality: 'chat',
  dataBoundary: 'external', strictLocal: false, privacyOnly: false, creditCost: 1, inputCreditCost: 1,
  supportsTools: false, description: '', ...extra,
})

const qwen = model('strict-local/qwen3-tts', ['av'], {
  modality: 'audio', dataBoundary: 'self_hosted', strictLocal: true, speechEndpoint: true,
  voices: ['sohee', 'vivian'], creditCost: 0, creditPerCall: 0,
})

/** A silent clip, `seconds` long. */
function wav(seconds = 0.3): Buffer {
  const rate = 24_000
  const samples = Math.floor(rate * seconds)
  const buffer = Buffer.alloc(44 + samples * 2)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + samples * 2, 4)
  buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(rate, 24)
  buffer.writeUInt32LE(rate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples * 2, 40)
  return buffer
}

type Speak = { text: string; voice: string; warm: boolean; spoken?: boolean; source?: string }

/** The model list, with or without the self-hosted voice, and the speech route that records what it is asked. */
async function stub(page: Page, { withVoice, seconds = 0.3 }: { withVoice: boolean; seconds?: number }) {
  const speaks: Speak[] = []
  await page.route('**/api/models', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: {
      litellmAvailable: true, defaultChatModel: 'fixture/model',
      defaults: { chat: 'fixture/model' }, defaultModelByKind: {},
      defaultAvModelByMode: withVoice ? { audio: qwen.id } : {},
      models: [model('fixture/model', ['chat', 'report', 'slides']), ...(withVoice ? [qwen] : [])],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] },
    } })
  })
  await page.route('**/api/audio/speak', (route) => {
    speaks.push(route.request().postDataJSON() as Speak)
    return route.fulfill({ status: 200, contentType: 'audio/wav', body: wav(seconds) })
  })
  return { speaks }
}

test('답변의 「읽어 주기」를 누르면 블록 단위로 읽고, 다시 누르면 멈춘다', async ({ page }, testInfo) => {
  // Each clip lasts long enough to press 멈춤 while the first one plays.
  const { speaks } = await stub(page, { withVoice: true, seconds: 20 })
  const { unexpected } = await composerFixture(page, testInfo, { reply })
  await page.goto(`/s/${chatId}`)

  const read = page.getByRole('button', { name: '읽어 주기', exact: true })
  await expect(read).toHaveCount(1)
  await read.click()

  // The button turns into 멈춤 and the first block is asked for, with the words to put into speech.
  const stop = page.getByRole('button', { name: '멈춤', exact: true })
  await expect(stop).toBeVisible()
  await expect.poll(() => speaks.length).toBeGreaterThan(1)
  expect(speaks[0]).toMatchObject({ voice: 'sohee', spoken: true, source: '네, 세 가지로 정리해 드릴게요.' })
  // A list and a table are each one block, read in spoken words by the server.
  expect(speaks[1].source).toBe('- 신청은 10월부터입니다.\n- 대상은 설립 7년 이내 기업입니다.')
  await expect(page.getByTestId('read-aloud-status')).toHaveText('1/4')

  // The written answer is not touched.
  await expect(page.getByText('신청은 10월부터입니다.').first()).toBeVisible()

  const asked = speaks.length
  await stop.click()
  await expect(page.getByRole('button', { name: '읽어 주기', exact: true })).toHaveCount(1)
  await expect(page.getByTestId('read-aloud-status')).toHaveCount(0)
  await page.waitForTimeout(300)
  // Nothing more is voiced once it is stopped.
  expect(speaks.length).toBe(asked)
  expect(unexpected).toEqual([])
})

test('자체 음성 모델이 없으면 「읽어 주기」가 보이지 않는다', async ({ page }, testInfo) => {
  await stub(page, { withVoice: false })
  const { unexpected } = await composerFixture(page, testInfo, { reply })
  await page.goto(`/s/${chatId}`)
  await expect(page.getByText('네, 세 가지로 정리해 드릴게요.').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '읽어 주기', exact: true })).toHaveCount(0)
  expect(unexpected).toEqual([])
})

const sessionId = 'a7777777777777777777777777777777'
const artifactId = 'b7777777777777777777777777777777'
const emptyLists = [
  '/artifacts', '/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/store',
  '/jobs', '/designs', '/shares', '/design-templates', '/skills/store', '/agents', '/user-pipelines',
]

test('보고서의 「읽어 주기」는 제목과 각 절을 차례로 읽고, 읽는 절을 표시한다', async ({ page }, testInfo) => {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const speaks: Speak[] = []
  const user = {
    id: 'fixture-user', name: 'Fixture', email: 'fixture@example.test', role: 'user', status: 'active',
    monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
    preferences: { autoMemory: false, showUsage: false, streamResponses: true },
  }
  const sections = [
    { id: 's0', heading: '점검 개요', level: 1, status: 'done', content: '이번 점검은 클라우드 보안을 다룹니다.' },
    { id: 's1', heading: '발견 사항', level: 1, status: 'done', content: '접근 권한이 넓게 열려 있었습니다.' },
  ]
  const artifact = {
    id: artifactId, title: '보안 점검 결과', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '보안 점검 결과', sources: [], sections },
  }
  const made = { id: 'm2', role: 'assistant', content: '보고서를 만들었습니다.', createdAt: at, artifactIds: [artifactId] }
  const session = {
    id: sessionId, kind: 'report', title: '보안 점검', projectId: null, agentId: null,
    model: 'fixture/model', routingMode: 'manual', artifactId, skillIds: [],
    renderTemplateId: null, pinned: false, createdAt: at, updatedAt: at, preview: null,
    messageCount: 2, made: null, pending: null,
    messages: [{ id: 'm1', role: 'user', content: '보안 점검 보고서', createdAt: at }, made],
  }

  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = req.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (method === 'POST' && path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    if (method === 'GET' && path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (method === 'GET' && path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/model', defaults: { chat: 'fixture/model' },
      defaultModelByKind: {}, defaultAvModelByMode: { audio: qwen.id },
      models: [model('fixture/model', ['chat', 'report', 'slides']), qwen],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] },
    })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/sessions') return json([{ ...session, messages: null }])
    if (method === 'GET' && path === `/sessions/${sessionId}`) return json(session)
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return json(artifact)
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (method === 'POST' && path === '/audio/speak') {
      speaks.push(req.postDataJSON() as Speak)
      return route.fulfill({ status: 200, contentType: 'audio/wav', body: wav(20) })
    }
    if (method === 'GET' && (path.endsWith('/messages') || path.endsWith('/jobs') || path.endsWith('/versions'))) return json([])
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && emptyLists.includes(path)) return json([])
    if (method === 'GET' && path.startsWith('/design-templates/')) return json({ detail: 'not_found' }, 404)
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'not_found' }, 404)
  })

  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel.getByText('이번 점검은 클라우드 보안을 다룹니다.')).toBeVisible({ timeout: 20_000 })

  await panel.getByRole('button', { name: '읽어 주기', exact: true }).click()
  await expect(panel.getByRole('button', { name: '멈춤', exact: true })).toBeVisible()
  // The title first, then the sections in order, each under its heading.
  await expect.poll(() => speaks.length).toBeGreaterThan(2)
  expect(speaks.slice(0, 3).map((s) => s.source ?? s.text)).toEqual([
    '보안 점검 결과',
    '점검 개요. 이번 점검은 클라우드 보안을 다룹니다.',
    '발견 사항. 접근 권한이 넓게 열려 있었습니다.',
  ])
  await expect(panel.getByTestId('read-aloud-status')).toContainText('1/3')

  await panel.getByRole('button', { name: '멈춤', exact: true }).click()
  await expect(panel.getByRole('button', { name: '읽어 주기', exact: true })).toBeVisible()
  expect(unexpected).toEqual([])
})
