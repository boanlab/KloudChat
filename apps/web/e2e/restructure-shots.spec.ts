import { expect, test, type Page } from '@playwright/test'
import { hubFixture, myAgents } from './customize-fixture'
import { openTool } from './composer-tools'
import { viewport } from './project'

/** Screenshots of the agent-centric screens for review; runs only with RESTRUCTURE_SHOTS set. */

const dir = process.env.RESTRUCTURE_SHOTS ?? ''
test.skip(!dir, 'RESTRUCTURE_SHOTS is not set')
test.use({ serviceWorkers: 'block' })

async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(350)
}

test('screens', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  await hubFixture(page, testInfo)
  const tag = viewport(testInfo)
  const shot = async (name: string, full = true) => {
    await settle(page)
    await page.screenshot({ path: `${dir}/${tag}-${name}.png`, fullPage: full })
  }

  await page.goto('/customize?tab=agents&view=mine')
  await expect(page.getByTestId('item-card').first()).toBeVisible({ timeout: 15_000 })
  await shot('hub-agents', false)

  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('주간 보고 요약')
  await shot('create-direct')
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  await expect(page.getByTestId('agent-bundles')).toBeVisible()
  await shot('create-base')
  const review = page.getByTestId('bundle-row').filter({ hasText: '다 쓴 뒤 검토하기' })
  await review.getByRole('button', { name: '자세히' }).click()
  await review.getByRole('checkbox', { name: '형식 검사' }).scrollIntoViewIfNeeded()
  await shot('create-base-locked', false)
  await form.getByRole('radio', { name: /직접 구성/ }).check()
  await expect(page.getByTestId('process-custom')).toBeVisible()
  await shot('create-custom')

  await page.goto(`/customize/agents/${myAgents[0].id}`)
  await expect(page.getByTestId('process-chain')).toBeVisible({ timeout: 15_000 })
  await shot('agent-detail', false)

  await page.goto('/customize/bundles')
  await expect(page.getByTestId('bundle-card').first()).toBeVisible({ timeout: 15_000 })
  await shot('bundles')

  await page.goto('/settings/memory')
  await expect(page.getByRole('button', { name: '새 메모리' })).toBeVisible({ timeout: 15_000 })
  await shot('settings-memory', false)

  await page.goto('/new/report')
  await expect(page.getByRole('button', { name: '도구', exact: true })).toBeVisible({ timeout: 15_000 })
  await openTool(page, '템플릿 고르기')
  await expect(page.getByRole('dialog', { name: '템플릿 고르기' })).toBeVisible({ timeout: 15_000 })
  await shot('composer-templates', false)

  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  await expect(page.getByTestId('draft-notice')).toBeVisible({ timeout: 15_000 })
  await shot('save-as-agent')
})
