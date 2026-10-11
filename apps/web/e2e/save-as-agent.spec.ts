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
  await expect(page.getByText('테마를 찾을 수 없습니다. 아티팩트 모양에서 테마를 다시 고르세요.')).toBeVisible()
  // The refusal opens the group that holds the field.
  await expect(page.getByTestId('agent-output-group')).toHaveAttribute('open', '')
  await expect(page.getByText('design_system_not_found')).toHaveCount(0)
})

/** N1: a distillation that timed out says so, with 다시 시도; it never claims a filled 지시. */
test('a failed distillation says so and 다시 시도 fills the 지시', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, {
    decided: [
      { distilled: 'failed', prompt: '', decided: [], delayMs: 600 },
      { distilled: 'ok', prompt: agentDraft.systemPrompt, decided: agentDraft.decided },
    ],
  })
  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  const form = page.getByTestId('agent-form')
  const notice = page.getByTestId('draft-notice')
  // The rest of the form is usable while the 지시 is being read.
  await expect(form.getByRole('textbox', { name: '이름' })).toHaveValue(agentDraft.name, { timeout: 15_000 })
  await expect(notice).toHaveAttribute('data-state', 'pending')
  await expect(notice).not.toContainText('채워 두었습니다. 고친 뒤')
  await expect(notice).toHaveAttribute('data-state', 'failed')
  await expect(notice).toContainText('정리하지 못했습니다')
  await expect(form.getByRole('textbox', { name: '지시' })).toHaveValue('')
  await form.getByRole('textbox', { name: '지시' }).fill('표로 정리한다.')
  const drafts = state.reads.filter((p) => p.endsWith('/agent-draft')).length
  const asked = state.reads.filter((p) => p.endsWith('/agent-draft/decided')).length
  await notice.getByRole('button', { name: '다시 시도' }).click()
  await expect(notice).toHaveAttribute('data-state', 'ok')
  await expect(notice).toContainText('이 대화에서 정한 것을 채워 두었습니다')
  // What was typed meanwhile stays; the distilled section is added below it.
  await expect(form.getByRole('textbox', { name: '지시' })).toHaveValue(`표로 정리한다.\n\n${agentDraft.systemPrompt}`)
  // Only the distillation was asked again (dev StrictMode may double the first requests).
  expect(state.reads.filter((p) => p.endsWith('/agent-draft')).length).toBe(drafts)
  expect(state.reads.filter((p) => p.endsWith('/agent-draft/decided')).length).toBe(asked + 1)
  expect(state.problems).toEqual([])
})

test('nothing settled says so instead of 「채워 두었습니다」', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo, { decided: [{ distilled: 'empty', prompt: '', decided: [] }] })
  await page.goto('/customize/agents/new?from=session:c0000000000000000000000000000000')
  const notice = page.getByTestId('draft-notice')
  await expect(notice).toHaveAttribute('data-state', 'empty', { timeout: 15_000 })
  await expect(notice).toContainText('따로 정한 규칙은 찾지 못했습니다')
  await expect(page.getByTestId('agent-form').getByRole('textbox', { name: '지시' })).toHaveValue('')
})
