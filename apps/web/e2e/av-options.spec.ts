import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Switching 종류 changes the av model; the 해상도/소리 chips follow the model's priced combinations. */

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

// Priced for sound only: no `720p:silent` to fall back to.
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
  // Force the av surface on.
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

  // 영상 on arrival: the remembered (cheapest) av model is a speech model, so mode and model are paired.
  await expect(page.getByRole('button', { name: /Sound Only/ })).toBeVisible()
  await expect(page.getByText('예상 1,200 크레딧')).toBeVisible()

  // Out and back. Kind rows are `menuitemcheckbox` (they carry a tick).
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '오디오' }).click()
  await expect(page.getByRole('button', { name: /^종류\s*오디오/ })).toBeVisible()
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '영상' }).click()
  await expect(page.getByRole('button', { name: /^종류\s*영상/ })).toBeVisible()

  await expect(page.getByRole('button', { name: /Sound Only/ })).toBeVisible()
  // The composer's default 소리 is 없음; this model has no silent price, so the chip moves.
  await expect(page.getByRole('button', { name: /소리.*있음/ })).toBeVisible()
  // 720p is already priced, so it stays.
  await expect(page.getByRole('button', { name: /해상도.*720p/ })).toBeVisible()
  // No refusal; a price instead: 4 seconds at 300 credits a second.
  await expect(page.getByText('이 모델은 이 조합을 만들지 않습니다')).toHaveCount(0)
  await expect(page.getByText('예상 1,200 크레딧')).toBeVisible()
})
