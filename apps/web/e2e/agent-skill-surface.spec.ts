import { expect, test } from '@playwright/test'
import { hubFixture, mySkills, storeSkills } from './customize-fixture'

/**
 * A skill kept on in an agent that none of the agent's surfaces run is said so next to the
 * picker, with the way out (add the surface or remove the skill), instead of being dropped
 * without a word at run time.
 */

test.use({ serviceWorkers: 'block' })

const reportOnly = { ...mySkills[1], id: 'k7777777777777777777777777777777', name: '개조식 보고', kinds: ['report'] }

const pickSkill = async (page: import('@playwright/test').Page, name: RegExp) => {
  await page.getByTestId('active-skill-picker').getByRole('button', { name: '켜 둘 스킬 고르기' }).click()
  await page.getByRole('dialog', { name: '켜 둘 스킬 고르기' }).getByRole('checkbox', { name }).check()
  await page.keyboard.press('Escape')
}

test('a kept-on skill outside the agent surfaces is named with why, and clears when the surface is added', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [...mySkills, reportOnly] }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })

  // Opening on chat chosen by hand: the skill does not move it.
  await page.getByText('고급', { exact: true }).click()
  await form.getByRole('combobox', { name: '처음 여는 화면' }).selectOption('chat')
  await page.getByText('스킬과 도구', { exact: true }).click()
  await expect(page.getByTestId('skill-surface-warning')).toHaveCount(0)
  await pickSkill(page, /개조식 보고/)

  // a new agent runs on chat only: the report-only skill would never apply
  const warning = page.getByTestId('skill-surface-warning')
  await expect(warning).toHaveText(
    '「개조식 보고」 스킬은 보고서 화면에서만 쓰여 이 에이전트의 화면(챗)에서는 적용되지 않습니다. 고급 › 사용할 화면에서 보고서 화면을 더하거나 스킬을 빼세요.',
  )

  await form.getByRole('button', { name: '보고서', exact: true }).click()
  await expect(warning).toHaveCount(0)
  // Still opening on chat: said, with the way out.
  await expect(page.getByTestId('skill-start-note')).toHaveText(
    '「개조식 보고」 스킬은 보고서 화면에서만 작동해, 챗 화면에서 시작하는 새 대화에서는 쓰이지 않습니다. 고급 › 처음 여는 화면을 보고서로 바꾸세요.',
  )
  expect(state.problems).toEqual([])
})

test('a report-only skill makes the agent open on the report screen', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [...mySkills, reportOnly] }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('주간보고 작성기')
  await page.getByText('스킬과 도구', { exact: true }).click()
  await pickSkill(page, /개조식 보고/)

  await expect(page.getByTestId('skill-surface-warning')).toHaveCount(0)
  await expect(page.getByTestId('skill-start-note')).toHaveText(
    '「개조식 보고」 스킬은 보고서 화면에서 작동합니다. 이 에이전트는 보고서 화면에서 시작합니다.',
  )
  await page.getByText('고급', { exact: true }).click()
  await expect(form.getByRole('combobox', { name: '처음 여는 화면' })).toHaveValue('report')
  await expect(form.getByRole('button', { name: '보고서', exact: true })).toHaveAttribute('aria-pressed', 'true')

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  expect(state.calls.find((c) => c.method === 'POST' && c.path === '/agents')?.body).toMatchObject({
    kinds: ['chat', 'report'], surface: 'report', activeSkillIds: [reportOnly.id],
  })
  expect(state.problems).toEqual([])
})

test('a screen added in 사용할 화면 is the one a new conversation opens on', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await page.getByText('고급', { exact: true }).click()
  const opens = form.getByRole('combobox', { name: '처음 여는 화면' })
  await expect(opens).toHaveValue('')
  await form.getByRole('button', { name: '보고서', exact: true }).click()
  await expect(opens).toHaveValue('report')
  // A second screen leaves the chosen one.
  await form.getByRole('button', { name: '슬라이드', exact: true }).click()
  await expect(opens).toHaveValue('report')
  expect(state.problems).toEqual([])
})

test('a new account keeps on an official skill without adding it first', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [] }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await page.getByText('스킬과 도구', { exact: true }).click()
  await page.getByTestId('active-skill-picker').getByRole('button', { name: '켜 둘 스킬 고르기' }).click()
  const dialog = page.getByRole('dialog', { name: '켜 둘 스킬 고르기' })
  // Official skills are listed as in the chat's 「+ › 스킬」; another person's shared skill is not.
  await expect(dialog.getByText('공식 스킬 · 고르면 내 스킬에 추가됩니다')).toBeVisible()
  await expect(dialog.getByText('검색 결과가 없습니다')).toHaveCount(0)
  await expect(dialog.getByRole('checkbox', { name: /보도자료 교정/ })).toHaveCount(0)
  await dialog.getByRole('checkbox', { name: /견적 계산/ }).check()
  await page.keyboard.press('Escape')

  const picker = page.getByTestId('active-skill-picker')
  await expect(picker.getByText('견적 계산', { exact: true })).toBeVisible()
  await expect.poll(() => state.calls.some((c) => c.method === 'POST' && c.path === `/skills/${storeSkills[0].id}/install`)).toBe(true)
  // Now one of the person's skills, under 공식 스킬.
  await picker.getByRole('button', { name: '켜 둘 스킬 고르기' }).click()
  await expect(dialog.getByRole('group', { name: '공식 스킬' }).getByRole('checkbox', { name: /견적 계산/ })).toBeChecked()
  expect(state.problems).toEqual([])
})
