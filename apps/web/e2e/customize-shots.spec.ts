import { expect, test, type Page } from '@playwright/test'
import { hubFixture, myAgents, mySkills } from './customize-fixture'

/** Screenshots of the hub for review; runs only with CUSTOMIZE_SHOTS set to a directory. */

const dir = process.env.CUSTOMIZE_SHOTS ?? ''
test.skip(!dir, 'CUSTOMIZE_SHOTS is not set')
test.use({ serviceWorkers: 'block' })

async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(350)
}

test('screens', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  await hubFixture(page, testInfo, { conversations: 40 })
  const phone = testInfo.project.name === 'phone'
  const tag = testInfo.project.name
  const shot = async (name: string, full = false) => {
    await settle(page)
    await page.screenshot({ path: `${dir}/${tag}-${name}.png`, fullPage: full })
  }

  for (const tab of ['agents', 'skills']) {
    await page.goto(`/customize?tab=${tab}&view=explore`)
    await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
    await shot(`explore-${tab}`)
  }
  await page.goto('/customize?tab=connectors')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  await shot('tab-connectors')
  await page.goto('/customize?tab=agents&view=mine')
  await expect(page.getByTestId('item-card').first()).toBeVisible({ timeout: 15_000 })
  await shot('mine-agents')
  await page.goto('/customize?tab=skills&view=mine')
  await expect(page.getByTestId('item-card').first()).toBeVisible({ timeout: 15_000 })
  await shot('mine-skills')
  await page.goto('/customize/bundles')
  await expect(page.getByTestId('bundle-card').first()).toBeVisible({ timeout: 15_000 })
  await shot('bundles')

  await page.goto('/customize?tab=skills&view=explore')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '추가', exact: true }).filter({ visible: true }).first().click()
  await expect(page.getByRole('menu')).toBeVisible()
  await shot('add-menu')
  await page.keyboard.press('Escape')

  await page.goto('/customize/skills/new')
  await expect(page.getByRole('heading', { level: 1, name: '새 스킬' })).toBeVisible({ timeout: 15_000 })
  await shot('skill-create')

  await page.goto('/customize/agents/new')
  await expect(page.getByRole('heading', { level: 1, name: '새 에이전트' })).toBeVisible({ timeout: 15_000 })
  await shot('agent-create-closed', true)
  for (const summary of await page.getByTestId('agent-advanced').locator('summary').all()) await summary.click()
  await shot('agent-create-open', true)
  await page.getByRole('button', { name: '직접 고르기' }).first().click()
  await page.getByRole('button', { name: '도구 고르기' }).click()
  await page.getByRole('dialog', { name: '도구 고르기' }).scrollIntoViewIfNeeded()
  await shot('tool-picker')

  await page.goto(`/customize/agents/${myAgents[0].id}`)
  await expect(page.getByRole('heading', { level: 1, name: myAgents[0].name })).toBeVisible({ timeout: 15_000 })
  await shot('agent-detail')
  await page.goto(`/customize/skills/${mySkills[1].id}`)
  await expect(page.getByRole('heading', { level: 1, name: mySkills[1].name }).first()).toBeVisible({ timeout: 15_000 })
  await shot('skill-detail')

  await page.goto('/customize')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  if (phone) {
    await page.getByRole('button', { name: '사이드바 토글' }).click()
    await page.waitForTimeout(400)
    await shot('sidebar')
  } else {
    await shot('sidebar')
  }
  await page.getByRole('button', { name: /^계정 메뉴/ }).filter({ visible: true }).first().click()
  await expect(page.getByRole('menu')).toBeVisible()
  await shot('account-menu')
})

test('dark', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop')
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => localStorage.setItem('kchat-theme', 'dark'))
  await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=explore')
  await expect(page.getByRole('region', { name: '새 에이전트', exact: true })).toBeVisible({ timeout: 15_000 })
  await settle(page)
  await page.screenshot({ path: `${dir}/dark-explore-agents.png` })
})
