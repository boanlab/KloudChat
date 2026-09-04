import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** 원문 편집 round-trips the whole report as Markdown: title, headings and body. */
test('보고서를 문서 단위로 고치면 제목·절 제목·본문이 함께 반영된다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  await page.getByRole('button', { name: '원문 편집' }).click()
  const source = page.getByLabel('문서 원본')
  const preview = page.getByLabel('문서 미리보기')
  await expect(source).toBeVisible()
  await expect(preview).toBeVisible()

  // The whole document, title included.
  await expect(source).toHaveValue(/^# /)

  const title = `문서편집 확인 ${Date.now()}`
  await source.fill(
    `# ${title}\n\n## 바뀐 절 제목\n\n첫 문단이다.\n\n### 새 소제목\n\n5. 다섯째\n1. 여섯째\n\n## 새로 추가한 절\n\n추가한 본문이다.\n`,
  )
  await expect(preview.getByRole('heading', { name: '바뀐 절 제목' })).toBeVisible()
  await expect(preview.getByRole('heading', { name: '새 소제목' })).toBeVisible()
  // Numbering follows the source.
  expect(await preview.locator('ol').first().getAttribute('start')).toBe('5')

  await page.getByRole('button', { name: '저장', exact: true }).last().click()
  await expect(source).toBeHidden({ timeout: 20_000 })

  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await expect(page.getByRole('heading', { name: '바뀐 절 제목' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '새로 추가한 절' })).toBeVisible()

  // Survives a reload.
  await page.reload()
  await expect(page.getByRole('heading', { name: '새로 추가한 절' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toHaveCount(0)
})
