import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

const STARTING_POINTS = [
  {
    id: 't_e2e_debug',
    kind: 'chat',
    group: '개발',
    title: '장애 원인 좁히기',
    description: '스택 트레이스에서 가설과 확인 방법까지',
    fills: ['에러 로그', '재현 조건'],
    prompt: '이 에러의 원인을 좁혀 줘.\n\n에러: ',
  },
  {
    id: 't_e2e_report',
    kind: 'report',
    group: '연구',
    title: '문헌 동향 조사',
    description: '핵심 논점과 연구 공백을 정리한다',
    fills: ['연구 주제', '조사 기간'],
    prompt: '문헌 동향을 조사해 줘.\n\n주제: ',
  },
]

test.beforeEach(async ({ page }) => {
  await signIn(page)
  await page.route('**/api/prompt-templates', (route) =>
    route.fulfill({ json: STARTING_POINTS }),
  )
})

test('채팅은 시작점 한 목록이고, 카드가 무엇을 적을지 묻는다', async ({ page }) => {
  await page.goto('/new/chat')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: '작업 시작하기' })).toBeVisible()
  await expect(dialog.getByRole('tab')).toHaveCount(0)
  await dialog.getByLabel('시작점 검색').fill('장애 원인')
  const card = dialog.locator('div.group', { hasText: '장애 원인 좁히기' }).first()
  // The card lists its questions; the composer asks them.
  await expect(card.getByText('에러 로그')).toBeVisible()
  await expect(card.getByRole('textbox')).toHaveCount(0)
  await card.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toBeVisible()
  const questions = page.getByRole('group', { name: '장애 원인 좁히기 시작점 질문' })
  await expect(questions.getByLabel('장애 원인 좁히기 · 에러 로그')).toBeVisible()
  await expect(questions.getByLabel('장애 원인 좁히기 · 재현 조건')).toBeVisible()
})

test('보고서 시작점은 결과 모양을 카드에서 고른다', async ({ page }) => {
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('tab')).toHaveCount(0)
  await expect(dialog.getByLabel('시작점 검색')).toBeVisible()
  await dialog.getByLabel('시작점 검색').fill('문헌 동향')
  const card = dialog.locator('div.group', { hasText: '문헌 동향 조사' }).first()
  await expect(card).toBeVisible()
  // The result shape is picked on the card.
  await card.getByRole('button', { name: /결과 모양 고르기/ }).click()
  await expect(page.getByRole('menuitem', { name: '한 장 요약' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: '주제에 맞게 새로 만들기' })).toBeVisible()
  await page.keyboard.press('Escape')
})

test('좁은 화면에서도 선택창이 가로로 넘치지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const box = await dialog.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
})
