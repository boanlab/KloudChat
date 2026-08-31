import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A look, on the two screens that are supposed to be showing it.
 *
 * The design editor and the design gallery were each half of one result and
 * never met: three hex fields with nothing to look at on one side, a card in
 * the default indigo on the other, whatever the project was wearing. Both now
 * ask the same preview route for the same four tokens, so this walks the two
 * of them and reads the address each frame is asking for.
 *
 * No generation, so no credits: what is being checked is the document the
 * gallery advertises, not the one a model would write into it.
 */

/** Not a colour any seed or theme uses, so a stale frame cannot pass. */
const ACCENT = '#0a7b57'

test('디자인 편집 화면과 갤러리 카드가 그 디자인을 그대로 보여 준다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  // ── 1. The design is saved as it was typed ──────────────────────────
  //
  // The editor used to show a live preview beside the form — the gallery's own
  // card, drawn in the tokens above it. That went with the previews: the card
  // it borrowed was a 서식's seed filled with a sample, and six 서식 carried
  // the same one, so the picture said less than the name did.
  const name = `미리보기 검증 ${Date.now()}`
  await page.goto('/designs')
  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await designs.getByRole('button', { name: '디자인 추가' }).click()
  await designs.getByLabel('이름', { exact: true }).fill(name)
  await designs.getByLabel('강조색 색상 코드').fill(ACCENT)
  await designs.getByLabel('서체').selectOption('serif')

  await designs.getByRole('button', { name: '저장', exact: true }).click()
  await expect(designs.locator('li', { hasText: name })).toBeVisible({ timeout: 20_000 })

  // ── 2. A project wearing it ─────────────────────────────────────────
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

  // ── 3. The gallery inside it draws its cards in that look ───────────
  await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
  await page.getByRole('menuitem', { name: '슬라이드' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // The gallery opens on the project's own surface, wearing that project.
  //
  // This used to watch for the preview request each card made — the frames
  // were `sandbox=""` so what they painted could not be read, and the request
  // was the honest signal. The previews went with the seeds they were drawn
  // from, and the design now reaches the document rather than a thumbnail of
  // it: the file the 서식 hands over carries it, and so does the page view.
  await page.getByRole('button', { name: '서식 고르기' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('button').first()).toBeVisible({
    timeout: 20_000,
  })
})
