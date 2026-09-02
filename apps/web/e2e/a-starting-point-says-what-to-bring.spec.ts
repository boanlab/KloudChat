import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * 시작점은 무엇을 적어야 하는지, 무엇으로 되는지를 말한다.
 *
 * 「챗, 보고서, 슬라이드 모두 '작업 시작하기' 부분이 실제 사용자가 어떻게
 * 써야 하는지 인풋으로 뭘 줘야 하는지 명확하게 와닿지 않는다」 — the card
 * listed five nouns and handed them to a placeholder that vanished at the
 * first keystroke. Now every blank is a field with an example in it, the
 * card says up front whether the job needs web search or a file, and what
 * was filled in lands in the composer as the request, readable and editable.
 */
test('빈칸마다 예시가 있고, 채운 것이 요청이 된다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('문헌 동향')
  const card = dialog.locator('div.group', { hasText: '문헌 동향 조사' }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })

  // 예시가 빈칸 안에 적혀 있다.
  const question = card.getByLabel('문헌 동향 조사 · 연구 질문')
  await expect(question).toHaveAttribute('placeholder', /^예: /)
  await expect(card.getByLabel('문헌 동향 조사 · 기간·언어')).toHaveAttribute('placeholder', /2020/)

  // 웹 검색으로 찾는 일이라고 카드가 먼저 말한다.
  await expect(card.getByText('웹 검색으로 찾습니다')).toBeVisible()
  await expect(card.getByText('인용 형식 맞추기')).toBeVisible()

  await question.fill('LLM 기반 코드 리뷰의 효과')
  await card.getByLabel('문헌 동향 조사 · 기간·언어').fill('2021~2025, 영어')
  await card.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(dialog).toBeHidden()

  // 채운 만큼이 입력창에 요청으로 들어 있다 — 빈 칸은 빠진다.
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue(/문헌 동향 조사\n연구 질문: LLM 기반 코드 리뷰의 효과\n기간·언어: 2021~2025, 영어$/)
  // 웹 검색이 켜져 있다.
  await expect(page.getByRole('button', { name: /웹 검색/ }).first()).toHaveAttribute('aria-pressed', 'true')
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

/**
 * 논문 그림은 이름표가 있다. The image 서식 for a method figure takes a
 * paragraph describing the method, not a five-word subject and a choice of
 * "texture" — and what it draws has the method's own labels on it.
 */
test('방법 구조도는 설명 문단을 받고 이름표가 있는 그림을 낸다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  await page.goto('/new/image')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('서식 검색').fill('방법 구조도')
  const card = dialog.locator('div.group', { hasText: '방법 구조도' }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  // 한 줄이 아니라 문단으로 받는다.
  const description = card.getByLabel('방법 설명')
  await expect(description).toHaveJSProperty('tagName', 'TEXTAREA')
  await expect(description).toHaveValue(/인코더/)
  await card.getByRole('button', { name: '이 설명으로 도식 그리기' }).click()
  await expect(dialog).toBeHidden()
  // 설명이 그대로 입력창에 있고, 강조·언어가 줄로 붙었다.
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/인코더[\s\S]*강조할 것: 학습되는 부분\n이름표 언어: 한국어$/)

  const drawn = page.waitForResponse((r) => r.url().includes('/diagrams/store') && r.request().method() === 'POST', { timeout: 240_000 })
  await page.getByLabel('프롬프트 입력').press('Enter')
  const stored = await (await drawn).json() as { data: { source: string; caption: string } }
  // mermaid 이고, 설명의 용어가 이름표로 들어 있다.
  expect(stored.data.source).toMatch(/flowchart|graph/)
  expect(stored.data.source).toMatch(/인코더|검색|디코더|검증/)
  await expect(page.getByRole('button', { name: '도식 소스(mermaid) 복사' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/^그림\. /)).toBeVisible()
})
