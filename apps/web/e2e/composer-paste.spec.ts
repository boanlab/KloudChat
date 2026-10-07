import { expect, test, type Page } from '@playwright/test'

// A screenshot on the clipboard pasted into the composer is attached and sent with the
// next message; a text paste stays text. The backend is a fixture: nothing leaves the page.

const sessionId = 'ffffffffffffffffffffffffffffffff'
const at = '2026-10-04T00:00:00.000Z'
// A 1×1 PNG: what a screenshot tool puts on the clipboard, only smaller.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

type Upload = { name: string; type: string }

async function fixture(page: Page, reply = '에러 코드는 503입니다.', sendStatus = 200) {
  const uploads: Upload[] = []
  const sends: Record<string, unknown>[] = []
  const unexpected: string[] = []
  let session: Record<string, unknown> | null = null
  // After a turn the client reloads the transcript: it holds what was sent and said.
  const transcript = () => sends.flatMap((sent, n) => [
    { id: `asked-${n}`, role: 'user', content: sent.content, createdAt: at },
    { id: `answer-${n}`, role: 'assistant', content: reply, createdAt: at, steps: [],
      failure: null, model: 'fixture/vision' },
  ])
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api', '')
    const method = request.method()
    if (path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-token', expiresIn: 3600,
        user: { id: 'fixture-user', name: '붙여넣기 검증', email: 'fixture@example.test',
          role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
          avatarColor: '#168267', allowedModels: [], createdAt: at,
          preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    }
    if (path === '/auth/config') {
      return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat'], privacy: { externalDataGuard: false },
        passwordResetEnabled: false, dictationEnabled: false } })
    }
    if (path === '/models') {
      return route.fulfill({ json: { models: [{
        id: 'fixture/vision', label: 'Fixture vision', name: 'Fixture vision', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'self_hosted',
        strictLocal: true, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: true, contextWindow: 64000,
      }], defaultChatModel: 'fixture/vision', litellmAvailable: true } })
    }
    if (path === '/credits') {
      return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    }
    if (path === '/files' && method === 'POST') {
      // Multipart: the file part's name and type are what the composer pasted.
      const body = request.postDataBuffer()?.toString('latin1') ?? ''
      const name = /filename="([^"]+)"/.exec(body)?.[1] ?? ''
      const type = /Content-Type: ([^\r\n]+)\r\n\r\n/.exec(body)?.[1] ?? ''
      uploads.push({ name, type })
      return route.fulfill({ status: 201, json: { id: `pasted-${uploads.length}`, name,
        mime: type, size: 68, tokens: 0, preview: '', error: null,
        projectId: null, sessionId: null, createdAt: at } })
    }
    if (path === '/sessions' && method === 'POST') {
      session = { id: sessionId, title: '스크린샷', kind: 'chat', model: 'fixture/vision',
        routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pinned: false,
        messages: [], messageCount: 0, createdAt: at, updatedAt: at }
      return route.fulfill({ status: 201, json: session })
    }
    if (path === '/sessions' && method === 'GET') return route.fulfill({ json: session ? [session] : [] })
    if (path === `/sessions/${sessionId}` && method === 'GET') {
      const messages = transcript()
      return route.fulfill({ status: session ? 200 : 404,
        json: session ? { ...session, messages, messageCount: messages.length } : {} })
    }
    if (path === `/sessions/${sessionId}/messages` && method === 'GET') {
      return route.fulfill({ json: transcript() })
    }
    if (path === `/sessions/${sessionId}/messages` && method === 'POST') {
      sends.push(request.postDataJSON() as Record<string, unknown>)
      // A gateway that timed out: the server stored and answered the turn all the same.
      if (sendStatus !== 200) return route.fulfill({ status: sendStatus, body: 'Gateway Timeout' })
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: `data: ${JSON.stringify({ type: 'delta', text: reply })}\n\ndata: {"type":"done"}\n\n`,
      })
    }
    if (method === 'GET' && ['/projects', '/artifacts', '/skills', '/memory', '/agents',
      '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/prompt-templates', '/artifacts/counts'].includes(path)) {
      return route.fulfill({ json: path === '/artifacts/counts' ? { counts: {}, total: 0 } : [] })
    }
    if (method !== 'GET') unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  return { uploads, sends, unexpected }
}

/** Dispatches a paste carrying `files` (and `text`) on the composer; returns whether the
 *  page let the browser's own paste go ahead. */
async function paste(page: Page, files: { name: string; type: string }[], text = '') {
  return page.getByLabel('프롬프트 입력').evaluate(
    (input, { files, png, text }) => {
      const data = new DataTransfer()
      const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0))
      for (const file of files) data.items.add(new File([bytes], file.name, { type: file.type }))
      if (text) data.setData('text/plain', text)
      const event = new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })
      return input.dispatchEvent(event)
    },
    { files, png: PNG, text },
  )
}

test('a pasted screenshot is attached and sent with the next message', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/new/chat')
  const input = page.getByLabel('프롬프트 입력')
  await input.click()
  const proceeded = await paste(page, [{ name: 'image.png', type: 'image/png' }])
  // The composer took the file; the browser does not paste it as text as well.
  expect(proceeded).toBe(false)
  await expect(page.getByRole('button', { name: 'image.png 제거', exact: true })).toBeVisible()
  expect(state.uploads).toEqual([{ name: 'image.png', type: 'image/png' }])

  await input.fill('이 스크린샷의 에러 코드 알려 줘')
  await input.press('Enter')
  await expect.poll(() => state.sends.length).toBe(1)
  expect(state.sends[0]).toEqual(expect.objectContaining({
    content: '이 스크린샷의 에러 코드 알려 줘',
    attachments: ['pasted-1'],
  }))
  expect(state.unexpected).toEqual([])
})

test('several images pasted at once are all attached', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').click()
  await paste(page, [
    { name: 'before.png', type: 'image/png' },
    { name: 'after.png', type: 'image/png' },
  ])
  await expect(page.getByRole('button', { name: 'before.png 제거', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'after.png 제거', exact: true })).toBeVisible()
  expect(state.uploads.map((u) => u.name)).toEqual(['before.png', 'after.png'])
})

test('a text paste stays text and uploads nothing', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').click()
  const proceeded = await paste(page, [], 'ERROR 503: upstream timeout')
  // The page leaves a text paste to the browser.
  expect(proceeded).toBe(true)
  expect(state.uploads).toEqual([])
  await expect(page.getByRole('button', { name: /제거$/ })).toHaveCount(0)
})

// A range written with one tilde is not struck through, and a table whose header the
// model glued to the sentence before it still renders as a table.
test('a reply keeps its ranges unstruck and its glued table a table', async ({ page }) => {
  const reply = [
    '저역(100~500 Hz)에서는 거의 0 dB이고, 고역(500~10000 Hz)에서 오차가 커집니다. ~~취소~~',
    '',
    '결과는 다음과 같습니다. | 주파수 (Hz) | 측정 이득 (dB) |',
    '| :--- | :--- |',
    '| 100 | -0.09 |',
    '| 1590 | -3.09 |',
  ].join('\n')
  const state = await fixture(page, reply)
  await page.goto('/new/chat')
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('결과 정리해 줘')
  await input.press('Enter')
  await expect.poll(() => state.sends.length).toBe(1)
  const table = page.getByRole('table')
  await expect(table).toBeVisible()
  await expect(table.getByRole('cell', { name: '1590', exact: true })).toBeVisible()
  await expect(page.getByText('결과는 다음과 같습니다.', { exact: true })).toBeVisible()
  // Only the double tilde strikes.
  await expect(page.locator('del')).toHaveText(['취소'])
  await expect(page.getByText(/100~500 Hz/)).toBeVisible()
})

// A send that times out at the gateway is not a refusal: the draft is not handed back to
// be sent twice, and the answer the server went on to write shows in the conversation.
test('a gateway timeout on send shows the turn instead of a refusal', async ({ page }) => {
  const state = await fixture(page, '답이 서버에서 계속 쓰였습니다.', 504)
  await page.goto('/new/chat')
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('긴 보고서를 써 줘')
  await input.press('Enter')
  await expect.poll(() => state.sends.length).toBe(1)
  await expect(page.getByText('답이 서버에서 계속 쓰였습니다.')).toBeVisible()
  await expect(page.getByText(/요청이 거부되었습니다/)).toHaveCount(0)
  await expect(page.getByText(/응답이 늦어 연결이 끊겼습니다/)).toHaveCount(0)
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})
