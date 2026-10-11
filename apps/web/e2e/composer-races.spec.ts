import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'

/**
 * A fast typist must not lose what they typed: a send before the workspace has loaded waits
 * with the text in the box, a new conversation keeps the text until the server takes the turn,
 * and a file attached right after choosing an agent stays attached.
 */

function gate() {
  let open: () => void = () => undefined
  const opened = new Promise<void>((resolve) => { open = resolve })
  return { opened, open }
}

/** Whether the model list has been answered; the composer sends right away from then on. */
async function modelsServed(page: Page) {
  let served = false
  await page.route('**/api/models', async (route) => {
    await route.fallback()
    served = true
  })
  return () => served
}

async function holdModels(page: Page) {
  const models = gate()
  await page.route('**/api/models', async (route) => {
    await models.opened
    await route.fallback()
  })
  return models
}

test('Enter while the models still load waits, then sends what was typed', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  const models = await holdModels(page)
  await page.goto('/')
  const input = page.getByLabel('프롬프트 입력')
  await expect(input).toBeVisible()
  await input.fill('안녕하세요 테스트')
  await input.press('Enter')
  await expect(page.getByText('모델을 불러오면 바로 보냅니다')).toBeVisible()
  await expect(input).toHaveValue('안녕하세요 테스트')
  expect(sends).toHaveLength(0)
  models.open()
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.content).toBe('안녕하세요 테스트')
  await expect(page).toHaveURL(/\/s\//)
  await expect(input).toHaveValue('')
  expect(unexpected).toEqual([])
})

test('a new conversation keeps the text in the box until the server takes it', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  const create = gate()
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() === 'POST') await create.opened
    await route.fallback()
  })
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('느린 서버에서도 남아 있어야 합니다')
  await input.press('Enter')
  await expect(page.getByRole('button', { name: '전송' })).toHaveAttribute('title', '보내는 중…')
  await expect(input).toHaveValue('느린 서버에서도 남아 있어야 합니다')
  create.open()
  await expect.poll(() => sends.length).toBe(1)
  await expect(page).toHaveURL(/\/s\//)
  await expect(input).toHaveValue('')
  expect(unexpected).toEqual([])
})

test('a refused first turn leaves the text where it was typed', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 503, json: { detail: 'busy' } })
    await route.fallback()
  })
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('거절돼도 남아야 합니다')
  await input.press('Enter')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(input).toHaveValue('거절돼도 남아야 합니다')
  await expect(page).toHaveURL(/\/$/)
  expect(unexpected).toEqual([])
})

test('a file attached while an agent conversation opens stays attached', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  const create = gate()
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() === 'POST') await create.opened
    await route.fallback()
  })
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  await page.getByRole('button', { name: '도구', exact: true }).click()
  await page.getByRole('menuitem', { name: '에이전트' }).click()
  await page.getByRole('menuitem', { name: '논문 리뷰어' }).click()
  await page.getByLabel('파일 선택').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('회의 메모') })
  create.open()
  await expect(page).toHaveURL(/\/s\//)
  await expect(page.getByTitle('이 대화의 에이전트')).toContainText('논문 리뷰어')
  await expect(page.getByRole('button', { name: 'notes.txt 제거' })).toBeVisible()
  // Still there once the conversation has settled.
  await page.waitForTimeout(500)
  await expect(page.getByRole('button', { name: 'notes.txt 제거' })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('an empty file or a program is turned away before it is uploaded, with the formats that work', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  let uploads = 0
  await page.route('**/api/files', async (route) => {
    uploads += 1
    await route.fallback()
  })
  await page.goto('/')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  const picker = page.getByLabel('파일 선택')
  await expect(picker).toHaveAttribute('accept', /\.hwpx/)
  await picker.setInputFiles([
    { name: '빈파일.txt', mimeType: 'text/plain', buffer: Buffer.alloc(0) },
    { name: '악성파일.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') },
  ])
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('빈파일.txt — 빈 파일입니다')
  await expect(alert).toContainText('악성파일.exe — 읽을 수 없는 형식입니다. PDF·한글')
  expect(uploads).toBe(0)
  expect(unexpected).toEqual([])
})

test('a request for a text message and a board notice is answered in chat, not made a report', async ({ page }, info) => {
  const { unexpected, sends, created } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('첨부한 공문 보고 학생들에게 보낼 안내문(문자 메시지 1개, 학과 홈페이지 공지 1개)을 써 주세요')
  await input.press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(created.map((row) => row.kind)).toEqual(['chat'])
  // A document order with no short channel still goes to the report.
  await page.goto('/')
  await input.fill('총장님께 보내는 협조 공문 작성해 주세요')
  await input.press('Enter')
  await expect.poll(() => created.length).toBe(2)
  expect(created[1].kind).toBe('report')
  expect(unexpected).toEqual([])
})

test('Enter twice on home sends one question and says the first is on its way', async ({ page }, info) => {
  const { unexpected, sends, created } = await composerFixture(page, info)
  const create = gate()
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() === 'POST') await create.opened
    await route.fallback()
  })
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('한 번만 보내져야 합니다')
  await input.press('Enter')
  await input.press('Enter')
  await expect(page.getByText('앞 질문을 보내는 중입니다.')).toBeVisible()
  create.open()
  await expect.poll(() => sends.length).toBe(1)
  await page.waitForTimeout(500)
  expect(sends).toHaveLength(1)
  expect(created).toHaveLength(1)
  expect(unexpected).toEqual([])
})

test('an Enter that ends a Korean composition (keyCode 229) does not send', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('안녕하세요')
  await input.evaluate((el) => {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    Object.defineProperty(event, 'keyCode', { get: () => 229 })
    el.dispatchEvent(event)
  })
  await page.waitForTimeout(300)
  expect(sends).toHaveLength(0)
  await expect(input).toHaveValue('안녕하세요')
  expect(unexpected).toEqual([])
})

test('removing an attachment by keyboard moves focus to the next chip, then the box', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  let n = 0
  await page.route('**/api/files', async (route) => {
    n += 1
    await route.fulfill({ status: 201, json: {
      id: `f${String(90 + n).padStart(32, '0')}`, name: `메모${n}.txt`, size: 12, mime: 'text/plain', tokens: 4,
      projectId: null, sessionId: null, preview: '', error: null, createdAt: '2026-10-09T07:00:00.000Z' } })
  })
  await page.goto('/')
  const picker = page.getByLabel('파일 선택')
  await picker.setInputFiles([{ name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('a') }])
  await picker.setInputFiles([{ name: 'b.txt', mimeType: 'text/plain', buffer: Buffer.from('b') }])
  const first = page.getByRole('button', { name: '메모1.txt 제거' })
  await first.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: '메모2.txt 제거' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('프롬프트 입력')).toBeFocused()
  expect(unexpected).toEqual([])
})

test('a long paste folds into a chip that goes with the message, opens, or becomes a file', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  const paste = (text: string) => input.evaluate((el, value) => {
    const data = new DataTransfer()
    data.setData('text/plain', value)
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
  }, text)
  const long = '가'.repeat(3000)
  await paste(long)
  await expect(page.getByText('붙여 넣은 텍스트 · 3,000자')).toBeVisible()
  await expect(input).toHaveValue('')
  // 펼치기 puts it in the box.
  await page.getByRole('button', { name: '펼치기' }).click()
  await expect(input).toHaveValue(long)
  await input.fill('요약해 주세요')
  await paste(long)
  await input.press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.content).toBe(`요약해 주세요\n\n${long}`)
  expect(unexpected).toEqual([])
})

test('the counter shows near the limit and a message over it is not sent', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('나'.repeat(200_001))
  await expect(page.getByText('200,001 / 200,000자', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '전송' })).toBeDisabled()
  await input.press('Enter')
  await expect(page.getByRole('alert')).toContainText('질문은 200,000자까지 보낼 수 있습니다.')
  expect(sends).toHaveLength(0)
  expect(unexpected).toEqual([])
})

test('words inside pasted material are not the request: a mail reply stays in chat', async ({ page }, info) => {
  const { unexpected, sends, created } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill([
    '아래 메일에 정중하게 회신 써 줘',
    '---받은 메일---',
    '안녕하세요. 공동 연구 중간 점검 회의 관련입니다. 발표 자료를 11월 6일까지 보내 주시면 검토해 드리겠습니다.',
  ].join('\n'))
  await input.press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(created.map((row) => row.kind)).toEqual(['chat'])
  expect(unexpected).toEqual([])
})

test('「코드로 계산한 값」 in a deck request is not a request for code', async ({ page }, info) => {
  const { created } = await composerFixture(page, info)
  const models = await modelsServed(page)
  await page.goto('/')
  await expect.poll(models).toBe(true)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('강의용 슬라이드 만들어 줘. 대기시간은 코드로 계산한 값을 그대로 써 줘')
  await input.press('Enter')
  await expect.poll(() => created.length).toBe(1)
  expect(created[0].kind).toBe('slides')
})

test('a document asked for in a conversation takes the conversation with it', async ({ page }, info) => {
  const { sends } = await composerFixture(page, info)
  const handed: string[] = []
  const reportId = 'r0000000000000000000000000000001'
  const transcript = { id: 'f0000000000000000000000000000077', name: '대화 기록.txt', size: 40, mime: 'text/plain',
    tokens: 10, projectId: null, sessionId: reportId, preview: '', error: null, createdAt: '2026-10-09T07:00:00.000Z' }
  await page.route('**/api/sessions/*/handoff', async (route) => {
    handed.push(route.request().url())
    await route.fulfill({ status: 201, json: { session: {
      id: reportId, kind: 'report', title: '새 대화', model: 'fixture/model', routingMode: 'manual', projectId: null,
      agentId: null, artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false,
      messageCount: 0, createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z',
      messages: [], preview: null, made: null }, attachments: [transcript] } })
  })
  await page.goto(`/s/${chatId}`)
  const input = page.getByLabel('프롬프트 입력')
  await expect(input).toBeVisible()
  await input.fill('위 내용으로 결과 보고서 작성해 줘')
  await input.press('Enter')
  await expect.poll(() => handed.length).toBe(1)
  expect(handed[0]).toContain(`/sessions/${chatId}/handoff`)
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].path).toBe(`/sessions/${reportId}/messages`)
  expect(sends[0].body.attachments).toEqual([transcript.id])
})

test('an approved outline card is spent: it does not stay live to write the document again', async ({ page }, info) => {
  const { sends } = await composerFixture(page, info)
  const pending = {
    stage: 'outline', request: '신입생 OT 결과 보고서', attachments: [], answers: {},
    plan: { title: '신입생 OT 결과 보고서', sections: ['개요', '결과', '다음 단계'] },
  }
  const reportId = 'c0000000000000000000000000000009'
  const row = {
    id: reportId, kind: 'report', title: '신입생 OT 결과 보고서', model: 'fixture/model', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pending, skillIds: [], renderTemplateId: null, pinned: false,
    messageCount: 1, createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z', preview: null,
    made: null, messages: [{ id: 'm0000000000000000000000000000091', role: 'user', content: '신입생 OT 결과 보고서',
      attachments: [], createdAt: '2026-10-09T07:00:00.000Z' }],
  }
  const answered = gate()
  await page.route(`**/api/sessions/${reportId}**`, async (route) => {
    const request = route.request()
    if (request.method() === 'POST' && request.url().endsWith('/messages')) {
      sends.push({ path: `/sessions/${reportId}/messages`, body: request.postDataJSON() })
      await answered.opened
      return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done"}\n\n' })
    }
    if (request.method() === 'GET' && !request.url().includes('/jobs')) return route.fulfill({ json: row })
    await route.fallback()
  })
  await page.goto(`/s/${reportId}`)
  const approve = page.getByRole('button', { name: /이대로 생성/ })
  await expect(approve).toBeVisible()
  await approve.click()
  await expect.poll(() => sends.length).toBe(1)
  // While the document is being written the spent card offers nothing to press again.
  await expect(page.getByRole('button', { name: /이대로 생성/ })).toHaveCount(0)
  answered.open()
})
