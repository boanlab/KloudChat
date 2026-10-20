import { expect, test, type Page } from '@playwright/test'
import { hubFixture, mySkills, storeSkills } from './customize-fixture'

/**
 * 「이 스킬로 에이전트 만들기」 drafts an agent that applies the skill on every turn (the person only
 * checks the name and saves; a store skill is installed on save, once), and each of an agent's
 * skills is 「항상 적용」 or 「필요할 때」.
 */

test.use({ serviceWorkers: 'block' })

const made = (state: Awaited<ReturnType<typeof hubFixture>>) =>
  state.calls.filter((c) => c.method === 'POST' && c.path === '/agents')

const openSkills = async (page: Page) => {
  const group = page.getByTestId('agent-tools-group')
  if ((await group.getAttribute('open')) === null) await page.getByText('스킬과 도구', { exact: true }).click()
}

test('an own skill becomes an agent with the name checked and saved', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const skill = mySkills[1]
  await page.goto(`/customize/skills/${skill.id}`)
  await page.getByTestId('item-detail-header').getByRole('button', { name: '이 스킬로 에이전트 만들기' }).click()
  await expect(page).toHaveURL(new RegExp(`/customize/agents/new\\?from=skill:${skill.id}`))
  await expect(page.getByTestId('skill-draft-notice')).toHaveText('「주간 현황 보고」 스킬로 채워 두었습니다. 이름을 확인하고 저장하세요.')
  const form = page.getByTestId('agent-form')
  await expect(form.getByRole('textbox', { name: '이름' })).toHaveValue('주간 현황 보고')

  await openSkills(page)
  const row = page.getByTestId('agent-skill-row').filter({ hasText: '주간 현황 보고' })
  await expect(row.getByRole('radio', { name: '항상 적용' })).toHaveAttribute('aria-checked', 'true')
  await expect(row).toContainText('약 320 토큰')

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  const [body] = made(state).map((c) => c.body)
  expect(body).toMatchObject({ name: '주간 현황 보고', skillIds: [skill.id], activeSkillIds: [skill.id], kinds: skill.kinds })
  expect(String(body.systemPrompt)).toContain('「주간 현황 보고」 스킬을 매 턴 적용해 일합니다.')
  expect(state.problems).toEqual([])
})

test('a store skill is installed once on save, and an installed one is not installed again', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const shared = storeSkills[1]
  await page.goto('/customize?tab=skills&view=explore')
  await page.getByTestId('item-card').filter({ hasText: shared.name }).getByTestId('skill-make-agent').click()
  await expect(page.getByTestId('skill-draft-notice')).toContainText('저장하면 이 스킬이 내 스킬에 설치됩니다.')
  await openSkills(page)
  await expect(page.getByTestId('agent-skill-row').filter({ hasText: shared.name })).toContainText('저장하면 내 스킬에 설치됩니다.')
  // nothing installed before the save
  expect(state.calls.some((c) => c.path.endsWith('/install'))).toBe(false)

  await page.getByTestId('agent-form').getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  const copy = `copy-${shared.id}`.slice(0, 32)
  expect(state.calls.filter((c) => c.path === `/skills/${shared.id}/install`)).toHaveLength(1)
  expect(made(state)[0].body).toMatchObject({ skillIds: [copy], activeSkillIds: [copy] })

  // Again from the same store skill: its copy is used, no second install.
  await page.goto(`/customize/agents/new?from=skill:${shared.id}`)
  await expect(page.getByTestId('skill-draft-notice')).not.toContainText('설치됩니다')
  await page.getByTestId('agent-form').getByRole('textbox', { name: '이름' }).fill('보도자료 교정 2')
  await page.getByTestId('agent-form').getByRole('button', { name: '만들기' }).click()
  await expect.poll(() => made(state).length).toBe(2)
  expect(state.calls.filter((c) => c.path.endsWith('/install'))).toHaveLength(1)
  expect(made(state)[1].body).toMatchObject({ activeSkillIds: [copy] })
  expect(state.problems).toEqual([])
})

test('each skill is applied always or when needed, and an empty chosen list says it uses none', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('주간 정리')
  await openSkills(page)
  const list = page.getByTestId('agent-skill-list')
  await list.getByRole('button', { name: '직접 고르기' }).click()
  await expect(list.getByTestId('skill-mode-hint')).toHaveText(
    '「직접 고르기」에서 아무 스킬도 두지 않으면 이 에이전트는 스킬을 쓰지 않습니다. 입력창에서 골라도 쓰이지 않습니다.',
  )

  for (const name of ['데이터 분석', '주간 현황 보고']) {
    await list.getByRole('button', { name: '+ 스킬 추가 (스토어 포함)' }).click()
    await page.getByRole('dialog', { name: '스킬 추가' }).getByRole('listitem').filter({ hasText: name })
      .getByRole('button', { name: '추가', exact: true }).click()
    await page.keyboard.press('Escape')
  }
  await expect(list.getByTestId('skill-mode-hint')).toHaveText('이 목록의 스킬만 씁니다. 입력창에서도 이 스킬만 고를 수 있습니다.')
  const analysis = list.getByTestId('agent-skill-row').filter({ hasText: '데이터 분석' })

  // added to a chosen list: needed-when first
  await expect(analysis.getByRole('radio', { name: '필요할 때' })).toHaveAttribute('aria-checked', 'true')
  await analysis.getByRole('radio', { name: '항상 적용' }).click()
  await expect(analysis.getByRole('radio', { name: '항상 적용' })).toHaveAttribute('aria-checked', 'true')
  await expect(list.getByTestId('pinned-total')).toHaveText('항상 적용 1/6개 · 약 320 / 8,000 토큰')
  await analysis.getByText('본문 미리 보기').click()
  await expect(analysis.getByTestId('skill-body-preview')).toContainText('1. 자료를 읽는다')

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  expect(made(state)[0].body).toMatchObject({
    skillIds: [mySkills[0].id, mySkills[1].id],
    activeSkillIds: [mySkills[0].id],
  })
  expect(state.problems).toEqual([])
})

test('a save opened with a return path goes back there with the new agent, never off the app', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const skill = mySkills[1]
  const back = encodeURIComponent('/customize?tab=agents')
  await page.goto(`/customize/agents/new?from=skill:${skill.id}&return=${back}`)
  await expect(page.getByTestId('skill-draft-notice')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('agent-form').getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\?tab=agents&added=a9999999999999999999999999999999$/)

  // another site's address is not followed: the agent opens as usual
  await page.goto(`/customize/agents/new?from=skill:${skill.id}&return=${encodeURIComponent('//evil.example/x')}`)
  await expect(page.getByTestId('skill-draft-notice')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('agent-form').getByRole('textbox', { name: '이름' }).fill('주간 현황 보고 2')
  await page.getByTestId('agent-form').getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  expect(state.problems).toEqual([])
})
