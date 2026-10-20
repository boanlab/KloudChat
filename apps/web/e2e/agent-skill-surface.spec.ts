import { expect, test } from '@playwright/test'
import { hubFixture, mySkills, storeSkills } from './customize-fixture'

/**
 * A skill kept on in an agent that none of the agent's surfaces run is said so next to the
 * picker, with the way out (add the surface or remove the skill), instead of being dropped
 * without a word at run time.
 */

test.use({ serviceWorkers: 'block' })

const reportOnly = { ...mySkills[1], id: 'k7777777777777777777777777777777', name: '개조식 보고', kinds: ['report'] }

/** 「+ 스킬 추가」: a skill added under 「기본값 따르기」 applies on every turn. */
const pickSkill = async (page: import('@playwright/test').Page, name: RegExp, button = '추가') => {
  await page.getByTestId('agent-skill-list').getByRole('button', { name: '+ 스킬 추가 (스토어 포함)' }).click()
  await page.getByRole('dialog', { name: '스킬 추가' }).getByRole('listitem').filter({ hasText: name })
    .getByRole('button', { name: button }).click()
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
  const report = form.getByRole('button', { name: '보고서', exact: true })
  await expect(report).toHaveAttribute('aria-pressed', 'true')
  // Clicked again by someone who did not see it was added: the skill needs it, so it stays on.
  await report.click()
  await expect(report).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('screen-held')).toHaveText(
    '「개조식 보고」 스킬이 보고서 화면에서 작동해 보고서 화면은 켜 둡니다. 빼려면 스킬을 먼저 빼세요.',
  )
  await expect(form.getByRole('combobox', { name: '처음 여는 화면' })).toHaveValue('report')

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

test('a new account keeps on a store skill without adding it first', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [] }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await page.getByText('스킬과 도구', { exact: true }).click()
  await page.getByTestId('agent-skill-list').getByRole('button', { name: '+ 스킬 추가 (스토어 포함)' }).click()
  const dialog = page.getByRole('dialog', { name: '스킬 추가' })
  // The store's skills are listed, official and a colleague's shared one alike.
  await expect(dialog.getByText('스토어 · 설치하면 내 스킬에 복사됩니다')).toBeVisible()
  await expect(dialog.getByRole('listitem').filter({ hasText: '보도자료 교정' })).toBeVisible()
  await dialog.getByRole('listitem').filter({ hasText: '견적 계산' }).getByRole('button', { name: '설치하고 추가' }).click()
  await page.keyboard.press('Escape')

  const row = page.getByTestId('agent-skill-row').filter({ hasText: '견적 계산' })
  await expect(row).toBeVisible()
  await expect(row.getByRole('radio', { name: '항상 적용' })).toHaveAttribute('aria-checked', 'true')
  await expect.poll(() => state.calls.some((c) => c.method === 'POST' && c.path === `/skills/${storeSkills[0].id}/install`)).toBe(true)
  // Now one of the person's skills: no longer offered from the store.
  await page.getByTestId('agent-skill-list').getByRole('button', { name: '+ 스킬 추가 (스토어 포함)' }).click()
  await expect(dialog.getByRole('listitem').filter({ hasText: '견적 계산' })).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('an agent saved while an official skill is still being added keeps that skill', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [] }) : route.fallback(),
  )
  // A slow add: the person saves before it answers.
  let release = () => {}
  const held = new Promise<void>((done) => (release = done))
  await page.route('**/api/skills/*/install', async (route) => {
    await held
    await route.fallback()
  })
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('견적 도우미')
  await page.getByText('스킬과 도구', { exact: true }).click()
  await pickSkill(page, /견적 계산/, '설치하고 추가')
  // Typed while the add is pending: kept too.
  await form.getByRole('textbox', { name: '이름' }).fill('견적 도우미 2')
  await form.getByRole('button', { name: '만들기' }).click()
  await page.waitForTimeout(300)
  release()

  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  const made = state.calls.filter((c) => c.method === 'POST' && c.path === '/agents')
  expect(made).toHaveLength(1)
  expect(made[0].body).toMatchObject({ name: '견적 도우미 2', activeSkillIds: [`copy-${storeSkills[0].id}`.slice(0, 32)] })
  expect(state.problems).toEqual([])
})
