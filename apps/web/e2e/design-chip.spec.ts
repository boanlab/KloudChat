import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openSidebar, signIn } from './helpers'

/** The 서식 chip follows the session row across reloads and conversation changes;
 *  media 서식 leave no chip but name the option values they set. Model requests are stubbed. */

/** A bearer token of the test's own; the app holds its token in memory. */
async function token(page: Page) {
  return page.evaluate(async (account) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    return (await res.json()).accessToken as string
  }, E2E_ADMIN)
}

/** A session already wearing a 서식, created via the API. The title is stamped: it is later clicked in the sidebar. */
async function wearing(
  page: Page,
  kind: 'slides' | 'report',
  templateId: string,
  name: string,
) {
  const title = `${name} ${Date.now().toString(36)}`
  const headers = { Authorization: `Bearer ${await token(page)}` }
  const created = await page.request.post('/api/sessions', { headers, data: { kind } })
  const { id } = (await created.json()) as { id: string }
  await page.request.patch(`/api/sessions/${id}`, {
    headers,
    data: { renderTemplateId: templateId, title },
  })
  return { id, title }
}

/** The gallery card for one 서식, found by search since the gallery pages its grid. */
async function card(page: Page, name: string) {
  await page.getByRole('button', { name: '양식 고르기' }).click()
  const dialog = page.getByRole('dialog')
  const search = dialog.getByLabel('양식 검색')
  if (await search.count()) await search.fill(name)
  const found = dialog.locator('div.group', { hasText: name }).first()
  await expect(found).toBeVisible({ timeout: 20_000 })
  return found
}

test('세션이 입고 있는 서식은 새로고침해도 칩으로 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const { id: sessionId } = await wearing(page, 'slides', 'deck-editorial', '편집형 덱을 입은 대화')

  await page.goto(`/s/${sessionId}`)
  const chip = page.getByRole('button', { name: '편집형 덱 양식 해제' })
  await expect(chip).toBeVisible({ timeout: 20_000 })

  // The chip is the only place the shape is named.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)

  // Drawn from the session row, which survives a reload.
  await page.reload()
  await expect(chip).toBeVisible({ timeout: 20_000 })

  // Taking it off reaches the row.
  await chip.click()
  await expect(page.getByRole('button', { name: '편집형 덱 양식 해제' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '편집형 덱 양식 해제' })).toHaveCount(0)
})

test('한 대화에서 고른 서식이 다음 대화의 서식을 덮지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const { id: lecture, title: lectureTitle } = await wearing(page, 'slides', 'deck-lecture', '강의 자료 대화')

  // Stubbed turn: the subject is where the pick goes at submit.
  await page.route('**/api/sessions/*/messages', (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body:
        'data: {"type":"page","html":"<section class=\\"slide\\">스텁</section>"}\n' +
        'data: {"type":"usage","inputTokens":0,"outputTokens":0,"credits":0}\n',
    }),
  )

  await page.goto('/new/slides')
  await (await card(page, '편집형 덱')).getByRole('button', { name: '이 양식으로 시작' }).click()
  await expect(page.getByRole('button', { name: '편집형 덱 양식 해제' })).toBeVisible()

  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // From the sidebar, not by address: a reload would clear the pick regardless.
  await openSidebar(page)
  await page.getByRole('button', { name: lectureTitle }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${lecture}`), { timeout: 20_000 })

  // The pick was spent on its turn; this conversation shows only its own shape.
  await expect(page.getByRole('button', { name: '강의형 덱 양식 해제' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('button', { name: '편집형 덱 양식 해제' })).toHaveCount(0)
})
