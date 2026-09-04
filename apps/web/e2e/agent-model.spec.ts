import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/** Model precedence is turn override → conversation → agent; the client must not stamp the
 *  screen default onto a conversation opened against an agent. */
test('에이전트로 시작한 대화는 에이전트의 모델을 쓰고, 작성창에서 바꾸면 그것이 이긴다', async ({
  page,
}) => {
  test.setTimeout(180_000)
  await signIn(page)

  // The chat screen default, so the pinned model can be chosen to differ from it.
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
  // 화면 기본 모델 is "no pinned model", not a model.
  const labels = (await select.locator('option').allTextContents())
    .map((s) => s.trim())
    .filter((label) => label !== '화면 기본 모델')
  const pinned = labels.find((label) => label !== surfaceDefault)
  test.skip(!pinned, '모델이 하나뿐인 인스턴스에서는 우선순위를 구분할 수 없습니다.')

  const name = `모델요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('모델 우선순위 확인용')
  // A real system prompt: a leftover row must not fail `starter.spec`.
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
  // The card: innermost box holding both the name and its delete button.
  const card = page
    .locator('div')
    .filter({ hasText: name })
    .filter({ has: page.getByRole('button', { name: `${name} 삭제` }) })
    .last()
  await expect(card.getByText(pinned!, { exact: true })).toBeVisible()

  await card.getByRole('button', { name: '실행' }).click()
  await page.waitForURL(/\/s\//, { timeout: 20_000 })
  await expect(page.getByRole('button', { name: pinned! })).toBeVisible({ timeout: 20_000 })

  // Override: the conversation now has a model of its own.
  const other = labels.find((label) => label !== pinned)
  if (other) {
    await page.getByRole('button', { name: pinned! }).click()
    // The <select> prints "Vendor · Name"; picker rows print the name alone.
    const rowName = other.split(' · ').pop()!
    await page.getByRole('menu').getByRole('button', { name: rowName, exact: false }).first().click()
    await expect(page.getByRole('button', { name: other })).toBeVisible({ timeout: 20_000 })
    // Written to the conversation, so it survives a reload.
    await page.reload()
    await expect(page.getByRole('button', { name: other })).toBeVisible({ timeout: 20_000 })
  }

  await page.goto('/agents')
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})

/** The editor can leave an agent with no pinned model (화면 기본 모델) and reads it back as such. */
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

  // Reopened: a select that cannot hold '' pins a model by the act of opening.
  await card.getByRole('button', { name: '편집' }).click()
  await expect(dialog.getByLabel('모델')).toHaveValue('')

  await dialog.getByRole('button', { name: '취소' }).click()
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})
