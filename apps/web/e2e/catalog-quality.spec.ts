import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('보고서 시작점은 실제 조사 절차와 필요한 자료를 공개한다', async ({ page }) => {
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: /양식 (docx|pptx)/i })).toHaveCount(0)
  await dialog.getByLabel('시작점 검색').fill('문헌 동향')

  const card = dialog.locator('.grid > *').filter({ hasText: '문헌 동향 조사' })
  await expect(card).toContainText(
    '검색 방법과 선정 기준을 밝히고 주요 쟁점과 연구 공백을 정리합니다',
  )
  await expect(card).toContainText('연구 질문')
  await expect(card).toContainText('포함·제외 기준')
  await expect(card).toContainText('검색 데이터베이스·검색어·기준일')
  await expect(card.getByText('검색 한계와 후속 연구 질문을 명시한다.')).toBeHidden()
  await card.getByText('실제 작업 방식 보기').click()
  await expect(card.getByText('검색 한계와 후속 연구 질문을 명시한다.')).toBeVisible()
})

test('발표 시작점은 시각 서식과 별개인 발표 절차를 제공한다', async ({ page }) => {
  await page.goto('/new/slides')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('의사결정 브리핑')
  await expect(dialog.locator('.grid > *').filter({ hasText: '의사결정 브리핑' })).toContainText(
    '첫 두 장 안에 결정할 사안과 권고안',
  )
})

/**
 * 이미지에서는 서식이 곧 시작점이다.
 *
 * On 챗 · 보고서 · 슬라이드 a 시작점 is a job and a 서식 is the shape it comes
 * in. On 이미지 there is no such split — 방법 구조도, 티저 그림, 포스터 *are*
 * the jobs, and their example sentence is the prompt. The surface ships no
 * 시작점 at all, so a dialogue that only ever draws 시작점 came up empty on it:
 * 「조건에 맞는 시작점이 없습니다」 on a screen with six 서식 behind it.
 */
test('이미지에는 서식이 시작점 자리에 선다', async ({ page }) => {
  await page.goto('/new/image')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })

  await expect(dialog.getByText('조건에 맞는 시작점이 없습니다.')).toHaveCount(0)
  await expect(dialog.getByText('어떤 모양으로 받을까요?')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '이 서식으로 시작' }).first()).toBeVisible({
    timeout: 20_000,
  })
  // 논문 도판 계열이 실제로 골라진다.
  await dialog.getByLabel('서식 검색').fill('방법 구조도')
  await expect(dialog.locator('.grid > *').filter({ hasText: '방법 구조도' })).toBeVisible({
    timeout: 20_000,
  })
})
