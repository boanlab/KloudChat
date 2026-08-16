import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A delete that has not happened yet.
 *
 * The confirmation added in round two stops the wrong press; this is for the
 * right press on the wrong row. There is no soft delete on the server, so the
 * undo is the request not having been sent — which means the test has to check
 * the *server*, not the screen, to know whether anything survived.
 */
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

  // Gone from the *list* at once — waiting for the server would make the
  // interface feel slower than the decision. Checked by the row's own delete
  // button rather than by the name, because the undo banner says the name too.
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(0, {
    timeout: 10_000,
  })

  const undo = page.getByRole('button', { name: '실행 취소' })
  await expect(undo).toBeVisible()
  await undo.click()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(1, {
    timeout: 10_000,
  })

  // The row is back on screen because it never left the server. A reload is
  // the only way to tell that apart from a local restore of something already
  // destroyed.
  await page.reload()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(1, {
    timeout: 20_000,
  })

  // Now let it go, and let the window pass.
  await page.getByRole('button', { name: `${name} 삭제` }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
  await page.waitForTimeout(8_000)
  await page.reload()
  await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(0, {
    timeout: 20_000,
  })
})
