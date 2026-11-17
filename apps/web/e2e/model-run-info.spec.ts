import { expect, test } from '@playwright/test'
import { setWebSearch } from './composer-tools'
import type { AnswerRunInfo } from '../src/types'
import { mockApp, model, now, sessionId } from './super-agent-fixture'

// The picker shows one short chip per model and the details behind an ⓘ that is pressed, never
// hovered; an answer lists what it actually ran on.
const flash = {
  ...model('local/flash', 'Mock · Flash', 'hybrid'),
  runInfo: { quantization: 'NVFP4', kvCache: 'fp8', engine: 'vLLM 0.33.0' },
}
const wafer = {
  ...model('external/kimi', 'Mock · Kimi', 'external', 40),
  runInfo: {
    quantization: 'fp8', providerPinned: 'Wafer', zdr: true, policy: 'open_weight_strict',
  },
}
const undisclosed = {
  ...model('external/kt', 'Mock · KT', 'external', 40),
  runInfo: { quantization: 'unknown', providerPinned: 'KT', zdr: false },
}
const plain = model('external/plain', 'Mock · Plain', 'external', 40)
const catalogue = [flash, wafer, undisclosed, plain]

async function startSession(page: import('@playwright/test').Page, runInfo?: AnswerRunInfo) {
  const state = await mockApp(page, 'manual', undefined, undefined, { catalogue, runInfo })
  state.sessions = [{ ...state.sessions[0], id: sessionId, kind: 'chat', title: '', projectId: null, agentId: null, model: 'local/flash', routingMode: 'manual', artifactId: null, pinned: false, createdAt: now, updatedAt: now, messages: [], preview: null, messageCount: 0 }]
  await page.goto(`/s/${sessionId}`)
  return state
}

async function openPicker(page: import('@playwright/test').Page) {
  const state = await startSession(page)
  await page.getByRole('button', { name: /Mock · Flash/ }).click()
  return state
}

test('모델 항목에는 짧은 칩 하나만, 알려진 경우에만 붙는다 @phone', async ({ page }) => {
  await openPicker(page)
  const rows = page.getByTestId('model-row')
  await expect(rows.filter({ hasText: 'Mock · Flash' }).getByTestId('model-run-chip')).toHaveText('NVFP4')
  await expect(rows.filter({ hasText: 'Mock · Kimi' }).getByTestId('model-run-chip')).toHaveText('제공자: Wafer')
  await expect(rows.filter({ hasText: 'Mock · KT' }).getByTestId('model-run-chip')).toHaveText('제공자: KT')
  await expect(rows.filter({ hasText: 'Mock · Plain' }).getByTestId('model-run-chip')).toHaveCount(0)
  // the model name carries none of it
  await expect(page.getByRole('menuitemradio', { name: /Mock · Flash/ })).not.toContainText('vLLM')
})

test('ⓘ를 눌러야 상세가 열리고, 호버로는 열리지 않으며 선택과 겹치지 않는다 @phone', async ({ page }) => {
  const state = await openPicker(page)
  const row = page.getByTestId('model-row').filter({ hasText: 'Mock · Kimi' })
  const button = row.getByTestId('model-info-button')
  await expect(button).toHaveAttribute('aria-expanded', 'false')
  if (!page.viewportSize() || page.viewportSize()!.width >= 640) {
    await button.hover()
    await expect(row.getByTestId('model-info-panel')).toHaveCount(0)
  }
  await button.click()
  const panel = row.getByTestId('model-info-panel')
  await expect(panel).toBeVisible()
  await expect(button).toHaveAttribute('aria-expanded', 'true')
  const id = await panel.getAttribute('id')
  await expect(button).toHaveAttribute('aria-describedby', id!)
  await expect(panel).toContainText('양자화 (고정 제공자)')
  await expect(panel).toContainText('fp8')
  await expect(panel).toContainText('Wafer')
  await expect(panel).toContainText('양자화 공개·ZDR 엔드포인트')
  await expect(panel).not.toContainText('엔드포인트만')
  await expect(panel).toContainText('128.0k')
  // pressing ⓘ did not pick the model, and the menu stayed open
  expect(state.patches).toHaveLength(0)
  await expect(page.getByRole('menuitemradio', { name: /Mock · Flash/ })).toBeVisible()
  // the panel stays inside the screen
  const box = await panel.boundingBox()
  const width = page.viewportSize()!.width
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(width)
  // a second press closes it
  await button.click()
  await expect(panel).toHaveCount(0)
})

test('자체 호스팅 모델의 상세는 양자화·KV 캐시·엔진을, 미공개는 미공개로 말한다', async ({ page }) => {
  await openPicker(page)
  const flashRow = page.getByTestId('model-row').filter({ hasText: 'Mock · Flash' })
  await flashRow.getByTestId('model-info-button').click()
  const panel = flashRow.getByTestId('model-info-panel')
  await expect(panel).toContainText('양자화 (가중치)')
  await expect(panel).toContainText('NVFP4')
  await expect(panel).toContainText('KV 캐시')
  await expect(panel).toContainText('vLLM 0.33.0')
  const ktRow = page.getByTestId('model-row').filter({ hasText: 'Mock · KT' })
  // 미공개 only: nothing to detail, so no icon (the pin stays as the chip)
  await expect(ktRow.getByTestId('model-info-button')).toHaveCount(0)
  await expect(ktRow.getByTestId('model-run-chip')).toHaveText('제공자: KT')
})

test('양자화·KV 캐시·엔진 중 하나도 없는 모델에는 ⓘ가 없다 @phone', async ({ page }) => {
  await openPicker(page)
  const rows = page.getByTestId('model-row')
  await expect(rows.filter({ hasText: 'Mock · Plain' }).getByTestId('model-info-button')).toHaveCount(0)
  await expect(rows.filter({ hasText: 'Mock · KT' }).getByTestId('model-info-button')).toHaveCount(0)
  await expect(rows.filter({ hasText: 'Mock · Flash' }).getByTestId('model-info-button')).toHaveCount(1)
  await expect(rows.filter({ hasText: 'Mock · Kimi' }).getByTestId('model-info-button')).toHaveCount(1)
  // an icon-less row is as tall as its text, aligned with the others
  const box = (await rows.filter({ hasText: 'Mock · Plain' }).boundingBox())!
  const other = (await rows.filter({ hasText: 'Mock · KT' }).boundingBox())!
  expect(Math.abs(box.width - other.width)).toBeLessThan(1)
  expect(box.x).toBeCloseTo(other.x, 0)
})

test('키보드로 열고 Esc는 상세만 닫고 바깥 클릭으로도 닫힌다', async ({ page }) => {
  await openPicker(page)
  const row = page.getByTestId('model-row').filter({ hasText: 'Mock · Flash' })
  const button = row.getByTestId('model-info-button')
  await button.focus()
  await page.keyboard.press('Enter')
  const panel = row.getByTestId('model-info-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('최대 컨텍스트')
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(page.getByRole('menuitemradio', { name: /Mock · Kimi/ })).toBeVisible()
  await expect(button).toBeFocused()
  await button.click()
  await expect(panel).toBeVisible()
  await page.getByRole('heading', { level: 1 }).or(page.locator('body')).first().click({ position: { x: 2, y: 2 } })
  await expect(page.getByTestId('model-info-panel')).toHaveCount(0)
})

test('답 아래 이번 답 상세에 실제로 답한 제공자·호출 설정을 보여 준다 @phone', async ({ page }) => {
  await startSession(page, {
    model: 'external/kimi', provider: 'Wafer', fallback: false, quantization: 'fp8',
    providerPinned: 'Wafer', zdr: true, policy: 'open_weight_strict',
    call: { temperature: 0.4, topP: 0.9, seed: 7, maxTokens: 4000, reasoning: 'high', systemPromptHash: 'a1b2c3d4e5f6' },
  })
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  const button = page.getByTestId('run-details-button')
  await expect(button).toBeVisible()
  await expect(page.getByTestId('run-details-panel')).toHaveCount(0)
  await button.click()
  const panel = page.getByTestId('run-details-panel')
  for (const text of ['Wafer', '양자화 (고정 제공자)', 'fp8', 'temperature', '0.4', 'top_p', '0.9', 'seed', 'max_tokens', '4000',
    '추론 노력', 'high', 'a1b2c3d4e5f6', '대체 모델로 답함', '아니요', '양자화 공개·ZDR 엔드포인트']) {
    await expect(panel).toContainText(text)
  }
  // the prompt itself is never there
  await expect(panel).not.toContainText('규칙')
  await page.reload()
  await page.getByTestId('run-details-button').click()
  await expect(page.getByTestId('run-details-panel')).toContainText('Wafer')
})
