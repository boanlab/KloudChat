import { expect, test, type Page } from '@playwright/test'
import { surfaceOn } from './helpers'
import { composerFixture } from './composer-fixture'

/** 취소 on a clip job reaches the server, and a failed cancel leaves the card running. Job stubbed. */

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

/** The session id the stubbed rows must carry, or the page filters them away. */
function sessionIdOf(page: Page) {
  return new URL(page.url()).pathname.split('/').pop() ?? ''
}

const veo = {
  id: 'fixture/veo-lite', label: 'Veo 3.1 Lite', name: 'Veo 3.1 Lite', vendor: 'Google', provider: 'fixture',
  dataBoundary: 'external', strictLocal: false, privacyOnly: false, modality: 'video', kinds: ['av'],
  creditCost: 0, inputCreditCost: 0, creditPerSecond: { '720p:silent': 3000, '720p:sound': 4000 },
  supportsTools: false, description: '',
}

/** Puts a stubbed running clip on screen. Returns false when the av surface is off. */
async function startStubbedJob(page: Page): Promise<boolean> {
  // A priced video model, the admin's default: an unpriced shape is refused in the composer.
  await page.route('**/api/models', (route) => route.fulfill({ json: {
    models: [veo], litellmAvailable: true, defaultChatModel: '',
    defaultModelByKind: { av: veo.id }, defaultAvModelByMode: { video: veo.id },
  } }))
  await page.route('**/api/sessions/*/jobs', async (route) => {
    const parts = new URL(route.request().url()).pathname.split('/')
    const row = jobRow(parts[parts.length - 2])
    // The poll is answered with the same row.
    await route.fulfill({ json: route.request().method() === 'POST' ? row : [row] })
  })

  if (!(await surfaceOn(page, 'av'))) return false
  await page.getByRole('button', { name: /^해상도/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '720p' }).click()
  await page.keyboard.press('Escape')

  await expect(page.getByRole('button', { name: /Veo 3\.1 Lite/ })).toBeVisible()
  await expect(page.getByText(/예상 [\d,]+ 크레딧/).first()).toBeVisible({ timeout: 15_000 })

  await page.getByLabel('프롬프트 입력').fill('취소 확인용')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  await expect(page.getByText('만드는 중').first()).toBeVisible({ timeout: 30_000 })
  return true
}

test('취소는 서버까지 간다', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await composerFixture(page, testInfo)
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

test('취소가 실패하면 카드는 되던 대로 돌아간다', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await composerFixture(page, testInfo)
  test.skip(!(await startStubbedJob(page)), 'av 표면이 꺼져 있습니다')

  await page.route(`**/api/jobs/${JOB_ID}/cancel`, async (route) => {
    await route.fulfill({ status: 500, json: { detail: 'upstream_failed' } })
  })

  await page.getByRole('button', { name: '취소', exact: true }).first().click()

  // Still being made and still to be charged, so the card must not claim it stopped.
  await expect(page.getByText('작업을 취소하지 못했습니다.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('만드는 중').first()).toBeVisible()
  await expect(page.getByText('취소됨')).toHaveCount(0)
})
