import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

/** A shared page names the agent, project and 서식 behind a conversation, and leaks none of their bodies.
 *  Set up through the API. */

/** A bearer token of the test's own; the app holds its token in memory. */
async function token(page: Page) {
  return page.evaluate(async (account) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    return (await res.json()).accessToken as string
  }, E2E_ADMIN)
}

test('링크를 받은 사람은 무엇이 이 결과를 만들었는지 읽고, 그 속은 읽지 못한다', async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000)
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }
  const stamp = Date.now()

  const project = await page.request.post('/api/projects', {
    headers,
    data: {
      name: `2분기 감사 ${stamp}`,
      emoji: '📁',
      instructions: '회계 팀에만 공유한다. 미확정 수치는 괄호로 표시한다.',
    },
  })
  const agent = await page.request.post('/api/agents', {
    headers,
    data: {
      name: `감사 담당 ${stamp}`,
      description: '감사 보고서를 쓴다',
      systemPrompt: '내부 감사 절차대로 쓰고, 확인되지 않은 수치는 쓰지 않는다.',
    },
  })
  const session = await page.request.post('/api/sessions', {
    headers,
    data: {
      kind: 'report',
      projectId: (await project.json()).id,
      agentId: (await agent.json()).id,
    },
  })
  const { id } = (await session.json()) as { id: string }
  await page.request.patch(`/api/sessions/${id}`, {
    headers,
    data: { renderTemplateId: 'doc-brief', title: `2분기 감사 요약 ${stamp}` },
  })
  const share = await page.request.post('/api/shares', {
    headers,
    data: { sessionId: id, scope: 'link' },
  })
  const { token: link } = (await share.json()) as { token: string }

  // A brand-new context: no cookie, no account.
  const stranger = await browser.newContext()
  const guest = await stranger.newPage()
  await guest.goto(`/share/${link}`)
  await expect(guest.getByText('읽기 전용')).toBeVisible({ timeout: 20_000 })

  // The three names, in the same sentences the conversation's owner was shown.
  await expect(guest.getByText('이 대화가 가지고 시작하는 것')).toBeVisible()
  await expect(guest.getByText(`감사 담당 ${stamp}`)).toBeVisible()
  await expect(guest.getByText('이 에이전트가 답합니다')).toBeVisible()
  await expect(guest.getByText(`2분기 감사 ${stamp}`)).toBeVisible()
  await expect(guest.getByText('한 장 요약')).toBeVisible()
  await expect(guest.getByText('결과물이 이 서식으로 나옵니다')).toBeVisible()

  // Nothing behind them, read off the response: an unrendered body still left the workspace.
  const payload = await guest.request.get(`/api/shared/${link}`)
  const wire = await payload.text()
  expect(wire).not.toContain('내부 감사 절차대로')
  expect(wire).not.toContain('회계 팀에만 공유')

  await stranger.close()
})

/** A conversation that started with nothing shows no header. */
test('가지고 시작한 것이 없으면 공유 화면은 그 자리를 비워 둔다', async ({ page, browser }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const headers = { Authorization: `Bearer ${await token(page)}` }

  const session = await page.request.post('/api/sessions', { headers, data: { kind: 'chat' } })
  const { id } = (await session.json()) as { id: string }
  await page.request.patch(`/api/sessions/${id}`, { headers, data: { title: '그냥 대화' } })
  const share = await page.request.post('/api/shares', {
    headers,
    data: { sessionId: id, scope: 'link' },
  })
  const { token: link } = (await share.json()) as { token: string }

  const stranger = await browser.newContext()
  const guest = await stranger.newPage()
  await guest.goto(`/share/${link}`)
  await expect(guest.getByText('읽기 전용')).toBeVisible({ timeout: 20_000 })
  await expect(guest.getByText('이 대화가 가지고 시작하는 것')).toHaveCount(0)

  await stranger.close()
})
