import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/** A turn survives the reader leaving the page, and the screen matches what was stored. */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('생성 중에 다른 화면에 다녀와도 답이 도착해 있다', async ({ page }) => {
  test.setTimeout(420_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/chat')

  const token = `TURN-${Math.random().toString(36).slice(2, 7).toUpperCase()}`
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toBeVisible({ timeout: 20_000 })
  await box.fill(
    `${token} 라는 표식으로 시작해서, 광합성의 명반응과 암반응을 각각 세 문장으로 설명해 주세요.`,
  )
  await page.keyboard.press('Enter')

  // Leave as soon as the stream opens.
  await expect(page.getByLabel('중지')).toBeVisible({ timeout: 45_000 })
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  const conversation = page.url()
  await page.goto('/artifacts')
  await expect(page.getByRole('heading', { name: '아티팩트' })).toBeVisible({ timeout: 20_000 })
  await page.waitForTimeout(4_000)

  // Return inside the app, not by reload.
  await page.goto(conversation)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })

  // Still running or finished; either way the answer is here.
  await expect(
    page.locator('p').filter({ hasText: /광합성|명반응|암반응/ }).first(),
    '자리를 비운 사이에 만들어진 답이 화면에 오지 않았습니다',
  ).toBeVisible({ timeout: 240_000 })
})

test('턴이 끝나면 화면이 저장된 것과 일치한다', async ({ page }) => {
  test.setTimeout(300_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/chat')

  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toBeVisible({ timeout: 20_000 })
  await box.fill('대한민국의 수도는 어디입니까? 한 낱말로 답하세요.')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 180_000 })
  await page.waitForTimeout(2_500)

  const onScreen = (await page.locator('main').innerText()).replace(/\s+/g, ' ')

  // What the server stored.
  const stored = await page.evaluate(async ([email, password, url]) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    const { accessToken } = await login.json()
    const id = url.split('/s/')[1]
    const row = await (
      await fetch(`/api/sessions/${id}`, { headers: { Authorization: `Bearer ${accessToken}` } })
    ).json()
    const messages: { role: string; content: string }[] = row.messages ?? []
    const last = [...messages].reverse().find((m) => m.role === 'assistant')
    return (last?.content ?? '').replace(/\s+/g, ' ')
  }, [USER.email, USER.password, page.url()] as [string, string, string])

  expect(stored.length, '서버에 저장된 답이 없습니다').toBeGreaterThan(0)
  // The stored answer's head is on screen.
  const head = stored.slice(0, 30).trim()
  expect(onScreen, `화면이 저장된 답을 담고 있지 않습니다: 「${head}」`).toContain(head)
})
