import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('검증을 명시한 요청은 토글을 모르더라도 출처를 실제로 찾는다', async ({ page }) => {
  test.setTimeout(300_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/chat')

  const request =
    '"한국 청소년의 스마트폰 과의존 비율이 40%를 넘는다"는 보도를 검증해 주세요. ' +
    '조사 주체, 정의, 표본, 연도를 확인하고 다른 조사와 비교해 주세요.'
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill(request)
  await composer.press('Enter')
  await page.getByLabel('중지').waitFor({ state: 'visible', timeout: 45_000 })
  await page.getByLabel('중지').waitFor({ state: 'hidden', timeout: 240_000 })

  const main = page.locator('main')
  const answer = (await main.innerText()).split(request).join(' ')
  console.log(`\n===== 명시적 검증 결과 =====\n${answer.slice(-2200)}`)
  expect(answer).toMatch(/조사|표본/)
  expect(answer).not.toMatch(/2023년[^.\n]{0,80}12\.8%|12\.8%[^.\n]{0,80}2023년/)
  const sourceLinks = await main.locator('a[href^="http"]').evaluateAll((links) =>
    links.map((link) => (link as HTMLAnchorElement).href),
  )
  expect(sourceLinks.some((href) => new URL(href).pathname !== '/'), '직접 출처 링크').toBe(true)
})
