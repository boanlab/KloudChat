import { expect, test, type Page } from '@playwright/test'
import { signIn, surfaceOn } from './helpers'

/**
 * '취소' has to reach the server.
 *
 * Rewriting the card locally is not cancelling: the clip goes on being made
 * upstream, is charged on delivery, and comes back as 만드는 중 on reload.
 *
 * The job is stubbed rather than generated — a real clip is 12,000 credits and
 * several minutes, and what is under test is the call, not the video.
 */

const JOB_ID = 'cafe0000000000000000000000000001'

function jobRow(sessionId: string, over: Record<string, unknown> = {}) {
  return {
    id: JOB_ID,
    sessionId,
    kind: 'av',
    status: 'running',
    progress: 20,
    stage: '만드는 중',
    creditsUsed: 0,
    creditsEstimated: 12000,
    error: null,
    artifactId: null,
    createdAt: new Date().toISOString(),
    finishedAt: null,
    prompt: '취소 확인용',
    model: '',
    params: { resolution: '720p', seconds: 4, audio: false, aspect: '16:9' },
    ...over,
  }
}

/** The conversation the card is rendered under, which the stubbed rows have to
 *  carry or the page filters them away. */
function sessionIdOf(page: Page) {
  return new URL(page.url()).pathname.split('/').pop() ?? ''
}

/**
 * Puts a running clip on the screen without asking any provider for one. The
 * poll that follows the card is refused rather than answered, so nothing but
 * the store itself decides what the card says next.
 */
async function startStubbedJob(page: Page): Promise<boolean> {
  await page.route('**/api/sessions/*/jobs', async (route) => {
    const parts = new URL(route.request().url()).pathname.split('/')
    const row = jobRow(parts[parts.length - 2])
    // The poll that follows the card is answered with the same row rather than
    // refused: refusing it threw inside the turn that creates the session, and
    // the screen never got as far as the card this test is about.
    await route.fulfill({ json: route.request().method() === 'POST' ? row : [row] })
  })

  // The clip surface, when this workspace has it on. It spends credits per
  // generation and defaults to off, and the screen for a surface that is off
  // carries no option chips to press.
  if (!(await surfaceOn(page, 'av'))) return false
  await page.getByRole('button', { name: /^해상도/ }).click()
  await page.getByRole('menuitem', { name: '720p' }).click()
  await page.keyboard.press('Escape')

  // The model is named rather than left to whatever the account last used:
  // every (model × resolution × sound × length) is priced separately and an
  // unlisted combination is refused in the composer, so a test about
  // cancelling would otherwise fail for having nothing to start.
  await page.getByRole('button', { name: /Veo/ }).first().click()
  await page.getByRole('button', { name: /Veo 3\.1 Lite/ }).first().click()
  await expect(page.getByText(/예상 [\d,]+ 크레딧/).first()).toBeVisible({ timeout: 15_000 })

  await page.getByLabel('프롬프트 입력').fill('취소 확인용')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  await expect(page.getByText('만드는 중').first()).toBeVisible({ timeout: 30_000 })
  return true
}

test('취소는 서버까지 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  test.skip(!(await startStubbedJob(page)), 'av 표면이 꺼져 있습니다')

  await page.route(`**/api/jobs/${JOB_ID}/cancel`, async (route) => {
    await route.fulfill({
      json: jobRow(sessionIdOf(page), {
        status: 'canceled',
        stage: '취소됨',
        finishedAt: new Date().toISOString(),
      }),
    })
  })

  const cancelled = page.waitForRequest(
    (r) => r.url().includes(`/jobs/${JOB_ID}/cancel`) && r.method() === 'POST',
    { timeout: 15_000 },
  )
  await page.getByRole('button', { name: '취소', exact: true }).first().click()
  await cancelled

  await expect(page.getByText('취소됨').first()).toBeVisible({ timeout: 15_000 })
})

test('취소가 실패하면 카드는 되던 대로 돌아간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  test.skip(!(await startStubbedJob(page)), 'av 표면이 꺼져 있습니다')

  await page.route(`**/api/jobs/${JOB_ID}/cancel`, async (route) => {
    await route.fulfill({ status: 500, json: { detail: 'upstream_failed' } })
  })

  await page.getByRole('button', { name: '취소', exact: true }).first().click()

  // The clip is still being made and will still be charged on delivery, so the
  // card must not sit there claiming it was stopped.
  await expect(page.getByText('작업을 취소하지 못했습니다.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('만드는 중').first()).toBeVisible()
  await expect(page.getByText('취소됨')).toHaveCount(0)
})
