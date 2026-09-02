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
  // 결과 서식 탭은 없어졌다 — 업무 시작점으로 접혔다. What is left is one
  // list of jobs, and each card asks its own questions on the card.
  await page.goto('/new/chat')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: '작업 시작하기' })).toBeVisible()
  await expect(dialog.getByRole('tab')).toHaveCount(0)
  await dialog.getByLabel('시작점 검색').fill('장애 원인')
  const card = dialog.locator('div.group', { hasText: '장애 원인 좁히기' }).first()
  // 빈칸이 카드 위에 있다.
  await expect(card.getByLabel('장애 원인 좁히기 · 에러 로그')).toBeVisible()
  await card.getByLabel('장애 원인 좁히기 · 재현 조건').fill('로그인 직후 새로고침')
  await card.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toBeVisible()
  // 채운 것이 요청으로 들어 있다.
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/장애 원인 좁히기\n재현 조건: 로그인 직후 새로고침/)
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
  // 결과 모양은 카드의 고르개다 — 열일곱 서식이 전부 여기서 닿는다.
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
