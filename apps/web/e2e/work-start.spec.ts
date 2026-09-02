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

test('채팅은 업무 시작점만 보여 주고 선택 의미를 미리 설명한다', async ({ page }) => {
  await page.goto('/new/chat')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: '작업 시작하기' })).toBeVisible()
  await expect(dialog.getByRole('tab', { name: /업무 시작점/ })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(dialog.getByRole('tab', { name: /결과 서식/ })).toHaveCount(0)
  await expect(dialog.getByText('이번 요청에만 적용 · 선택 후 해제 가능')).toBeVisible()
  await expect(dialog.getByText('에러 로그')).toBeVisible()
  await dialog.getByRole('button', { name: /장애 원인 좁히기/ }).click()
  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveAttribute(
    'placeholder',
    '에러 로그, 재현 조건을 적어 주세요',
  )
})

test('보고서는 업무와 결과 모양을 분리해 탐색하고 검색한다', async ({ page }) => {
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  const format = dialog.getByRole('tab', { name: /결과 서식/ })
  const starting = dialog.getByRole('tab', { name: /업무 시작점/ })
  await expect(format).toHaveAttribute('aria-selected', 'true')
  await expect(dialog.getByLabel('결과 서식 검색')).toBeVisible()
  await expect(dialog.getByText('문헌 동향 조사')).toHaveCount(0)

  await starting.click()
  await expect(starting).toHaveAttribute('aria-selected', 'true')
  await expect(dialog.getByLabel('시작점 검색')).toBeVisible()
  await expect(dialog.getByText('문헌 동향 조사')).toBeVisible()
  await expect(dialog.getByText('미리보기와 점검 항목을 확인한 뒤 고르세요.')).toHaveCount(0)
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
