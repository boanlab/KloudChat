import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * An agent that can look things up in its own documents.
 *
 * Project files are pushed into every turn whole, inside a character budget —
 * past that budget the block degrades to a list of filenames and the model is
 * told the material exists without being shown any of it. An agent had no files
 * at all: the only way to give one background was to paste it into the system
 * prompt by hand.
 */
test('에이전트에 파일과 URL 을 붙이고, 지우고, 저장 전에는 막힌다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/agents')

  // A brand-new agent has no id, so the shelf says so rather than pretending.
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('에이전트를 저장하면 자료를 붙일 수 있습니다')).toBeVisible()

  const name = `자료요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('붙인 자료 안에서 찾아 답합니다')
  // A real system prompt, not because this test reads it, but because a run that
  // dies before its cleanup leaves this row behind — and `starter.spec` checks
  // that every agent has one. A fixture should not be able to fail another test.
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill('너는 붙어 있는 자료 안에서만 근거를 찾아 답한다. 자료에 없는 내용은 없다고 말한다.')
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  // Reopen from its card: now it is a saved agent and the shelf is live.
  // The innermost element holding *this* agent's delete button. Filtering by
  // text matched every ancestor up to the page body, and `.last()` on the
  // 편집 buttons inside then opened whichever agent happened to be last on
  // screen — the upload landed on a starter agent and stayed there.
  await page.getByRole('button', { name: `${name} 삭제` }).waitFor({ timeout: 20_000 })
  const card = page
    .locator('div')
    .filter({ has: page.getByRole('button', { name: `${name} 삭제` }) })
    .last()
  await card.getByRole('button', { name: '편집' }).click()
  await expect(dialog.getByText('아직 붙인 자료가 없습니다.')).toBeVisible({ timeout: 20_000 })

  await dialog.getByLabel('자료 파일').setInputFiles({
    name: 'jichim.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(
      '제1조 암호화\n저장 데이터는 AES-256 으로 암호화하고 키는 90일마다 교체한다.\n' +
        '제2조 로그\n접근 로그는 최소 1년간 보존한다.',
    ),
  })
  const row = dialog.locator('li', { hasText: 'jichim.txt' })
  await expect(row).toBeVisible({ timeout: 30_000 })
  // The token count is what tells you the text was actually extracted — a row
  // with a name and no reading is a document the agent cannot quote.
  await expect(row.getByText(/토큰/)).toBeVisible()

  // It survives reopening: it is a row, not this dialog's memory.
  await page.keyboard.press('Escape')
  await card.getByRole('button', { name: '편집' }).click()
  await expect(dialog.locator('li', { hasText: 'jichim.txt' })).toBeVisible({ timeout: 20_000 })

  // And it can be taken off again.
  await dialog.locator('li', { hasText: 'jichim.txt' })
    .getByRole('button', { name: /삭제/ })
    .click()
  await expect(dialog.locator('li', { hasText: 'jichim.txt' })).toHaveCount(0, { timeout: 15_000 })

  // Clean up the agent so the list does not fill with test rows.
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await dialog.getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})


/**
 * Documents attached before the index existed, and ones whose indexing failed.
 *
 * Indexing happens on upload and is deliberately non-fatal — the row is the
 * source of truth and the vector index is derived. That leaves a state the UI
 * has to be honest about: a document the word search finds and the
 * meaning-based search does not. It looks identical to a fully indexed one
 * unless something says otherwise.
 */
test('색인되지 않은 자료는 그렇다고 말하고, 다시 색인할 수 있다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/agents')

  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  const dialog = page.getByRole('dialog')
  const name = `색인요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('색인 상태 표시 확인용')
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill('너는 붙어 있는 자료 안에서만 근거를 찾아 답한다. 자료에 없는 내용은 없다고 말한다.')
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  // The innermost element holding *this* agent's delete button. Filtering by
  // text matched every ancestor up to the page body, and `.last()` on the
  // 편집 buttons inside then opened whichever agent happened to be last on
  // screen — the upload landed on a starter agent and stayed there.
  await page.getByRole('button', { name: `${name} 삭제` }).waitFor({ timeout: 20_000 })
  const card = page
    .locator('div')
    .filter({ has: page.getByRole('button', { name: `${name} 삭제` }) })
    .last()
  await card.getByRole('button', { name: '편집' }).click()

  await dialog.getByLabel('자료 파일').setInputFiles({
    name: 'jichim.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('제1조 로그\n접근 로그는 최소 1년간 보존한다.'),
  })
  const row = dialog.locator('li', { hasText: 'jichim.txt' })
  await expect(row).toBeVisible({ timeout: 30_000 })

  // With an index configured the upload is covered immediately, and the badge
  // and the button both stay away. Without one, both appear. Either is a
  // correct deployment — what must not happen is a badge with no way to act on
  // it, or a button offered when there is nothing to do.
  const badge = row.getByText('색인 안 됨')
  const button = dialog.getByRole('button', { name: '색인하기' })
  const stale = await badge.isVisible().catch(() => false)
  expect(await button.isVisible().catch(() => false), '배지와 버튼이 어긋난다').toBe(stale)

  if (stale) {
    await button.click()
    await expect(badge).toHaveCount(0, { timeout: 60_000 })
    await expect(button).toHaveCount(0)
  }

  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await dialog.getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})
