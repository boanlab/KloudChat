import { expect, test, type Page, type Route } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A quarter of the conversations on this account are a question with nothing
 * under it: the sentence is stored before the model answers, so a turn that
 * breaks, is refused, or has its tab closed leaves only the asking behind.
 *
 * Keeping the question is right — the person did ask. What was wrong is that
 * nothing said the answer never came, and there was no way to ask again. The
 * live tab did put a notice on screen; it lived in that tab's memory and was
 * gone on reload, which is exactly the state these specs open in.
 *
 * The transcripts are served from here rather than earned by breaking a real
 * stream: what is under test is what a reader is shown when they come back to
 * one, and a turn that fails for real still costs a model call to fail.
 */

const ID = 's_unanswered_e2e'
const QUESTION = '전기차 보조금이 어떻게 되나요?'

type Row = Record<string, unknown>

function message(over: Row): Row {
  return {
    id: 'm_1',
    role: 'user',
    content: QUESTION,
    steps: null,
    attachments: null,
    variants: null,
    usage: null,
    model: null,
    routing: null,
    startedFrom: null,
    rating: null,
    failure: null,
    createdAt: new Date().toISOString(),
    ...over,
  }
}

function session(messages: Row[]): Row {
  return {
    id: ID,
    kind: 'chat',
    title: '보조금 문의',
    projectId: null,
    agentId: null,
    model: 'vendor/model',
    routingMode: 'manual',
    artifactId: null,
    renderTemplateId: null,
    pinned: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messages,
    preview: QUESTION,
    messageCount: messages.length,
  }
}

/** Serves one fabricated conversation and records anything sent back into it. */
async function serve(
  page: Page,
  messages: Row[],
  sent: string[],
  reply?: string,
  holdMs = 0,
) {
  const row = session(messages)
  await page.route('**/api/sessions**', async (route: Route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const json = (payload: unknown) =>
      route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      })

    if (method === 'GET' && url.pathname.endsWith('/api/sessions')) {
      return json([{ ...row, messages: null }])
    }
    if (method === 'GET' && url.pathname.endsWith(`/api/sessions/${ID}`)) return json(row)
    if (method === 'POST' && url.pathname.endsWith(`/api/sessions/${ID}/messages`)) {
      sent.push(String((route.request().postDataJSON() as { content: string }).content))
      // Answered slowly rather than instantly when asked to: a turn that is
      // still running is a state the interface has to be judged in, and a
      // response that lands before the first assertion never shows it.
      if (holdMs) await new Promise((resolve) => setTimeout(resolve, holdMs))
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
        body:
          `data: ${JSON.stringify({ type: 'delta', text: reply ?? '' })}\n\n` +
          `data: ${JSON.stringify({ type: 'done' })}\n\n`,
      })
    }
    return route.continue()
  })
}

test('답변이 오지 않은 질문은 다시 열어도 그 사실과 다시 물어볼 길을 함께 보여 준다', async ({
  page,
}) => {
  test.setTimeout(120_000)
  await signIn(page)
  const sent: string[] = []
  await serve(page, [message({ failure: 'no_answer' })], sent, '지자체마다 다릅니다.')

  await page.goto(`/s/${ID}`)

  await expect(page.getByText(QUESTION).last()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('답변을 받지 못했습니다.')).toBeVisible()

  // A video job has had a retry all along; a conversation turn had none.
  await page.getByRole('button', { name: '다시 물어보기' }).click()
  await expect.poll(() => sent, { timeout: 30_000 }).toEqual([QUESTION])
  // Asked again, not repaired: the question that went unanswered stays in the
  // record beside the one that did not.
  await expect(page.getByText(QUESTION)).toHaveCount(2)
})

test('중간에 끊긴 답변은 쓰다 만 내용과 함께 끊겼다고 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await serve(
    page,
    [
      message({}),
      message({
        id: 'm_2',
        role: 'assistant',
        content: '보조금은 지자체마다',
        failure: 'interrupted',
      }),
    ],
    [],
  )

  await page.goto(`/s/${ID}`)

  await expect(page.getByText('보조금은 지자체마다')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('답변이 중간에 끊겨 여기까지만 남았습니다.')).toBeVisible()
  // The notice belongs to the half-written answer, so the question above it
  // must not grow a second one — nor a way back that would ask twice.
  await expect(page.getByText('답변을 받지 못했습니다.')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '다시 물어보기' })).toHaveCount(0)
})

test('답변이 온 대화는 실패한 것처럼 보이지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await serve(
    page,
    [message({}), message({ id: 'm_2', role: 'assistant', content: '지자체마다 다릅니다.' })],
    [],
  )

  await page.goto(`/s/${ID}`)

  await expect(page.getByText('지자체마다 다릅니다.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('답변을 받지 못했습니다.')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '다시 물어보기' })).toHaveCount(0)
})

test('아직 답이 오는 중인 질문은 실패로 보이지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const sent: string[] = []
  // The answer is held back, so the question spends the checks below in the one
  // state it must never be called unanswered in: still being answered.
  await serve(page, [], sent, '지자체마다 다릅니다.', 10_000)

  await page.goto(`/s/${ID}`)
  await page.getByLabel('프롬프트 입력').fill(QUESTION)
  await page.getByLabel('프롬프트 입력').press('Enter')

  await expect.poll(() => sent, { timeout: 30_000 }).toEqual([QUESTION])
  await expect(page.getByText(QUESTION).last()).toBeVisible()
  await expect(page.getByText('답변을 받지 못했습니다.')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '다시 물어보기' })).toHaveCount(0)

  // And once it does arrive, it is an answer and not a failure either.
  await expect(page.getByText('지자체마다 다릅니다.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('답변을 받지 못했습니다.')).toHaveCount(0)
})
