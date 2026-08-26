import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A multi-select menu stays open while rows are ticked. The compare-model
 * list is one: it used to close on every pick, so choosing two models meant
 * opening it twice. (The skill menu has the same fix; it needs a skill to
 * exist, so the model list — always populated — is what is asserted here.)
 */
const model = (id: string, label: string) => ({
  id,
  label,
  name: label.split(' · ')[1] ?? label,
  vendor: label.split(' · ')[0],
  provider: 'openrouter',
  dataBoundary: 'external',
  strictLocal: false,
  privacyOnly: false,
  modality: 'chat',
  kinds: ['chat'],
  creditCost: 7,
  inputCreditCost: 2,
  supportsTools: true,
})

test('비교할 모델을 고르는 동안 메뉴는 열려 있다', async ({ page }) => {
  await signIn(page)
  // The catalogue comes from the proxy; faked so the list is two rows on any
  // instance, with or without a LiteLLM behind it.
  await page.route('**/api/models', (route) =>
    route.fulfill({
      json: {
        models: [model('vendor/one', 'Vendor · One'), model('vendor/two', 'Vendor · Two')],
        litellmAvailable: true,
        defaultChatModel: 'vendor/one',
        autoRouting: {
          enabled: false,
          available: false,
          reason: 'disabled',
          classifierModelId: null,
          economyModelIds: [],
          qualityAvailable: false,
          qualityReason: 'disabled',
          qualityModelIds: [],
        },
      },
    }),
  )
  await page.goto('/new/chat')

  await page.getByRole('button', { name: '모델 비교' }).click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()

  // The model rows are toggles (`checked`), so they are checkboxes; the one
  // command in the menu, 비교 모드, is a plain item.
  const rows = menu.getByRole('menuitemcheckbox')
  await rows.nth(0).click()
  await expect(menu).toBeVisible()
  await rows.nth(1).click()
  await expect(menu).toBeVisible()

  // A command still closes it.
  await menu.getByRole('menuitem').filter({ hasText: '비교 모드' }).click()
  await expect(menu).toHaveCount(0)
})
