import { expect, test, type Page } from '@playwright/test'
import { setWebSearch } from './composer-tools'
import { mockApp, model, now, sessionId } from './super-agent-fixture'

const route = {
  mode: 'super' as const, taskClass: 'reasoning' as const, source: 'rule' as const,
  reasonCode: 'reasoning_markers', selectedModel: 'external/deep', effort: 'high' as const,
  pick: 'rule', tools: 'full' as const, routerVersion: 'v', reasoningMode: 'deep' as const,
  row: 'escalation' as const,
}

const chip = (page: Page) => page.getByTestId('reasoning-chip')

async function send(page: Page, text: string) {
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill(text)
  await page.getByLabel('프롬프트 입력').press('Enter')
}

test('추론 칩: 고르면 대화에 남고, 새로 열어도 그대로이며, 질문과 함께 가고, 답 줄에 쓴 강도를 적는다 @phone', async ({ page }) => {
  const state = await mockApp(page, 'super', route, undefined, {
    reasoning: { mode: 'deep', effort: 'high' }, showUsage: true,
  })
  await page.goto(`/s/${sessionId}`)
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'auto')
  await chip(page).click()
  await expect(page.getByRole('menuitemcheckbox', { name: '빠르게' })).toBeVisible()
  await page.getByRole('menuitemcheckbox', { name: '깊게' }).click()
  await expect.poll(() => state.patches.at(-1)).toMatchObject({ reasoningMode: 'deep' })
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'deep')

  // kept on the conversation: a reload starts from it
  await page.reload()
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'deep')
  await expect(chip(page)).toContainText('깊게')

  await send(page, '모놀리식을 쪼갤지 판단해 줘')
  await expect.poll(() => state.turns.at(-1)).toMatchObject({ reasoning: 'deep' })
  await expect(page.getByTestId('usage-reasoning')).toHaveText(/깊게 · high/)

  // the chip fits the phone's line beside the model, and nothing scrolls sideways
  const width = page.viewportSize()?.width ?? 1440
  if (width < 640) {
    const box = await chip(page).boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
})

test('홈 화면에서 고른 추론은 새 대화가 가져가고 첫 질문과 함께 간다', async ({ page }) => {
  const state = await mockApp(page, 'manual', undefined, undefined, { showUsage: true })
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: 'Super Agent', exact: true })).toBeVisible()
  await chip(page).click()
  await page.getByRole('menuitemcheckbox', { name: '빠르게' }).click()
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'fast')
  await send(page, '안녕')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ routingMode: 'super', reasoningMode: 'fast' })
  await expect.poll(() => state.turns.at(-1)).toMatchObject({ reasoning: 'fast' })
})

test('추론을 조절할 수 없는 모델은 칩을 끄고 이유를 보여 준다', async ({ page }) => {
  const catalogue = [
    model('local/flash', 'Mock · Flash', 'hybrid', 0, true),
    model('strict-local/flash', 'Mock · Strict Flash', 'self_hosted', 0, true),
    model('external/plain', 'Mock · Plain', 'external', 10, false),
  ]
  const state = await mockApp(page, 'manual', undefined, undefined, { catalogue })
  const row = {
    id: sessionId, kind: 'chat', title: '직접 고른 모델', projectId: null, agentId: null,
    model: 'external/plain', routingMode: 'manual', artifactId: null, pinned: false,
    createdAt: now, updatedAt: now, messages: [], preview: null, messageCount: 0,
  }
  state.sessions = [row]
  await page.goto(`/s/${sessionId}`)
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'unsupported')
  await expect(chip(page)).toHaveAttribute('aria-disabled', 'true')
  await expect(chip(page)).toHaveAttribute('title', /추론 강도를 바꿀 수 없어/)
  // pressing it opens nothing: there is no choice to make
  await chip(page).click({ force: true })
  await expect(page.getByRole('menuitemcheckbox', { name: '깊게' })).toHaveCount(0)

  // a model whose reasoning can be steered offers the choices
  state.sessions = [{ ...row, model: 'local/flash' }]
  await page.reload()
  await expect(chip(page)).toHaveAttribute('data-reasoning', 'auto')
  await chip(page).click()
  await expect(page.getByRole('menuitemcheckbox', { name: '깊게' })).toBeVisible()
})
