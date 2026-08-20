import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * 이미지 화면이 대화처럼 읽히는지.
 *
 * 프롬프트는 사라지고 결과는 옆 패널에만 나타나던 화면이었다. 사람이 쓴 문장은
 * 자기가 쓴 자리에 남아야 하고, 그림은 그 문장 아래에 있어야 한다.
 *
 * A picture is 4,400 credits, so nothing here asks for one: the generation call
 * is answered by the test with an artifact that already exists in the fixture
 * account, and the conversation the app re-reads afterwards is answered with
 * the two rows the server would have written. What is under test is the
 * transcript, not the model.
 */

/** The API takes a bearer token the app holds in memory, so a test asking for
 *  one of its own is the only way to reach the API beside the app. */
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

/**
 * Answers the generation call with `picture`, and the re-read of the
 * conversation with the turn the server writes for it: the prompt as a user
 * message, and a wordless assistant message carrying the artifact's id.
 */
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

  await page.goto('/new/image')
  await page.getByLabel('프롬프트 입력').fill(PROMPT)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // 사람이 쓴 문장이 대화에 남는다. 예전에는 제목 줄에만 있었다.
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })

  // 그리고 그 아래에 그림이 있다 — 패널이 아니라 대화 안에.
  const inTranscript = page.locator('img[src*="/api/files/"]').first()
  await expect(inTranscript).toBeVisible({ timeout: 20_000 })

  // 패널은 저절로 열리지 않는다. 같은 그림을 두 번 보여 주면서 대화를 3분의 1로
  // 밀어내는 대신, 열고 싶을 때 열도록 버튼을 남겨 둔다.
  await expect(page.getByRole('button', { name: '이미지 열기' })).toBeVisible()
  await inTranscript.click()
  await expect(page.getByRole('button', { name: '이미지 열기' })).toHaveCount(0)

  /**
   * And again after a reload, which is the whole reason the turn is stored
   * rather than drawn from what this tab happens to remember. The stubbed read
   * is the conversation the server holds — before this change it held nothing,
   * and a reload gave a blank screen with a panel beside it.
   */
  await page.reload()
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('img[src*="/api/files/"]').first()).toBeVisible({ timeout: 20_000 })
})

test('만들지 못하면 그 프롬프트가 실패한 차례로 남는다', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/sessions/*/images', async (route) => {
    await route.fulfill({ status: 502, json: { detail: '모델이 요청을 거절했습니다.' } })
  })

  await page.goto('/new/image')
  await page.getByLabel('프롬프트 입력').fill(PROMPT)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // 빈 대화가 아니라 실패한 차례. 무엇을 요청했는지가 남아 있어야 다시 시도할
  // 수 있고, 크레딧이 왜 그대로인지도 그 자리에서 읽힌다.
  await expect(page.getByText(PROMPT).first()).toBeVisible({ timeout: 20_000 })
  // 살아 있는 동안은 상대가 한 말 그대로.
  await expect(page.getByText('모델이 요청을 거절했습니다.')).toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible()

  /**
   * And after a reload it is the stored mark that speaks, in the product's own
   * words — the browser's copy of the upstream sentence lives in one tab and
   * is gone. Without the column the same conversation would come back empty.
   */
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
