import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn, surfaceOn } from './helpers'

/** A media session leaves a title, a turn and an artifact link. One narration per run (about 1,000 credits);
 *  pictures and clips share the helper, covered in `tests/test_media_record.py`. */

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

const PROMPT = '다음 문장을 읽어줘: 기록 확인용 내레이션입니다.'

test('내레이션 대화는 이름과 결과물을 남긴다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }

  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '오디오' }).click()

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

  // Poll the artifact link: the title is written as soon as the request is accepted.
  await expect
    .poll(async () => (await session()).artifactId, {
      timeout: 240_000,
      message: '대화가 만들어낸 결과물을 가리키지 않습니다',
    })
    .not.toBeNull()

  const after = await session()
  // Named from the prompt; there is no reply to summarise.
  expect(after.title).toBe(PROMPT)
  // The turn: the prompt as a user message, and a wordless answer holding the clip.
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
  // Linked in both directions.
  expect(artifact.sessionId).toBe(sessionId)
})

test('대화 기록에서 방금 만든 내레이션을 알아볼 수 있다', async ({ page }) => {
  await signIn(page)
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')

  await page.goto('/history')
  const row = page.locator('div').filter({ hasText: PROMPT }).last()
  await expect(row).toBeVisible({ timeout: 20_000 })

  // The second line says what came back, not the title again.
  await expect(row.getByText(/내레이션/).first()).toBeVisible()
  await expect(row.getByText(/\d+초/).first()).toBeVisible()
})

test('그림 화면을 여는 것만으로는 대화가 생기지 않는다', async ({ page }) => {
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }

  // `/new/:kind` lets the first prompt create the row.
  const count = async () =>
    ((await (await page.request.get('/api/sessions?kind=image', { headers })).json()) as unknown[])
      .length

  const before = await count()
  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  await expect(page).toHaveURL(/\/new\/image$/)
  expect(await count()).toBe(before)
})
