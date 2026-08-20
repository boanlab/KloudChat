import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openSidebar, signIn } from './helpers'

/**
 * What the composer says about the shape of the answer — across a reload, and
 * across a change of conversation.
 *
 * The chip had three lifetimes and none of them was the shape's. It hung off
 * the pick, which is client-only state, so a reload took the chip away while
 * every answer kept coming out in the shape the session was still wearing. The
 * pick was never put down once used, so a shape chosen in one conversation
 * outranked the shape the next one already had. And a media 서식 wrote the
 * option chips with nothing left to say afterwards where those values came
 * from.
 *
 * Nothing here writes a document or makes a picture: both cost real credits and
 * neither is what is under test. `design-templates.spec.ts` walks one 서식 all
 * the way to the file; this walks the composer, and stubs the two requests that
 * would otherwise run a model.
 */

/** The API takes a bearer token the app holds in memory, so a test asking for
 *  one of its own is the only way to reach the API beside the app. */
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

/**
 * A conversation already wearing a shape, made the way the server makes one.
 *
 * The alternative is to run a template turn for it, which writes an entire deck
 * for the sake of one column — and the column is the whole subject here.
 */
async function wearing(
  page: Page,
  kind: 'slides' | 'report',
  templateId: string,
  title: string,
) {
  const headers = { Authorization: `Bearer ${await token(page)}` }
  const created = await page.request.post('/api/sessions', { headers, data: { kind } })
  const { id } = (await created.json()) as { id: string }
  await page.request.patch(`/api/sessions/${id}`, {
    headers,
    data: { renderTemplateId: templateId, title },
  })
  return id
}

/** The gallery card for one 서식, once its preview has had time to arrive. */
async function card(page: Page, name: string) {
  await page.getByRole('button', { name: '서식 고르기' }).click()
  const found = page.getByRole('dialog').locator('div.group', { hasText: name })
  await expect(found).toBeVisible({ timeout: 20_000 })
  return found
}

test('세션이 입고 있는 서식은 새로고침해도 칩으로 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const sessionId = await wearing(page, 'slides', 'deck-editorial', '편집형 덱을 입은 대화')

  await page.goto(`/s/${sessionId}`)
  const chip = page.getByRole('button', { name: '편집형 덱 서식 해제' })
  await expect(chip).toBeVisible({ timeout: 20_000 })

  // The reason this test exists: the chip used to be drawn from the pick alone,
  // and a pick does not survive a reload. The row does, and the row is what
  // shapes every turn — so the composer said no shape was chosen while every
  // answer kept coming out in one.
  await page.reload()
  await expect(chip).toBeVisible({ timeout: 20_000 })

  // And taking it off still reaches the row rather than a memory of it.
  await chip.click()
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toHaveCount(0)
})

test('한 대화에서 고른 서식이 다음 대화의 서식을 덮지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const lecture = await wearing(page, 'slides', 'deck-lecture', '강의 자료 대화')

  // The turn is answered by a stub. A real one writes a deck block by block for
  // several minutes, and what is under test is where the pick goes at submit,
  // not what comes back.
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
  await (await card(page, '편집형 덱')).getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()

  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // Opened from the sidebar rather than by address: a reload would clear the
  // pick whatever the code did, and the leak this is about happens without one.
  await openSidebar(page)
  await page.getByRole('button', { name: '강의 자료 대화' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${lecture}`), { timeout: 20_000 })

  // The pick was spent on the turn that carried it, so this conversation shows
  // the shape it was already wearing — and only that one.
  await expect(page.getByRole('button', { name: '강의형 덱 서식 해제' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toHaveCount(0)
})

test('이미지 서식은 그 그림에서 끝나고, 남은 옵션은 누가 정했는지 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // The picture costs about 4,400 크레딧 and has nothing in it to assert; the
  // request going out is the whole event this test needs.
  let asked = 0
  await page.route('**/api/sessions/*/images', async (route) => {
    asked += 1
    await route.fulfill({ json: [] })
  })

  await page.goto('/new/image')
  await (await card(page, '포스터')).getByRole('button', { name: '이 서식으로 시작' }).click()
  const chip = page.getByRole('button', { name: '포스터 서식 해제' })
  await expect(chip).toBeVisible()
  // The card filled the sentence in and set the chips to match it.
  await expect(page.getByRole('button', { name: '비율 9:16' })).toBeVisible()

  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => asked, { timeout: 30_000 }).toBe(1)

  // No session row will ever hold an image 서식 — it is spent on the picture it
  // shaped — so a pick left standing here is a pick that shapes tomorrow's
  // picture too, in whatever conversation that turns out to be.
  await expect(chip).toHaveCount(0)

  // What the 서식 did leave is the option bar, and those values are one
  // workspace-wide preference rather than anything this conversation owns. With
  // the chip gone the bar is where it says so, so the ratio is not anonymous.
  await expect(page.getByText('포스터 서식이 정한 값')).toBeVisible()

  // Setting one by hand makes them the person's own, and the name goes with it.
  await page.getByRole('button', { name: '장수 1장' }).click()
  await page.getByRole('menuitem', { name: '2장' }).click()
  await expect(page.getByText('포스터 서식이 정한 값')).toHaveCount(0)
})

test('영상 서식이 정한 옵션은 칩이 없어도 출처를 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/new/av')
  await (await card(page, '발표 오프닝')).getByRole('button', { name: '이 서식으로 시작' }).click()

  // An a/v 서식 leaves no chip: it is spent on the sentence and on these chips
  // the moment it is picked, and nothing carries it at submit. That is exactly
  // why the values need a name — 1080p and 소리 있음 is the difference between
  // 12,000 and 32,000 크레딧, and nobody chose it in this conversation.
  await expect(page.getByRole('button', { name: '해상도 1080p' })).toBeVisible()
  await expect(page.getByText('발표 오프닝 서식이 정한 값')).toBeVisible()

  await page.getByRole('button', { name: '해상도 1080p' }).click()
  await page.getByRole('menuitem', { name: '720p' }).click()
  await expect(page.getByText('발표 오프닝 서식이 정한 값')).toHaveCount(0)
})
