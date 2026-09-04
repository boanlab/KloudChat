import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

/** A document request typed in chat opens a report session; a question about documents stays in chat.
 *  Asserted on the session kind, not the model's answer. */

const KIND_OF = `async ([id, email, password]) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  const { accessToken } = await login.json()
  const r = await fetch('/api/sessions/' + id, { headers: { Authorization: 'Bearer ' + accessToken } })
  return r.ok ? (await r.json()).kind : null
}`

test('챗에 쓴 문서 요청은 보고서 대화가 되고, 문서에 대한 질문은 챗에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/new/chat')
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('총장님께 보내는 협조 공문 작성해줘')
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/s\/[^/]+$/, { timeout: 30_000 })
  const handed = page.url().split('/s/')[1]
  const kind = await page.evaluate(
    async ([fn, ...args]) => await eval(fn)(args),
    [KIND_OF, handed, E2E_ADMIN.email, E2E_ADMIN.password],
  )
  expect(kind, '문서 요청이 보고서로 넘어가야 합니다').toBe('report')
  // The sentence is the new conversation's first request.
  await expect(page.getByText('총장님께 보내는 협조 공문 작성해줘').first()).toBeVisible()

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('공문 작성 방법 알려줘')
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/s\/[^/]+$/, { timeout: 30_000 })
  const stayed = page.url().split('/s/')[1]
  expect(stayed).not.toBe(handed)
  const chatKind = await page.evaluate(
    async ([fn, ...args]) => await eval(fn)(args),
    [KIND_OF, stayed, E2E_ADMIN.email, E2E_ADMIN.password],
  )
  expect(chatKind, '문서에 대한 질문은 챗에 남아야 합니다').toBe('chat')
})
