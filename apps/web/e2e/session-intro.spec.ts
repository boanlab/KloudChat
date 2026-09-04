import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** An empty conversation names the agent, project or 서식 it starts with. No turn is sent. */

test('에이전트로 시작하면 그 에이전트가 화면의 주어가 된다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/agents')
  if ((await page.getByRole('button', { name: '실행' }).count()) === 0) {
    await page.getByRole('tab', { name: /스토어/ }).click()
  }
  const run = page.getByRole('button', { name: '실행' }).first()
  await expect(run).toBeVisible({ timeout: 30_000 })
  await run.click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // Wait for the conversation to draw: until then the only `h1` is the agent list's.
  await expect(page.getByText('이 에이전트의 지시대로 답합니다', { exact: false })).toBeVisible({
    timeout: 20_000,
  })

  // The heading and the top bar agree on the name.
  const heading = page.getByRole('heading', { level: 1 })
  await expect(heading).toBeVisible({ timeout: 20_000 })
  const name = ((await heading.textContent()) ?? '').trim()
  expect(name).not.toContain('안녕하세요')
  await expect(page.locator('header, [class*="TopBar"]').getByText(name).first()).toBeVisible()
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
  // Named, and said what it will do.
  await expect(page.getByText(title, { exact: false }).first()).toBeVisible()
  await expect(page.getByText('지침과 자료를 함께 씁니다', { exact: false })).toBeVisible()
})
