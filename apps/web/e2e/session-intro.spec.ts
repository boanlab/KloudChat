import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * What an empty conversation says about itself.
 *
 * A session can arrive already carrying an agent, a project or a 서식, each
 * chosen on a different screen. Before this, all three arrived as a badge in
 * the top bar and the middle of the screen greeted the person as though
 * nothing had been decided — pressing 실행 on an agent was the plainest case,
 * because the screen that opened was indistinguishable from a blank one.
 *
 * Nothing here sends a turn, so nothing here costs credits.
 */

test('에이전트로 시작하면 그 에이전트가 화면의 주어가 된다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/agents')
  const run = page.getByRole('button', { name: '실행' }).first()
  await expect(run).toBeVisible({ timeout: 30_000 })
  await run.click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The top bar has always carried the agent's name; the point of this change
  // is that the middle of the screen carries it too. Read it from the badge
  // rather than from the card that was clicked, so the assertion is about the
  // two agreeing rather than about the agent list's markup.
  const heading = page.getByRole('heading', { level: 1 })
  await expect(heading).toBeVisible({ timeout: 20_000 })
  const name = ((await heading.textContent()) ?? '').trim()
  expect(name).not.toContain('안녕하세요')
  await expect(page.locator('header, [class*="TopBar"]').getByText(name).first()).toBeVisible()
  await expect(page.getByText('이 에이전트의 지시대로 답합니다', { exact: false })).toBeVisible()

  // And the surface's generic openings are gone: an agent is a stance somebody
  // chose, and "이번 주 회의록 정리해줘" under it is the product talking over
  // them.
  await expect(page.getByRole('group', { name: '이렇게 시작해 보세요' })).toHaveCount(0)
})

test('프로젝트 안에서 시작하면 무엇을 가지고 시작하는지 적혀 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/projects')
  const open = page.getByRole('link', { name: /./ }).filter({ hasNotText: '새 프로젝트' })
  test.skip((await open.count()) === 0, '이 계정에는 프로젝트가 없습니다')

  await page.goto('/projects')
  await page.locator('a[href^="/projects/"]').first().click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
  const title = ((await page.getByRole('heading', { level: 1 }).textContent()) ?? '').trim()

  await page.getByRole('button', { name: /새 작업|새 대화|챗/ }).first().click()
  await expect(page).toHaveURL(/\/s\/|\/new\//, { timeout: 20_000 })

  const carries = page.getByText('이 대화가 가지고 시작하는 것')
  await expect(carries).toBeVisible({ timeout: 20_000 })
  // Named, and said what it will do — not just labelled with its category.
  await expect(page.getByText(title, { exact: false }).first()).toBeVisible()
  await expect(page.getByText('지침과 자료를 함께 씁니다', { exact: false })).toBeVisible()
})
