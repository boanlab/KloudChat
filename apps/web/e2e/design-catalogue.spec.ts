import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The catalogue as a place, rather than as a modal per surface.
 *
 * `design-templates.spec.ts` walks one 서식 from the card to the file. This
 * one is about finding it at all: while the catalogue was only a button inside
 * a session, 회의록 was discoverable by opening a report and 제안 덱 by
 * opening a deck, so the sixteen shapes the product ships were reachable and
 * the catalogue was not.
 *
 * Nothing here generates anything, so no credits: the assertions stop at the
 * composer, which is where a person choosing a shape stops too.
 */

test('디자인 화면은 만드는 것과 제품이 주는 것을 탭으로 나눠 놓는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // The half that was already here is still the one you land on.
  await page.goto('/designs')
  const tabs = page.getByRole('tablist')
  await expect(tabs.getByRole('tab', { name: '디자인 시스템', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByRole('region', { name: '디자인 시스템' })).toBeVisible({
    timeout: 20_000,
  })

  await tabs.getByRole('tab', { name: '서식', exact: true }).click()
  // In the address, so the home rail can point at this half and so a link to
  // it survives being sent to somebody.
  await expect(page).toHaveURL(/\/designs\?tab=template/)

  // ── the whole point ─────────────────────────────────────────────────
  // Two shapes that belong to two different surfaces, on one screen.
  const documents = page.getByRole('region', { name: '보고서' })
  const decks = page.getByRole('region', { name: '슬라이드' })
  await expect(documents.getByText('회의록', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(decks.getByText('제안 덱', { exact: true })).toBeVisible()

  // Grouped by surface: a deck and a one-pager are not two versions of one
  // thing, and one flat grid of sixteen would say they are.
  await expect(decks.getByText('회의록', { exact: true })).toHaveCount(0)

  // ── and it is the gallery's own card ────────────────────────────────
  const minutes = documents.locator('div.group', { hasText: '회의록' })
  await expect(minutes).toHaveCount(1)
  // What it asks you to bring…
  await expect(minutes.getByText('일시와 참석자')).toBeVisible()
  // …and what the finished document will be read against, which is the only
  // thing that tells two shapes of one kind apart.
  // Folded, and saying how many. The list is the tallest thing on a card and
  // open it made a page of four cards taller than the screen; a reader
  // choosing a 서식 needs to know it has rules and how many, not read eight.
  const checks = minutes.locator('summary')
  await expect(checks).toHaveText(/확인하는 것 \d+개/)
  await expect(minutes.locator('li').first()).toBeHidden()
  await checks.click()
  await expect(minutes.locator('li').first()).toBeVisible()

  // ── and it starts the right screen ──────────────────────────────────
  // There is no session yet, so the button opens the surface first and the
  // sentence is waiting in the composer when it does.
  await minutes.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page).toHaveURL(/\/new\/report/, { timeout: 20_000 })
  // The chip, not a sentence. A document 서식 no longer types its example into
  // the box — the chip says which shape, and the words in the box stay the
  // person's own.
  await expect(page.getByText('회의록', { exact: true })).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})

test('서식 탭은 주소로 바로 열리고, 홈의 줄은 몇 개만 보여 준 뒤 넘긴다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/designs?tab=template')
  await expect(page.getByRole('tab', { name: '서식', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
    { timeout: 20_000 },
  )
  // Counted by cards. It used to count preview frames — every shipped shape
  // had one — and the previews went with the seeds they were drawn from.
  //
  // `div.group` alone counts every element wearing that class, nested ones
  // included, and reported 57 where there are 17 — which then made the rail's
  // "17종 모두 보기" look wrong instead of the count.
  // Count by the card's public action, not Tailwind structure. The gallery
  // changed its card markup without changing what a user can do.
  const anyCard = page.getByRole('button', { name: '이 서식으로 시작', exact: true })
  await expect(anyCard.first()).toBeVisible({ timeout: 20_000 })
  const all = await anyCard.count()
  expect(all).toBeGreaterThan(6)

  // ── the front door ──────────────────────────────────────────────────
  // It was a rail on the home screen showing eight of these, then a 결과 서식
  // tab beside 업무 시작점. Both are gone: 작업 시작하기 asks one question, and
  // a 시작점 brings the shape its job comes in. So the front door offers jobs,
  // and the shapes ride along named on the cards.
  await page.goto('/new/slides')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  const offered = dialog.getByRole('button', { name: /시작점 선택$/ })
  await expect(offered.first()).toBeVisible({ timeout: 20_000 })
  const shown = await offered.count()
  expect(shown).toBeGreaterThan(0)
  expect(shown).toBeLessThan(all)
  await page.keyboard.press('Escape')

  // The whole catalogue is still its own screen, and every shape is on it —
  // for anybody who wants to browse rather than to start.
  await page.goto('/designs?tab=template')
  await expect(
    page.getByRole('button', { name: '이 서식으로 시작', exact: true }),
  ).toHaveCount(all, { timeout: 20_000 })
})
