/**
 * Talking to the document you are looking at.
 *
 * The thing this checks is not a control — it is that a sentence typed in the
 * chat, under a document that already exists, works on that document. Before
 * this, it planned another one and offered to replace the one on screen, which
 * is why working on a report meant the panel's buttons and never the chat.
 *
 * Slow: each case needs a real document first. Kept apart from the faster
 * suites for that reason, and run in order so one generation serves the file.
 */

import { expect, test, type Page } from '@playwright/test'
import { approvePlan, signIn } from './helpers'

test.describe.configure({ mode: 'serial', retries: 1 })
test.setTimeout(600_000)

/**
 * The session the first case makes, so the later ones can open it themselves.
 *
 * These run in order on one page and used to rely on that page still showing
 * what the case before it left — which is true right up until a retry, where
 * the later case starts on a blank page and looks for a panel button that was
 * never drawn. One generation still serves the file; the difference is that
 * each case now says which document it is about.
 */
let sessionId = ''

async function openReport(page: Page) {
  await signIn(page)
  await page.goto('/new/report')
  await page.getByLabel('프롬프트 입력').fill(
    '사내 문서 보관 정책 개선 보고서를 써 줘. 현행 문제와 개선안을 나누고 마지막에 다음 행동을 적어 줘.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await approvePlan(page)
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 480_000 })
  sessionId = page.url().split('/s/')[1] ?? ''
}

/** Reopens the document the first case wrote, without writing another. */
async function reopen(page: Page) {
  expect(sessionId, '첫 사례가 문서를 만들지 못했습니다').not.toBe('')
  await signIn(page)
  await page.goto(`/s/${sessionId}`)
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 60_000 })
  // The revision the case before it started may still be streaming, and a
  // panel mid-run is not the panel these controls belong to.
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 300_000 })
}

/** The section headings the panel is showing, in order. */
async function headings(page: Page): Promise<string[]> {
  return page.locator('article h2').allInnerTexts()
}

test('완성된 문서 아래에 쓴 문장은 그 문서를 고친다', async ({ page }) => {
  await openReport(page)
  const before = await headings(page)
  expect(before.length).toBeGreaterThanOrEqual(3)

  const target = before[before.length - 1]
  await page.getByLabel('프롬프트 입력').fill(`"${target}" 절을 두 문장으로 짧게 줄여 줘.`)
  await page.getByLabel('프롬프트 입력').press('Enter')

  // The step names the part it landed on — the whole point of the routing, and
  // the only thing on screen that proves the instruction was read rather than
  // treated as a new document. The assistant's own sentence is collapsed into
  // the turn's summary button, so asserting on it tests the transcript's
  // rendering rather than the revision.
  const step = page.getByRole('button', { name: /고치는 중/ })
  await expect(step).toBeVisible({ timeout: 300_000 })
  await expect(step).toContainText(target.slice(0, 8))

  // And the document is the same document: no proposal card, same outline.
  await expect(page.getByRole('button', { name: '이대로 생성' })).toBeHidden()
  expect(await headings(page)).toEqual(before)
})

test('고치기 전 판은 저장 시점에 남는다', async ({ page }) => {
  // A revision is destructive in the way a regeneration was; the way back has
  // to exist before anybody trusts typing into the box.
  await reopen(page)
  await page.getByRole('button', { name: '버전 기록' }).click()
  await expect(page.getByText(/v1/).first()).toBeVisible()
})

test('새로 써 달라고 하면 문서를 고치지 않는다', async ({ page }) => {
  await reopen(page)
  const before = await headings(page)
  // Names the new subject. "완전히 다른 주제로 새로 써 줘" alone says what to
  // throw away and nothing about what to write, and the planner answered it
  // exactly as it should — 요청을 조금 더 구체적으로 적어 주세요 — which is a
  // fair answer to a fixture that asked for nothing. The claim under test is
  // about routing, not about how little a request can say.
  await page
    .getByLabel('프롬프트 입력')
    .fill('이건 버리고, 연구실 안전 교육 계획으로 완전히 새로 써 줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')

  // Planning again, which means a proposal to look at — not a document
  // silently replaced.
  //
  // Waited on the proposal alone. This used to accept "N곳을 고쳤습니다" as the
  // other half of an `.or()`, and that sentence is in the transcript already:
  // the case above put it there. So the wait passed on an old message, the
  // moment the screen loaded, and the assertion below then looked for a card
  // that a turn one second old had not drawn yet. The hedge was hiding the
  // very thing this checks.
  //
  // Nothing here is left to a model: `revise.obviously_new` reads "새로 써 줘"
  // and refuses to route it as an edit before any call is made.
  await expect(page.getByRole('button', { name: '이대로 생성' })).toBeVisible({
    timeout: 300_000,
  })

  // And the document on screen is still the one that was there — a plan is an
  // offer, and nothing is written until somebody takes it.
  expect(await headings(page)).toEqual(before)
})
