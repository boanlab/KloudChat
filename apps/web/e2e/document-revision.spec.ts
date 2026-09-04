/** A sentence typed under an existing document revises that document. Serial: one generation serves the file. */

import { expect, test, type Page } from '@playwright/test'
import { approvePlan, artifactReady, ribbonTab, signIn } from './helpers'

test.describe.configure({ mode: 'serial', retries: 1 })
test.setTimeout(600_000)

/** The session the first case makes; later cases reopen it by id (a retry starts on a blank page). */
let sessionId = ''

async function openReport(page: Page) {
  await signIn(page)
  await page.goto('/new/report')
  await page.getByLabel('프롬프트 입력').fill(
    '사내 문서 보관 정책 개선 보고서를 써 줘. 현행 문제와 개선안을 나누고 마지막에 다음 행동을 적어 줘.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await approvePlan(page)
  await artifactReady(page)
  sessionId = page.url().split('/s/')[1] ?? ''
}

/** Reopens the document the first case wrote, without writing another. */
async function reopen(page: Page) {
  expect(sessionId, '첫 사례가 문서를 만들지 못했습니다').not.toBe('')
  await signIn(page)
  await page.goto(`/s/${sessionId}`)
  await artifactReady(page, 60_000)
  // The previous case's revision may still be streaming.
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 300_000 })
}

/** The section headings the panel is showing, in order. */
async function headings(page: Page): Promise<string[]> {
  // Panel only: the transcript is articles too, and a summary may quote a heading.
  return page.locator('[data-panel="artifact"] article h2').allInnerTexts()
}

test('완성된 문서 아래에 쓴 문장은 그 문서를 고친다', async ({ page }) => {
  await openReport(page)
  const before = await headings(page)
  expect(before.length).toBeGreaterThanOrEqual(3)

  const target = before[before.length - 1]
  await page.getByLabel('프롬프트 입력').fill(`"${target}" 절을 두 문장으로 짧게 줄여 줘.`)
  await page.getByLabel('프롬프트 입력').press('Enter')

  // The step names the section it landed on.
  const step = page.getByRole('button', { name: /고치는 중/ })
  await expect(step).toBeVisible({ timeout: 300_000 })
  // 「3. 」 is the panel's numbering; the step names the section as stored.
  await expect(step).toContainText(target.replace(/^\d+\.\s*/, '').slice(0, 8))

  // Same document: no proposal card, same outline.
  await expect(page.getByRole('button', { name: '이대로 생성' })).toBeHidden()
  expect(await headings(page)).toEqual(before)
})

test('고치기 전 판은 저장 시점에 남는다', async ({ page }) => {
  await reopen(page)
  await ribbonTab(page, '검토')
  await page.getByRole('button', { name: '버전 기록' }).click()
  await expect(page.getByText(/v1/).first()).toBeVisible()
})

test('새로 써 달라고 하면 문서를 고치지 않는다', async ({ page }) => {
  await reopen(page)
  const before = await headings(page)
  // Names a new subject, so the planner has something to plan.
  await page
    .getByLabel('프롬프트 입력')
    .fill('이건 버리고, 연구실 안전 교육 계획으로 완전히 새로 써 줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')

  // A proposal, not a silent replacement: `revise.obviously_new` routes "새로 써 줘" before any model call.
  await expect(page.getByRole('button', { name: '이대로 생성' })).toBeVisible({
    timeout: 300_000,
  })

  // A plan is an offer; nothing is written until it is taken.
  expect(await headings(page)).toEqual(before)
})
