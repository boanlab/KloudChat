import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('행위자가 바뀌면 사실이 바뀐다 — 전자세금계산서 역발행', async ({ page }) => {
  test.setTimeout(240_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/chat')

  const request =
    '전자세금계산서 역발행을 처음 하는 경리 직원에게 설명해 주세요. ' +
    '누가 초안을 작성하고, 누가 승인하며, 법적으로 누가 발급하는지를 구분해 주세요.'
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill(request)
  await composer.press('Enter')
  await page.getByLabel('중지').waitFor({ state: 'visible', timeout: 45_000 })
  await page.getByLabel('중지').waitFor({ state: 'hidden', timeout: 180_000 })

  const main = (await page.locator('main').innerText()).split(request).join(' ')
  console.log(`\n===== 혼합형 정확성 계약 결과 =====\n${main.slice(-1800)}`)
  expect(main).toMatch(/매입자|공급받는 자|구매자|수요자/)
  expect(main).toMatch(/(공급자|매도자)[\s\S]{0,120}(확인|승인|발급)|(확인|승인|발급)[\s\S]{0,120}(공급자|매도자)/)
  expect(main).toMatch(/법적[^.\n]{0,80}(공급자|매도자)|(공급자|매도자)[^.\n]{0,80}법적/)
  expect(main).not.toMatch(/법적 발급자[^.\n]{0,30}국세청|국세청[^.\n]{0,30}법적 발급자/)
  expect(main).not.toMatch(/(구매자|수요자|매입자|공급받는 자)[^.\n]{0,30}(세금계산서|계산서)(를|가)?\s*발급/)
  // A named source without a link or a visible search result is fabricated
  // authority, even when the underlying sentence happens to be right.
  if (/출처\s*:/.test(main)) expect(main).toMatch(/https?:\/\//)
  if (/법\s*제\s*\d+\s*조|법\s*제[0-9]+조/.test(main)) expect(main).toMatch(/https?:\/\//)
})
