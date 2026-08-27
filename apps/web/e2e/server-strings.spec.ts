import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Interface strings the *server* writes, read in English.
 *
 * Most of what a screen says comes from a call site, where `t()` is right
 * there and a missing translation is visible while writing it. These do not:
 * they arrive in a response body and are rendered as data, so nothing at the
 * call site suggests a dictionary entry is needed and nothing at the server
 * suggests one exists.
 *
 * Narrow on purpose. `i18n-audit.spec.ts` is the sweep; this pins the specific
 * strings that reached the screen untranslated, on the screens they reach it
 * on, so a regression names itself instead of arriving in a list of a hundred.
 */

/**
 * Signed in, then switched to English.
 *
 * That order, not the other way round: `signIn` finds the form by its Korean
 * labels, so planting the language first leaves it looking for 로그인 on a
 * button that says Sign in. The store reads `kchat-lang` when it initialises,
 * and `page.goto` is a full navigation, so the language is in force by the
 * time the screen renders.
 */
async function inEnglish(page: import('@playwright/test').Page, path: string) {
  await signIn(page)
  await page.evaluate(() => localStorage.setItem('kchat-lang', 'en'))
  await page.goto(path)
  await page.waitForTimeout(1200)
}

test('사설 대역 표시가 영어 모드에서 번역된다', async ({ page }) => {
  // `services/geoip.py` says this in place of a place name for an address
  // inside RFC 1918 — which, on an instance behind a proxy on the same host,
  // is every row. Three screens show a region; this is the one an ordinary
  // account can reach.
  await inEnglish(page, '/settings/access')
  await expect(page.getByText('내부망', { exact: true })).toHaveCount(0)
  // The rows have to be there, or the assertion above passes on an empty table.
  await expect(page.getByText('Private network').first()).toBeVisible({ timeout: 15_000 })
})

test('연동 기능 이름이 모두 영어 모드에서 번역된다', async ({ page }) => {
  // Six labels come back from `GET /admin/settings`; five had an entry and one
  // did not, which is the failure this shape of string invites — nothing lines
  // them up, so a sixth is added years after the other five.
  await inEnglish(page, '/admin/system/features')
  for (const korean of ['웹 검색', '문서 가져오기', '코드 실행', '심층 조사', '음성 전사', '자료 검색']) {
    await expect(
      page.getByText(korean, { exact: true }),
      `연동 기능 이름 “${korean}” 이 영어 모드에 그대로 남았습니다`,
    ).toHaveCount(0)
  }
  await expect(page.getByText('Knowledge search').first()).toBeVisible({ timeout: 15_000 })
})
