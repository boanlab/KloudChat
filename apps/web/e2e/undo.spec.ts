import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Undo after delete: the request is held for the undo window, so the server still has the row. */
test('지운 직후에는 되돌릴 수 있고, 되돌리면 서버에도 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const name = `되돌리기 확인 ${Date.now().toString(36)}`
  await page.goto('/memory')
  await page.getByRole('button', { name: '새 메모리' }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  await page.getByRole('dialog').getByRole('button', { name: /^저장$|^추가$/ }).last().click()
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 20_000 })

  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()

  // Gone from the list at once. By the delete button: the undo banner says the name too.
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(0, {
    timeout: 10_000,
  })

  const undo = page.getByRole('button', { name: '실행 취소' })
  await expect(undo).toBeVisible()
  await undo.click()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(1, {
    timeout: 10_000,
  })

  // A reload tells a server row from a local restore.
  await page.reload()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(1, {
    timeout: 20_000,
  })

  // Let the window pass.
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await page.waitForTimeout(8_000)
  await page.reload()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(0, {
    timeout: 20_000,
  })
})
