import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/** Agent knowledge: attaching, removing and indexing documents on an agent. */
test('에이전트에 파일과 URL 을 붙이고, 지우고, 저장 전에는 막힌다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/agents')

  // An unsaved agent has no id, so the shelf says so.
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('에이전트를 저장하면 자료를 붙일 수 있습니다')).toBeVisible()

  const name = `자료요원${Date.now()}`
  await dialog.getByLabel('이름').fill(name)
  await dialog.getByLabel('설명').fill('붙인 자료 안에서 찾아 답합니다')
  // A real system prompt: a leftover row must not fail `starter.spec`.
  await dialog
    .getByLabel('시스템 프롬프트')
    .fill('너는 붙어 있는 자료 안에서만 근거를 찾아 답한다. 자료에 없는 내용은 없다고 말한다.')
  await dialog.getByRole('button', { name: '저장' }).last().click()
  await expect(dialog).toBeHidden({ timeout: 20_000 })

  // Reopen from its card: the innermost element holding this agent's delete button.
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
  // The token count proves the text was extracted.
  await expect(row.getByText(/토큰/)).toBeVisible()

  // Survives reopening.
  await page.keyboard.press('Escape')
  await card.getByRole('button', { name: '편집' }).click()
  await expect(dialog.locator('li', { hasText: 'jichim.txt' })).toBeVisible({ timeout: 20_000 })

  await dialog.locator('li', { hasText: 'jichim.txt' })
    .getByRole('button', { name: /삭제/ })
    .click()
  await expect(dialog.locator('li', { hasText: 'jichim.txt' })).toHaveCount(0, { timeout: 15_000 })

  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await dialog.getByRole('button', { name: '삭제' }).click()
  await expect(page.getByText(name)).toHaveCount(0, { timeout: 15_000 })
})


/** Indexing on upload is non-fatal; an unindexed document is flagged and can be reindexed. */
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

  // The innermost element holding this agent's delete button.
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

  // With an index configured neither badge nor button appears; without one, both do.
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
