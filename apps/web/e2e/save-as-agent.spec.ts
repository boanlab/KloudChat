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
