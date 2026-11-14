import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'
import { viewport } from './project'
import { contextUsage } from './shell-mocks'

/**
 * The composer's context gauge: a ring with the percent of the model's window the next turn
 * takes, and on hover or tap the breakdown. The server's numbers come from
 * `POST /sessions/context-usage`; the draft is counted as it is typed.
 */

const phoneProject = () => viewport(test.info()) === 'phone'
const shotPath = (name: string) => `${process.env.SHOT_DIR ?? 'test-results/shots'}/${name}.png`

type Asked = {
  sessionId?: string | null; model?: string | null; draft?: string; attachments?: string[]
  kind?: string; projectId?: string | null; agentId?: string | null
}

/** Answers the gauge: each turn adds 20k of history; the local model has a 32k window. */
async function gaugeServer(page: Page, sends: () => number) {
  const asked: Asked[] = []
  await page.route('**/sessions/context-usage', async (route) => {
    const body = route.request().postDataJSON() as Asked
    asked.push(body)
    const local = body.model === 'fixture/local'
    const history = body.sessionId ? 9_000 + sends() * 20_000 : 0
    const window = local ? 32_000 : 64_000
    const used = 3_000 + 1_200 + history
    await route.fulfill({
      json: contextUsage({
        model: local ? 'fixture/local' : 'fixture/model',
        statedWindow: window,
        window,
        used,
        parts: { system: 3_000, instructions: 0, memory: 0, files: 1_200, history, draft: 0, tools: 0 },
        // the second turn's question no longer fits whole: older turns run on a summary
        summarised: sends() >= 2,
        summarisedMessages: sends() >= 2 ? 4 : 0,
        lastTurn: history ? { inputTokens: 11_500, model: 'fixture/model' } : null,
      }),
    })
  })
  return asked
}

const gauge = (page: Page) => page.getByRole('meter', { name: '컨텍스트 사용량' })
const details = (page: Page) => page.getByRole('button', { name: /^컨텍스트 사용량 자세히/ })
const popover = (page: Page) => page.getByTestId('context-gauge-popover')

test('shows the share of the window, its breakdown, and follows a turn and the draft', async ({ page }, info) => {
  test.skip(phoneProject(), 'desktop layout')
  const { unexpected, sends } = await composerFixture(page, info)
  const asked = await gaugeServer(page, () => sends.length)
  await page.goto(`/s/${chatId}`)

  // 13.2k of 64k
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '21')
  await expect(gauge(page)).toHaveAttribute('aria-valuemin', '0')
  await expect(gauge(page)).toHaveAttribute('aria-valuemax', '100')
  await expect(page.getByTestId('context-gauge')).toContainText('21%')
  await expect(page.getByTestId('context-gauge')).toHaveAttribute('data-level', 'ok')
  expect(asked.at(-1)).toMatchObject({ sessionId: chatId, model: 'fixture/model' })

  await details(page).hover()
  await expect(popover(page)).toContainText('대화 13k / 64k 토큰')
  await expect(popover(page)).toContainText('기본 시스템 지침')
  await expect(popover(page)).toContainText('첨부·프로젝트 자료')
  await expect(popover(page)).toContainText('대화 기록')
  await expect(popover(page)).toContainText('지난 답의 실제 입력 12k 토큰')
  await expect(popover(page)).toContainText('모델 검증 모델 · 창 64k 토큰')
  await expect(popover(page)).not.toContainText('오래된 대화는 요약되어 이어집니다')
  await page.screenshot({ path: shotPath('context-gauge-1440'), animations: 'disabled' })

  // typing is counted at once, without asking the server
  const before = asked.length
  await page.getByLabel('프롬프트 입력').fill('가'.repeat(10_000))
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '32')
  await expect(popover(page)).toContainText('입력 중인 글')
  expect(asked.length).toBe(before)
  await page.getByLabel('프롬프트 입력').fill('')

  // a turn grows the conversation: the gauge asks again and nears the summary
  await page.mouse.move(0, 0)
  await page.getByLabel('프롬프트 입력').fill('한 번 더 정리해 줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '52')
  await page.getByLabel('프롬프트 입력').fill('세 번째 질문')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.length).toBe(2)
  await expect(page.getByTestId('context-gauge')).toHaveAttribute('data-level', 'near')
  await details(page).click()
  await expect(popover(page).getByTestId('context-gauge-summary')).toContainText('오래된 대화는 요약되어 이어집니다')
  await page.keyboard.press('Escape')
  expect(unexpected).toEqual([])
})

test('switching the model measures against its window', async ({ page }, info) => {
  test.skip(phoneProject(), 'desktop layout')
  const { unexpected } = await composerFixture(page, info)
  const asked = await gaugeServer(page, () => 0)
  await page.goto(`/s/${chatId}`)
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '21')

  await page.getByRole('button', { name: /검증 모델/ }).click()
  await page.getByRole('menuitemradio', { name: /로컬 모델/ }).click()
  await expect.poll(() => asked.at(-1)?.model).toBe('fixture/local')
  // the same 13.2k against 32k
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '41')
  await details(page).click()
  await expect(popover(page).getByTestId('context-gauge-model')).toContainText('모델 로컬 모델 · 창 32k 토큰')
  expect(unexpected).toEqual([])
})

test('a new conversation shows its base cost', async ({ page }, info) => {
  test.skip(phoneProject(), 'desktop layout')
  const { unexpected } = await composerFixture(page, info)
  const asked = await gaugeServer(page, () => 0)
  await page.goto('/')
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '7')
  expect(asked.at(-1)?.sessionId ?? null).toBeNull()
  await details(page).hover()
  await expect(popover(page)).toContainText('대화 4.2k / 64k 토큰')
  await expect(popover(page)).not.toContainText('대화 기록')
  expect(unexpected).toEqual([])
})

test('phone: the ring alone, the breakdown on a tap', { tag: '@phone' }, async ({ page }, info) => {
  test.skip(!phoneProject(), 'phone layout')
  const { unexpected, sends } = await composerFixture(page, info)
  await gaugeServer(page, () => sends.length)
  await page.goto(`/s/${chatId}`)
  await expect(gauge(page)).toHaveAttribute('aria-valuenow', '21')
  await expect(page.getByTestId('context-gauge')).not.toContainText('%')
  await details(page).tap()
  await expect(popover(page)).toContainText('대화 13k / 64k 토큰')
  const box = await popover(page).boundingBox()
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBeTruthy()
  await page.screenshot({ path: shotPath('context-gauge-390'), animations: 'disabled' })
  // a tap outside closes it
  await page.touchscreen.tap(200, 200)
  await expect(popover(page)).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('the report and slides composers show the gauge too', async ({ page }, info) => {
  test.skip(phoneProject(), 'desktop layout')
  const { unexpected } = await composerFixture(page, info)
  const asked = await gaugeServer(page, () => 0)
  for (const surface of ['report', 'slides'] as const) {
    await page.goto(`/new/${surface}`)
    await expect(gauge(page)).toBeVisible()
    await expect.poll(() => (asked.at(-1) as { kind?: string } | undefined)?.kind).toBe(surface)
  }
  expect(unexpected).toEqual([])
})

test('a conversation not sent yet is counted as a new one, not asked for by its id', async ({ page }, info) => {
  test.skip(phoneProject(), 'desktop layout')
  const { unexpected } = await composerFixture(page, info)
  const asked = await gaugeServer(page, () => 0)
  // A report opened in a project (「새로 시작 → 보고서」) lives in the browser until its first
  // send; the server has no row for its id, so asking by it answered 404.
  const heldId = 'd0000000000000000000000000000009'
  await page.addInitScript((id) => {
    const row = {
      id, kind: 'report', title: '새 작업', projectId: 'p-lectures', agentId: null, pipelineId: null,
      model: '', routingMode: 'manual', reasoningMode: null,
      createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z', pinned: false,
      messages: [], preview: null, messageCount: 0, made: null, artifactId: null, pending: null,
      skillIds: [], renderTemplateId: null, unsent: true,
    }
    sessionStorage.setItem('kchat.unsent.fixture-user', JSON.stringify({ [id]: row }))
  }, heldId)
  await page.goto(`/s/${heldId}`)
  await expect(gauge(page)).toBeVisible()
  expect(asked.length).toBeGreaterThan(0)
  for (const body of asked) {
    expect(body.sessionId ?? null).toBeNull()
    expect(body).toMatchObject({ kind: 'report', projectId: 'p-lectures' })
  }
  expect(unexpected).toEqual([])
})
