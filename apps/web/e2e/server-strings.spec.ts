import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Strings the server writes into response bodies are translated in English mode. */

/** Signs in (the form is found by its Korean labels), then switches to English. */
async function inEnglish(page: import('@playwright/test').Page, path: string) {
  await signIn(page)
  await page.evaluate(() => localStorage.setItem('kchat-lang', 'en'))
  await page.goto(path)
  await page.waitForTimeout(1200)
}

test('사설 대역 표시가 영어 모드에서 번역된다', async ({ page }) => {
  // `services/geoip.py` writes this for RFC 1918 addresses.
  await inEnglish(page, '/settings/access')
  await expect(page.getByText('내부망', { exact: true })).toHaveCount(0)
  // Rows must exist, or the assertion above passes on an empty table.
  await expect(page.getByText('Private network').first()).toBeVisible({ timeout: 15_000 })
})

test('연동 기능 이름이 모두 영어 모드에서 번역된다', async ({ page }) => {
  // Labels come back from `GET /admin/settings`.
  await inEnglish(page, '/admin/system/features')
  for (const korean of ['웹 검색', '문서 가져오기', '코드 실행', '심층 조사', '음성 전사', '자료 검색']) {
    await expect(
      page.getByText(korean, { exact: true }),
      `연동 기능 이름 “${korean}” 이 영어 모드에 그대로 남았습니다`,
    ).toHaveCount(0)
  }
  await expect(page.getByText('Knowledge search').first()).toBeVisible({ timeout: 15_000 })
})
