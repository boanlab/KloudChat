import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openSidebar, signIn, surfaceOn } from './helpers'

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
 *
 * The title is stamped, and the stamp is the point: this creates a row every
 * time it is called, once per project and again on the next run, and one of
 * these conversations is later opened by clicking its name in the sidebar. A
 * fixed title makes that click ambiguous the second time the file runs — the
 * failure names strict mode and says nothing about the shape being tested.
 */
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

/** The gallery card for one 서식, once its preview has had time to arrive. */
async function card(page: Page, name: string) {
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  // 쪽이 아니라 검색으로 찾는다. The gallery pages its grid and the catalogue
  // keeps growing, so a card written down as "on the first page" quietly
  // becomes a card the test reports as missing.
  const search = dialog.getByLabel(/서식 검색|시작점 검색/)
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
  const chip = page.getByRole('button', { name: '편집형 덱 서식 해제' })
  await expect(chip).toBeVisible({ timeout: 20_000 })

  // And the chip is the only place the shape is named. The empty screen's
  // "이 대화가 가지고 시작하는 것" card listed the 서식 as well for a while, so
  // a conversation wearing one said it twice before a word was typed — with the
  // × on only one of the two.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)

  // The chip has to be drawn from the session row, not from the pick: a pick
  // does not survive a reload and the row does, and the row is what shapes
  // every turn.
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
  const { id: lecture, title: lectureTitle } = await wearing(page, 'slides', 'deck-lecture', '강의 자료 대화')

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
  // 슬라이드에서 서식은 시작점 카드 위에서 고른다 — 결과 서식 탭이 그리로
  // 접혔다. `card()` 는 서식이 곧 일인 표면(이미지·오디오/동영상)에서 쓴다.
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const job = page.getByRole('dialog').locator('div.group').first()
  await job.getByRole('button', { name: /결과 모양 고르기/ }).click()
  await page.getByRole('menuitem', { name: '편집형 덱' }).click()
  await job.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()

  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // Opened from the sidebar rather than by address: a reload would clear the
  // pick whatever the code did, and the leak this is about happens without one.
  await openSidebar(page)
  await page.getByRole('button', { name: lectureTitle }).click()
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

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
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
  await page.getByRole('menuitemcheckbox', { name: /2장/ }).click()
  await expect(page.getByText('포스터 서식이 정한 값')).toHaveCount(0)
})

test('영상 서식이 정한 옵션은 칩이 없어도 출처를 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
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
