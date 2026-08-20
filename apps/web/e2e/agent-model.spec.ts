import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Which model an agent's conversation actually runs on.
 *
 * The API resolves a turn's model as turn override → conversation → agent, so
 * the agent only ever gets a say when the conversation has no model of its own.
 * The client used to stamp the screen default onto every new conversation,
 * which meant the last step was unreachable: an agent pinned to a strict-local
 * model for privacy quietly ran on whatever the chat screen happened to
 * default to, and the agent card went on printing the pinned model as a badge.
 *
 * Two claims here, and they have to agree: the composer names the agent's
 * model, and picking another one in the composer still wins — an override is
 * supposed to be something a person did on purpose.
 */
test('에이전트로 시작한 대화는 에이전트의 모델을 쓰고, 작성창에서 바꾸면 그것이 이긴다', async ({
  page,
}) => {
  test.setTimeout(180_000)
  await signIn(page)

  // The chat screen's own default, read where it is named out loud, so the
  // assertion below cannot pass by accident on an instance where the agent
  // happens to be pinned to that same model.
  await page.goto('/settings/preferences')
  const chatDefault = page.getByRole('button', { name: /^챗: / })
  await expect(chatDefault).toBeVisible({ timeout: 20_000 })
  const surfaceDefault = ((await chatDefault.getAttribute('aria-label')) ?? '')
    .replace(/^챗: /, '')
    .trim()

  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  const dialog = page.getByRole('dialog')

  const select = dialog.getByLabel('모델')
  const labels = (await select.locator('option').allTextContents()).map((s) => s.trim())
  const pinned = labels.find((label) => label !== surfaceDefault)
  test.skip(!pinned, '모델이 하나뿐인 인스턴스에서는 우선순위를 구분할 수 없습니다.')

  const name = `모델요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('모델 우선순위 확인용')
  // A real prompt: a run that dies before cleanup leaves this row behind, and
  // `starter.spec` checks that every agent has one.
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill('너는 짧고 사실만 담아 답한다. 모르는 것은 모른다고 말한다.')
  await select.selectOption({ label: pinned! })
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  await page.getByRole('button', { name: `${name} 삭제` }).waitFor({ timeout: 20_000 })
  const card = page
    .locator('div')
    .filter({ has: page.getByRole('button', { name: `${name} 삭제` }) })
    .last()
  // The badge is the claim the composer has to live up to.
  await expect(card.getByText(pinned!, { exact: true })).toBeVisible()

  await card.getByRole('button', { name: '실행' }).click()
  await page.waitForURL(/\/s\//, { timeout: 20_000 })
  await expect(page.getByRole('button', { name: pinned! })).toBeVisible({ timeout: 20_000 })

  // Deliberate override: the conversation now has a model of its own, so the
  // agent's stops applying — the precedence, not a special case.
  const other = labels.find((label) => label !== pinned)
  if (other) {
    await page.getByRole('button', { name: pinned! }).click()
    await page.getByRole('menu').getByRole('button', { name: other }).first().click()
    await expect(page.getByRole('button', { name: other })).toBeVisible({ timeout: 20_000 })
    // And it survives a reload, because it was written to the conversation
    // rather than held in the picker.
    await page.reload()
    await expect(page.getByRole('button', { name: other })).toBeVisible({ timeout: 20_000 })
  }

  await page.goto('/agents')
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})
