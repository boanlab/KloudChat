import { expect, test } from '@playwright/test'
import { hubFixture, mySkills } from './customize-fixture'

/**
 * A skill kept on in an agent that none of the agent's surfaces run is said so next to the
 * picker, with the way out (add the surface or remove the skill), instead of being dropped
 * without a word at run time.
 */

test.use({ serviceWorkers: 'block' })

const reportOnly = { ...mySkills[1], id: 'k7777777777777777777777777777777', name: '개조식 보고', kinds: ['report'] }

test('a kept-on skill outside the agent surfaces is named with why, and clears when the surface is added', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [...mySkills, reportOnly] }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })

  await page.getByText('스킬과 도구', { exact: true }).click()
  await expect(page.getByTestId('skill-surface-warning')).toHaveCount(0)
  await page.getByTestId('active-skill-picker').getByRole('button', { name: '켜 둘 스킬 고르기' }).click()
  await page.getByRole('dialog', { name: '켜 둘 스킬 고르기' }).getByRole('checkbox', { name: /개조식 보고/ }).check()
  await page.keyboard.press('Escape')

  // a new agent runs on chat only: the report-only skill would never apply
  const warning = page.getByTestId('skill-surface-warning')
  await expect(warning).toHaveText(
    '「개조식 보고」 스킬은 보고서 화면에서만 쓰여 이 에이전트의 화면(챗)에서는 적용되지 않습니다. 고급 › 사용할 화면에서 보고서 화면을 더하거나 스킬을 빼세요.',
  )

  await page.getByText('고급', { exact: true }).click()
  await form.getByRole('button', { name: '보고서', exact: true }).click()
  await expect(warning).toHaveCount(0)
  expect(state.problems).toEqual([])
})
