import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

/**
 * 챗에 「협조 공문 작성해줘」라고 쓰면 챗이 아니라 보고서가 받는다.
 *
 * 「챗에서 공문 같은 문서를 작성해줘 라고 하면 챗이 아니라 보고서 쪽으로
 * 넘겨줘」 — 챗 말풍선은 문서의 모양이 아니다. 절도, 서식도, 파일도 없다.
 * 문장은 보고서 화면의 새 대화로 넘어가고, 문서에 *대해* 묻는 문장은 챗에
 * 남는다.
 *
 * The turn itself is not awaited: what is asserted is which surface the
 * session was created on, read back through the API, not what the model
 * wrote there.
 */

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
  // 넘어간 문장은 그대로 그 대화의 첫 요청이다.
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
