import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn, surfaceOn } from './helpers'

/**
 * What a media session leaves behind for whoever comes back to it.
 *
 * Every picture and clip session on this account was an untitled row with no
 * messages: anonymous in 대화 기록, blank when opened, and `artifactId: null`
 * although the gallery's 원본 작업 열기 could already point the other way. The
 * writing surfaces never had the problem because they run a turn, and a turn
 * writes a title and hangs the finished document on the session.
 *
 * One narration per run, at roughly 1,000 credits — the cheapest surface that
 * goes down this path. No picture (4,400) and no clip (12,000): the record
 * those two leave is written by the same helper, and the shape of the line
 * under their titles is covered by `tests/test_media_record.py`.
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

const PROMPT = '다음 문장을 읽어줘: 기록 확인용 내레이션입니다.'

test('내레이션 대화는 이름과 결과물을 남긴다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }

  // Skipped where the workspace has this surface off. `image` and `av` spend
  // credits per generation and default to off, and the screen for a surface
  // that is off carries no composer to drive.
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  // The kind control is a dropdown labelled with its current value.
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitem', { name: '오디오' }).click()

  await page.getByLabel('프롬프트 입력').fill(PROMPT)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  const sessionId = page.url().split('/s/')[1]

  const session = async () =>
    (await (await page.request.get(`/api/sessions/${sessionId}`, { headers })).json()) as {
      title: string
      artifactId: string | null
      messages: { role: string; content: string; artifactIds: string[] | null }[]
      messageCount: number
    }

  // Poll the link rather than the title: the title is written the moment the
  // request is accepted and would pass before anything had been made.
  await expect
    .poll(async () => (await session()).artifactId, {
      timeout: 240_000,
      message: '대화가 만들어낸 결과물을 가리키지 않습니다',
    })
    .not.toBeNull()

  const after = await session()
  // Named from the prompt, with no model call: on this surface the prompt is
  // the sentence the person wrote, and there is no reply to summarise.
  expect(after.title).toBe(PROMPT)
  // And the turn itself, which is what makes the conversation readable rather
  // than merely findable. The prompt as the person's own message, and under it
  // an answer holding the clip — with no words in it, because nothing spoke.
  expect(after.messageCount).toBe(2)
  const [asked, answered] = after.messages
  expect(asked.role).toBe('user')
  expect(asked.content).toBe(PROMPT)
  expect(answered.role).toBe('assistant')
  expect(answered.content).toBe('')
  expect(answered.artifactIds).toEqual([after.artifactId])

  const artifact = (await (
    await page.request.get(`/api/artifacts/${after.artifactId}`, { headers })
  ).json()) as { kind: string; sessionId: string }
  expect(artifact.kind).toBe('audio')
  // Both directions now. The gallery could always reach the conversation; the
  // conversation could not reach what it had made.
  expect(artifact.sessionId).toBe(sessionId)
})

test('대화 기록에서 방금 만든 내레이션을 알아볼 수 있다', async ({ page }) => {
  await signIn(page)

  await page.goto('/history')
  const row = page.locator('div').filter({ hasText: PROMPT }).last()
  await expect(row).toBeVisible({ timeout: 20_000 })

  // The second line is not the title again. The title already carries what was
  // asked for; this says what came back, which is the only thing that tells
  // several clips of one request apart.
  await expect(row.getByText(/내레이션/).first()).toBeVisible()
  await expect(row.getByText(/\d+초/).first()).toBeVisible()
})

test('그림 화면을 여는 것만으로는 대화가 생기지 않는다', async ({ page }) => {
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }

  /**
   * Eleven picture and thirteen clip sessions on this account have no artifact
   * at all — abandoned before the first press, or a generation that failed —
   * and an empty row costs the list far more than it costs the database.
   *
   * `/new/:kind` is the entry point that gets this right: it holds the surface
   * open and lets the first prompt create the row. The 프로젝트 and 에이전트
   * screens still create one on navigation, which is where those empty rows
   * come from; naming the correct behaviour here is what keeps this entry
   * point from quietly joining them.
   */
  const count = async () =>
    ((await (await page.request.get('/api/sessions?kind=image', { headers })).json()) as unknown[])
      .length

  const before = await count()
  // Skipped where the workspace has this surface off. `image` and `av` spend
  // credits per generation and default to off, and the screen for a surface
  // that is off carries no composer to drive.
  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  await expect(page).toHaveURL(/\/new\/image$/)
  expect(await count()).toBe(before)
})
