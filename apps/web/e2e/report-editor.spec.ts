import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The report is edited as one document, not section by section. That is what
 * makes the title, the section headings and the space between sections
 * reachable at all — the per-section editor could only ever reach prose.
 */
test('보고서를 문서 단위로 고치면 제목·절 제목·본문이 함께 반영된다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.goto('/artifacts')
  // Filtered, not "whichever card is first": once decks existed they sorted
  // ahead of the reports and this opened a slides session, where there is no
  // document editor to click.
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // 원문 편집기의 저장은 화면의 마지막 저장이다 — 페이지뷰의 저장과 이름이
  // 겹치므로 위치로 가른다.
  // 수정 버튼은 이제 페이지 편집기(ProseMirror)로 간다 — 표를 고치려던
  // 사람이 `| --- |` 를 마주하지 않게 한 변경. 마크다운 원문은 제 이름을
  // 단 '원문 편집' 으로 옮겨 갔고, 이 스펙이 검증하는 것은 그 원문 왕복이다.
  await page.getByRole('button', { name: '원문 편집' }).click()
  const source = page.getByLabel('문서 원본')
  const preview = page.getByLabel('문서 미리보기')
  await expect(source).toBeVisible()
  await expect(preview).toBeVisible()

  // The whole document arrives as Markdown — title included.
  await expect(source).toHaveValue(/^# /)

  const title = `문서편집 확인 ${Date.now()}`
  await source.fill(
    `# ${title}\n\n## 바뀐 절 제목\n\n첫 문단이다.\n\n### 새 소제목\n\n5. 다섯째\n1. 여섯째\n\n## 새로 추가한 절\n\n추가한 본문이다.\n`,
  )
  await expect(preview.getByRole('heading', { name: '바뀐 절 제목' })).toBeVisible()
  await expect(preview.getByRole('heading', { name: '새 소제목' })).toBeVisible()
  // Numbering follows the source, the same way the exporters write it.
  expect(await preview.locator('ol').first().getAttribute('start')).toBe('5')

  await page.getByRole('button', { name: '저장', exact: true }).last().click()
  await expect(source).toBeHidden({ timeout: 20_000 })

  // Rendered document reflects every level, not just the prose.
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  await expect(page.getByRole('heading', { name: '바뀐 절 제목' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '새로 추가한 절' })).toBeVisible()

  // And it survives a reload, i.e. the server has it.
  await page.reload()
  await expect(page.getByRole('heading', { name: '새로 추가한 절' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toHaveCount(0)
})
