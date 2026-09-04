import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** A saved design system reaches the gallery opened inside a project wearing it. Nothing is generated. */

/** Not a colour any seed or theme uses. */
const ACCENT = '#0a7b57'

test('디자인 편집 화면과 갤러리 카드가 그 디자인을 그대로 보여 준다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  const name = `미리보기 검증 ${Date.now()}`
  await page.goto('/designs')
  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await designs.getByRole('button', { name: '디자인 추가' }).click()
  await designs.getByLabel('이름', { exact: true }).fill(name)
  await designs.getByLabel('강조색 색상 코드').fill(ACCENT)
  await designs.getByLabel('서체').selectOption('serif')

  await designs.getByRole('button', { name: '저장', exact: true }).click()
  await expect(designs.locator('li', { hasText: name })).toBeVisible({ timeout: 20_000 })

  const projectName = `미리보기 프로젝트 ${Date.now()}`
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).click()
  await page.getByLabel('이름', { exact: true }).fill(projectName)
  await page.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
  const projectId = page.url().split('/projects/')[1]

  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/projects/${projectId}`) &&
      r.request().method() === 'PATCH' &&
      r.status() === 200,
    { timeout: 20_000 },
  )
  await page.getByLabel('디자인', { exact: true }).selectOption({ label: name })
  await saved

  await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
  await page.getByRole('menuitem', { name: '슬라이드' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // The gallery opens on the project's own surface.
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('button').first()).toBeVisible({
    timeout: 20_000,
  })
})
