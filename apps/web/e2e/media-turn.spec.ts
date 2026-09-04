import { expect, test, type Page } from '@playwright/test'
import { signIn, surfaceOn } from './helpers'

/** The image surface reads as a conversation: the prompt stays, the picture sits under it, a failure is a failed turn.
 *  Generation is stubbed with an existing picture. */

/** A bearer token of the test's own; the app holds its token in memory. */
async function token(page: Page) {
  return page.evaluate(async () => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    return (await res.json()).accessToken as string
  })
}

const PROMPT = '흰 배경에 놓인 파란 자물쇠'

type Row = { id: string; kind: string; title: string; data: { src: string } }

/** A picture this account already paid for, on an earlier run. */
async function existingPicture(page: Page): Promise<Row | null> {
  const headers = { Authorization: `Bearer ${await token(page)}` }
  const body = await (await page.request.get('/api/artifacts', { headers })).json()
  const rows: Row[] = Array.isArray(body) ? body : (body?.items ?? [])
  return rows.find((a) => a.kind === 'image') ?? null
}

/** Answers generation with `picture` and the conversation re-read with the turn the server would write. */
async function stubGeneration(page: Page, picture: Row) {
  await page.route('**/api/sessions/*/images', async (route) => {
    await route.fulfill({ json: [picture] })
  })
  await page.route(/\/api\/sessions\/[0-9a-f]{32}$/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch()
    const session = await response.json()
    const now = new Date().toISOString()
    session.messages = [
      { id: 'm1', role: 'user', content: PROMPT, artifactIds: null, createdAt: now },
      {
        id: 'm2',
        role: 'assistant',
        content: '',
        artifactIds: [picture.id],
        usage: { credits: 4400 },
        createdAt: now,
      },
    ]
    session.messageCount = 2
    session.artifactId = picture.id
    await route.fulfill({ json: session })
  })
}

test('그림은 프롬프트 아래, 대화 안에 나온다', async ({ page }) => {
  await signIn(page)
  const picture = await existingPicture(page)
  test.skip(!picture, '이미지 아티팩트가 없습니다')
  await stubGeneration(page, picture!)

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  await page.getByLabel('프롬프트 입력').fill(PROMPT)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The prompt stays in the conversation.
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })

  // The picture is in the transcript, not only the panel.
  const inTranscript = page.locator('img[src*="/api/files/"]').first()
  await expect(inTranscript).toBeVisible({ timeout: 20_000 })

  // The panel does not open by itself.
  await expect(page.getByRole('button', { name: '이미지 열기' })).toBeVisible()
  await inTranscript.click()
  await expect(page.getByRole('button', { name: '이미지 열기' })).toHaveCount(0)

  // After a reload the stored turn is what is drawn.
  await page.reload()
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('img[src*="/api/files/"]').first()).toBeVisible({ timeout: 20_000 })
})

test('만들지 못하면 그 프롬프트가 실패한 차례로 남는다', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/sessions/*/images', async (route) => {
    await route.fulfill({ status: 502, json: { detail: '모델이 요청을 거절했습니다.' } })
  })

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  await page.getByLabel('프롬프트 입력').fill(PROMPT)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // A failed turn, not an empty conversation.
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })
  // The upstream's own words while the tab lives.
  await expect(page.getByText('모델이 요청을 거절했습니다.')).toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible()

  // After a reload the stored `failure` mark speaks, in the product's own words.
  await page.route(/\/api\/sessions\/[0-9a-f]{32}$/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const session = await (await route.fetch()).json()
    session.messages = [
      {
        id: 'm1',
        role: 'user',
        content: PROMPT,
        artifactIds: null,
        failure: 'no_answer',
        createdAt: new Date().toISOString(),
      },
    ]
    session.messageCount = 1
    await route.fulfill({ json: session })
  })
  await page.reload()
  await expect(page.getByText('만들지 못했습니다.')).toBeVisible({ timeout: 20_000 })
})
