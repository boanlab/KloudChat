import { expect, test } from '@playwright/test'
import { agentDraft, hubFixture } from './customize-fixture'

/**
 * 「에이전트로 저장」: a conversation opens the agent form prefilled — its name, what it settled,
 * its base and 묶음, its files — and nothing is saved until 저장. Fixture data only.
 */

test.use({ serviceWorkers: 'block' })

test('the form is prefilled from the conversation and nothing is saved before 저장', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('draft-notice')).toBeVisible()
  await expect(form.getByRole('textbox', { name: '이름' })).toHaveValue(agentDraft.name)
  await expect(form.getByRole('textbox', { name: '지시' })).toHaveValue(/이 대화에서 정한 것:\n- 보고 대상은 팀장/)
  await expect(form.getByRole('radio', { name: /기본 과정에서 시작/ })).toBeChecked()
  await expect(page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' })).toHaveValue('gaejo-report')
  const files = page.getByTestId('draft-files')
  await expect(files.getByRole('checkbox', { name: '주간 메모.txt' })).toBeChecked()
  // Nothing written yet.
  expect(state.calls.filter((c) => c.method !== 'GET')).toEqual([])

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  const writes = state.calls.map((c) => `${c.method} ${c.path}`)
  expect(writes).toEqual(expect.arrayContaining([
    'POST /agents', 'PUT /agents/a9999999999999999999999999999999/process',
    'POST /agents/a9999999999999999999999999999999/knowledge/from-files',
  ]))
  expect(state.calls.find((c) => c.path.endsWith('/from-files'))?.body).toEqual({ ids: [agentDraft.files[0].id] })
  expect(state.calls.find((c) => c.method === 'POST' && c.path === '/agents')?.body).toMatchObject({
    name: agentDraft.name, kinds: ['report'], surface: 'report',
  })
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

/** B-1: a conversation made with an official agent; the draft names the person's own theme. */
const officialDraft = {
  ...agentDraft, name: '회의록 에이전트', base: 'minutes', designSystemId: 'd1', renderTemplateId: null,
  systemPrompt: '이 대화에서 정한 것:\n- 결정과 할 일을 표로 둔다.', decided: ['결정과 할 일을 표로 둔다.'], files: [],
}

test('a conversation with an official agent saves as an agent', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { draft: officialDraft })
  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  const form = page.getByTestId('agent-form')
  await expect(form.getByRole('textbox', { name: '이름' })).toHaveValue('회의록 에이전트', { timeout: 15_000 })
  await expect(form.getByRole('textbox', { name: '지시' })).toHaveValue(/^이 대화에서 정한 것:/)
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  expect(state.calls.find((c) => c.method === 'POST' && c.path === '/agents')?.body).toMatchObject({ designSystemId: 'd1' })
  expect(state.problems).toEqual([])
})

test('a refused save says which field to fix', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo, { draft: { ...officialDraft, designSystemId: 'not-mine' } })
  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  const form = page.getByTestId('agent-form')
  await expect(form.getByRole('textbox', { name: '이름' })).toHaveValue('회의록 에이전트', { timeout: 15_000 })
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page.getByText('테마를 찾을 수 없습니다. 결과물 모양에서 테마를 다시 고르세요.')).toBeVisible()
  // The refusal opens the group that holds the field.
  await expect(page.getByTestId('agent-output-group')).toHaveAttribute('open', '')
  await expect(page.getByText('design_system_not_found')).toHaveCount(0)
})
