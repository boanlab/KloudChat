import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * How far a project's design system says it reaches, against how far it goes.
 *
 * `design-system.spec.ts` proves the look comes out of the deck and the export.
 * This is the other half and costs no model call: the two screens where a
 * person decides to use one at all have to name the same surfaces the server
 * folds it into — chat among them, which is the surface nobody expects and the
 * one where a design edited this afternoon changes an answer this evening.
 */
test('디자인이 닿는 곳을 프로젝트 화면과 빈 대화 화면이 똑같이 말한다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  const name = `말투 검증 ${Date.now()}`
  await page.goto('/designs')
  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await designs.getByRole('button', { name: '디자인 추가' }).click()
  await designs.getByLabel('이름', { exact: true }).fill(name)
  await designs.getByLabel('한 줄 설명').fill('짧게, 명사구로')
  await designs.getByLabel(/문체 규율/).fill('제목은 명사구로 쓴다.')
  await designs.getByRole('button', { name: '저장', exact: true }).click()
  await expect(designs.locator('li', { hasText: name })).toBeVisible({ timeout: 20_000 })

  const projectName = `도달 범위 ${Date.now()}`
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).click()
  await page.getByLabel('이름', { exact: true }).fill(projectName)
  await page.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
  const projectId = page.url().split('/projects/')[1]

  // The card's own sentence, before the picker is touched: it is what somebody
  // reads to decide, so 대화 has to be in it whether or not one is attached.
  await expect(page.getByText(/말투는 대화·보고서·슬라이드에/)).toBeVisible({ timeout: 20_000 })

  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/projects/${projectId}`) &&
      r.request().method() === 'PATCH' &&
      r.status() === 200,
    { timeout: 20_000 },
  )
  await page.getByLabel('디자인', { exact: true }).selectOption({ label: name })
  await saved

  // Each surface is told in its own terms, so a wording shared across all four
  // would pass one of these and fail the next.
  const panel = page.getByText('이 대화가 가지고 시작하는 것')
  for (const [surface, says] of [
    ['챗', '이 대화의 말투를 이 디자인에 맞춥니다'],
    ['슬라이드', '슬라이드의 말투와 색, 서체를 이 디자인에 맞춥니다'],
    ['이미지', '그림의 색과 스타일을 이 디자인에 맞춥니다'],
  ] as const) {
    await page.goto(`/projects/${projectId}`)
    await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
    const entry = page.getByRole('menuitem', { name: surface, exact: true })
    // A surface an administrator has switched off has no entry in this menu —
    // `image` and `av` are off unless somebody turns them on, because they
    // spend credits per generation. Asserting what such a surface says about a
    // design is asserting about a screen nobody in this workspace can reach.
    if ((await entry.count()) === 0 || !(await entry.isEnabled().catch(() => false))) {
      await page.keyboard.press('Escape')
      continue
    }
    await entry.click()
    await expect(page, `${surface} 화면으로 넘어가지 않았다`).toHaveURL(/\/s\/[0-9a-f]{32}/, {
      timeout: 60_000,
    })
    await expect(panel).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(says)).toBeVisible({ timeout: 20_000 })
  }

  // And nothing at all where it does not reach: the renderer for a clip never
  // sees the design, so a row here would be the same lie in the other
  // direction.
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
  const clips = page.getByRole('menuitem', { name: '오디오/동영상', exact: true })
  if ((await clips.count()) > 0 && (await clips.isEnabled().catch(() => false))) {
    await clips.click()
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
    await expect(panel).toBeVisible({ timeout: 20_000 })
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  }
})
