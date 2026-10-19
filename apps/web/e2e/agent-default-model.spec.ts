import { expect, test } from '@playwright/test'
import { hubFixture } from './customize-fixture'

/**
 * A new agent is not pinned to a model: it follows its surface's default, the admin's, and the
 * form names it. Price decides nothing: on a self-hosted install every model costs 0 and the
 * coding model is listed first, yet neither the agent nor the chat screen lands on it.
 */

test.use({ serviceWorkers: 'block' })

const row = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, label: `Local · ${name}`, name, vendor: 'Local', provider: 'local', kinds: ['chat', 'report', 'slides'],
  modality: 'chat', dataBoundary: 'hybrid', strictLocal: false, creditCost: 0, inputCreditCost: 0,
  supportsTools: true, contextWindow: 64000, description: '', ...extra,
})
const catalogue = {
  models: [
    row('local/qwen3-coder-next', 'Qwen3 Coder Next'),
    row('local/qwen3.8-27b', 'Qwen3.8 27B'),
    row('local/qwen3.8-flash', 'Qwen3.8 Flash'),
    row('vendor/cheap-image', 'Cheap Image', { kinds: ['image'], modality: 'image', creditCost: 1, creditPerImage: 1 }),
  ],
  litellmAvailable: true,
  defaultChatModel: 'local/qwen3.8-flash',
  defaultModelByKind: { report: 'local/qwen3.8-27b', slides: 'local/qwen3.8-27b' },
  defaultAvModelByMode: {},
  autoRouting: { enabled: false, available: false },
}

test('a new agent keeps the default model, named after the admin default, and is saved unpinned', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.route('**/api/models', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: catalogue }) : route.fallback(),
  )
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('기본 모델 도우미')
  await form.getByRole('textbox', { name: '지시' }).fill('짧게 답한다.')

  await page.getByTestId('agent-more-group').locator('summary').click()
  const model = form.getByLabel('모델')
  await expect(model).toHaveValue('')
  await expect(model.locator('option:checked')).toHaveText('기본 모델 (Local · Qwen3.8 Flash)')

  // A report agent names the report default.
  await form.getByRole('button', { name: '보고서', exact: true }).click()
  await form.getByRole('button', { name: '챗', exact: true }).click()
  await expect(model.locator('option:checked')).toHaveText('기본 모델 (Local · Qwen3.8 27B)')

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  const created = state.calls.find((c) => c.method === 'POST' && c.path === '/agents')
  expect(created?.body.model).toBe('')
  expect(state.problems).toEqual([])
})
