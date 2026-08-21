import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The clip chips and the model that has to honour them.
 *
 * Audio and video share one surface and one remembered model, so turning 종류
 * to 영상 changes the model underneath — while 해상도 and 소리 stay where the
 * last clip left them. When the new model does not price that combination the
 * composer refuses the turn, and the refusal is the first thing the person
 * hears about a combination the product chose for them. The chips are expected
 * to follow the model instead.
 */

const speech = {
  id: 'test/speech',
  label: 'Test · Speech',
  name: 'Speech',
  vendor: 'Test',
  provider: 'openrouter',
  dataBoundary: 'external',
  strictLocal: false,
  privacyOnly: false,
  modality: 'audio',
  kinds: ['av'],
  creditCost: 1,
  inputCreditCost: 0,
  creditPerCall: 500,
  supportsTools: false,
  description: '',
}

// Sound is the only shape it is priced for, exactly as Sora is in the live
// catalogue: there is no `720p:silent` to fall back to.
const soundOnlyVideo = {
  ...speech,
  id: 'test/sound-only',
  label: 'Test · Sound Only',
  name: 'Sound Only',
  modality: 'video',
  creditCost: 9,
  creditPerCall: 0,
  creditPerSecond: { '720p:sound': 300, '1080p:sound': 500 },
}

test('종류를 영상으로 바꾸면 그 모델이 만들 수 있는 조합으로 맞춰진다', async ({ page }) => {
  // The clip surface is optional, so say what this test needs rather than
  // skipping on an instance that happens to have it switched off.
  await page.route('**/api/auth/config', async (route) => {
    const config = await (await route.fetch()).json()
    await route.fulfill({
      json: {
        ...config,
        enabledKinds: [...new Set([...(config.enabledKinds ?? ['chat']), 'av'])],
      },
    })
  })
  await page.route('**/api/models', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: [speech, soundOnlyVideo],
        litellmAvailable: true,
        defaultChatModel: '',
      }),
    })
  })
  await signIn(page)
  await page.goto('/new/av')

  // 영상 on arrival, without a trip through 오디오 first. The remembered av
  // model is the cheapest of them, which is a speech model, so the surface has
  // to pair mode and model itself or it opens quoting a clip against a model
  // that sells none.
  await expect(page.getByRole('button', { name: /Sound Only/ })).toBeVisible()
  await expect(page.getByText('예상 1,200 크레딧')).toBeVisible()

  // And out and back, which is the other way the model changes underneath the
  // chips.
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitem', { name: '오디오' }).click()
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitem', { name: '영상' }).click()

  await expect(page.getByRole('button', { name: /Sound Only/ })).toBeVisible()
  // 소리 opened on 없음 — the composer's own default — and this model has no
  // silent price at all. The chip moves rather than the turn being refused.
  await expect(page.getByRole('button', { name: /소리.*있음/ })).toBeVisible()
  // Resolution is given up last: 720p was already priced, so it stays.
  await expect(page.getByRole('button', { name: /해상도.*720p/ })).toBeVisible()
  // The refusal must never be the first thing on screen. There is a price on
  // screen instead: 4 seconds at 300 credits a second.
  await expect(page.getByText('이 모델은 이 조합을 만들지 않습니다')).toHaveCount(0)
  await expect(page.getByText('예상 1,200 크레딧')).toBeVisible()
})
