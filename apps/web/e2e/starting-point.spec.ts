import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A 시작점 is carried by the turn, not typed into it.
 *
 * Picking a card used to paste the template's whole framing into the composer
 * — "업무·기술 보고서가 필요하다. 확인되지 않은 수치는…" — and the person
 * added six words on the end and sent it. The transcript then said all of it
 * in their voice, and a year later nobody could tell which half was whose.
 *
 * So what this walks is the seam: the box stays empty, a chip says where the
 * asking started, the request carries an id instead of a paragraph, and the
 * line above the bubble names the template rather than quoting it.
 *
 * The catalogue is stubbed rather than read from the seed. What is being
 * tested is the mechanism, and pinning it to a shipped template's wording
 * would make every edit to that wording a failing test.
 */

const CATALOGUE = [
  {
    id: 't_e2e_debug',
    kind: 'chat',
    group: '개발',
    title: '장애 원인 좁히기',
    description: '스택 트레이스에서 가설과 확인 방법까지',
    fills: ['에러 로그', '재현 조건'],
    prompt: '이 에러의 원인을 좁혀야 한다. 확신이 없는 것은 확인할 방법을 알려 줘.\n\n에러: ',
  },
]

test('시작점은 입력창을 채우지 않고 요청에 실려 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.route('**/api/prompt-templates', (route) =>
    route.fulfill({ json: CATALOGUE }),
  )

  // A fake stream: this is about what leaves the composer, and waiting on a
  // model would make it a test of the model.
  const sent: { content: string; startingTemplateId?: string }[] = []
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify({ type: 'delta', text: '먼저 로그를 보겠습니다.' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  const card = page.getByRole('dialog').locator('div.group', { hasText: '장애 원인 좁히기' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  // What the click will do, said on the card that does it.
  await expect(card.getByText('시작점으로 붙이기')).toBeVisible()
  await card.getByRole('button').first().click()

  // ── the composer is left alone ──────────────────────────────────────
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('')
  // …and asks for exactly what the card said you had to bring, with the
  // particle that belongs on the last of them.
  await expect(box).toHaveAttribute('placeholder', '에러 로그, 재현 조건을 적어 주세요')

  // The chip is where the 서식 chip lives, and it comes off the same way.
  const chip = page.getByText('장애 원인 좁히기', { exact: true })
  await expect(chip).toBeVisible()
  await page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' }).click()
  await expect(chip).toHaveCount(0)
  await expect(box).toHaveAttribute('placeholder', '무엇이든 물어보세요')

  // Put it back and send.
  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  await page
    .getByRole('dialog')
    .locator('div.group', { hasText: '장애 원인 좁히기' })
    .getByRole('button')
    .first()
    .click()

  const typed = 'ConnectionResetError 가 배포 직후에만 납니다'
  await box.fill(typed)
  await box.press('Enter')
  await expect(page.getByText('먼저 로그를 보겠습니다.')).toBeVisible({ timeout: 30_000 })

  // ── the turn carries the id, and the content is only what was typed ──
  expect(sent).toHaveLength(1)
  expect(sent[0].content).toBe(typed)
  expect(sent[0].startingTemplateId).toBe('t_e2e_debug')

  // ── and the transcript names it rather than quoting it ───────────────
  await expect(page.getByText('시작점 장애 원인 좁히기')).toBeVisible()
  // The framing is nowhere in the transcript. That is the whole point: the
  // words above the bubble are the product's, the words in it are theirs.
  await expect(page.getByText('확신이 없는 것은')).toHaveCount(0)

  // One turn, then it is over — a 시작점 is not sticky the way a 서식 is.
  await expect(box).toHaveValue('')
  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toHaveCount(0)
})
