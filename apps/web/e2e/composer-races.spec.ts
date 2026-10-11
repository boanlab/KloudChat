import { expect, test, type Page } from '@playwright/test'
import { composerFixture } from './composer-fixture'

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
