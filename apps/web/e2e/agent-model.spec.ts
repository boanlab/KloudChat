import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Which model an agent's conversation actually runs on.
 *
 * The API resolves a turn's model as turn override → conversation → agent, so
 * the agent only gets a say when the conversation has no model of its own —
 * which means the client must not stamp the screen default onto a conversation
 * opened against an agent. Otherwise an agent pinned to a strict-local model
 * runs on whatever the chat screen defaults to while its card prints the
 * pinned one.
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
  // The list opens with "no pinned model", which is a state and not a model.
  // Choosing it here would be asserting the opposite of what follows.
  const labels = (await select.locator('option').allTextContents())
    .map((s) => s.trim())
    .filter((label) => label !== '화면 기본 모델')
  const pinned = labels.find((label) => label !== surfaceDefault)
  test.skip(!pinned, '모델이 하나뿐인 인스턴스에서는 우선순위를 구분할 수 없습니다.')

  const name = `모델요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('모델 우선순위 확인용')
  // A real prompt: a run that dies before cleanup leaves this row behind, and
  // `starter.spec` checks that every agent has one.
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill(
      '너는 짧고 사실만 담아 답한다. 모르는 것은 모른다고 말하고, 확인하지 못한 것은 ' +
        '확인이 필요하다고 적는다.',
    )
  await select.selectOption({ label: pinned! })
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  await page.getByRole('button', { name: `${name} 삭제` }).waitFor({ timeout: 20_000 })
  // The innermost box holding both the name and its delete button, which is
  // the card — filtering on the button alone lands on the button's own row,
  // one level below the badges.
  const card = page
    .locator('div')
    .filter({ hasText: name })
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
    // The editor's <select> names a model in full — "Qwen · Qwen3.6 35b" —
    // while the picker's rows sit under a vendor heading and print the name
    // alone. Same model, and this is the half the menu shows.
    const rowName = other.split(' · ').pop()!
    await page.getByRole('menu').getByRole('button', { name: rowName, exact: false }).first().click()
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

/**
 * The state the product recommends, made sayable.
 *
 * An agent that pins no model follows whichever model the screen it is run on
 * defaults to, and every agent the instance seeds is in that state — the card
 * has always drawn it as 화면 기본 모델. The 모델 list, though, offered models
 * and nothing else, so opening such an agent showed a model it had not been
 * given and no way back to having none. Both halves are checked here: the
 * editor can put an agent into that state, and it still reads it back as that
 * state afterwards.
 */
test('모델을 고정하지 않은 에이전트를 편집기에서 만들 수 있고, 다시 열어도 그대로다', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  const dialog = page.getByRole('dialog')

  const name = `기본모델요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('모델을 고정하지 않는 에이전트')
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill('너는 짧고 사실만 담아 답한다. 확인하지 못한 것은 확인이 필요하다고 적는다.')
  await dialog.getByLabel('모델').selectOption('')
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  await page.getByRole('button', { name: `${name} 삭제` }).waitFor({ timeout: 20_000 })
  const card = page
    .locator('div')
    .filter({ hasText: name })
    .filter({ has: page.getByRole('button', { name: `${name} 삭제` }) })
    .last()
  await expect(card.getByText('화면 기본 모델', { exact: true })).toBeVisible()

  // Reopened rather than reasoned about: a select that cannot hold the value
  // is exactly how the model got pinned by the act of opening the editor.
  await card.getByRole('button', { name: '편집' }).click()
  await expect(dialog.getByLabel('모델')).toHaveValue('')

  await dialog.getByRole('button', { name: '취소' }).click()
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})
