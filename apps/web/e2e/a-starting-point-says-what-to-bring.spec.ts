import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** A 시작점 card says what to bring; each blank is a field with an example, and what is filled in becomes the request. */
test('고르면 입력창이 묻고, 빈칸마다 예시가 있고, 채운 것이 요청이 된다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('문헌 동향')
  const card = dialog.locator('div.group', { hasText: '문헌 동향 조사' }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })

  // The card lists its questions and outputs; no blanks on it.
  await expect(card.getByText('연구 질문', { exact: true })).toBeVisible()
  await expect(card.getByText('웹 검색으로 찾습니다')).toBeVisible()
  await expect(card.getByText('인용 형식 맞추기')).toBeVisible()
  await expect(card.getByRole('textbox')).toHaveCount(0)
  await card.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(dialog).toBeHidden()

  // Questions above the composer, each with an example.
  const questions = page.getByRole('group', { name: '문헌 동향 조사 시작점 질문' })
  await expect(questions).toBeVisible()
  const question = questions.getByLabel('문헌 동향 조사 · 연구 질문')
  await expect(question).toHaveAttribute('placeholder', /^예: /)
  await expect(questions.getByLabel('문헌 동향 조사 · 기간·언어')).toHaveAttribute('placeholder', /2020/)
  await expect(page.getByRole('button', { name: /웹 검색/ }).first()).toHaveAttribute('aria-pressed', 'true')

  // What is filled in becomes the request.
  await question.fill('LLM 기반 코드 리뷰의 효과')
  await questions.getByLabel('문헌 동향 조사 · 기간·언어').fill('2021~2025, 영어')
  let sent = ''
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    sent = (route.request().postDataJSON() as { content: string }).content
    await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: 'data: {"type":"done"}\n\n' })
  })
  await page.getByRole('button', { name: '전송' }).click()
  await expect.poll(() => sent, { timeout: 30_000 }).toMatch(/문헌 동향 조사\n연구 질문: LLM 기반 코드 리뷰의 효과\n기간·언어: 2021~2025, 영어/)
})

test('파일이 있어야 하는 일은 파일을 달라고 한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/chat')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('전공 원문')
  const card = dialog.locator('div.group', { hasText: '전공 원문 읽기' }).first()
  await expect(card.getByText('파일을 첨부해야 합니다')).toBeVisible({ timeout: 20_000 })
  await card.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(page.getByRole('button', { name: /이 일에는 파일이 필요합니다/ })).toBeVisible()
})

/** The method-figure image 서식 takes a paragraph and draws a labelled diagram. */
test('방법 구조도는 설명 문단을 받고 이름표가 있는 그림을 낸다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  await page.goto('/new/image')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('서식 검색').fill('방법 구조도')
  const card = dialog.locator('div.group', { hasText: '방법 구조도' }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card.getByText('방법 설명')).toBeVisible()
  await expect(card.getByRole('textbox')).toHaveCount(0)
  await card.getByRole('button', { name: '이 도식으로 시작' }).click()
  await expect(dialog).toBeHidden()

  // The description is a textarea; emphasis and language are selects.
  const questions = page.getByRole('group', { name: '방법 구조도 시작점 질문' })
  const description = questions.getByLabel('방법 구조도 · 방법 설명')
  await expect(description).toHaveJSProperty('tagName', 'TEXTAREA')
  await expect(description).toHaveAttribute('placeholder', /인코더/)
  await expect(questions.getByLabel('방법 구조도 · 강조할 것')).toHaveJSProperty('tagName', 'SELECT')

  // Left empty, the example becomes the sentence.
  const drawn = page.waitForResponse((r) => r.url().includes('/diagrams/store') && r.request().method() === 'POST', { timeout: 240_000 })
  await page.getByRole('button', { name: '전송' }).click()
  const stored = await (await drawn).json() as { data: { source: string; caption: string } }
  expect(stored.data.source).toMatch(/flowchart|graph/)
  expect(stored.data.source).toMatch(/인코더|검색|디코더|검증/)
  await expect(page.getByRole('button', { name: '도식 소스(mermaid) 복사' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/^그림\. /)).toBeVisible()
})
