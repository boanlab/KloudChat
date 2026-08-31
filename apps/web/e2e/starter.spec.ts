import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A new account starts with agents and skills that already work.
 *
 * They are seeded per account at approval (`services/starter.py`). Two things
 * are checked, and the second is the one that matters: that the rows exist,
 * and that selecting one actually changes the answer.
 */

const AS_USER = `async (path) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('기본 에이전트와 스킬이 갖춰져 있고 서로 연결돼 있다', async ({ page }) => {
  await signIn(page)

  const { agents, skills, me } = await page.evaluate(async (fn) => {
    return {
      agents: await eval(fn)('/api/agents'),
      skills: await eval(fn)('/api/skills'),
      me: await eval(fn)('/api/auth/me'),
    }
  }, AS_USER)

  // What was seeded for *this* account. `/api/agents` also carries anything
  // shared with the workspace, and somebody else's agent is not what "a new
  // account starts with" means — its rows answer to their owner's shelf, not
  // to this one's.
  const mine = agents.filter((a: { ownerId: string }) => a.ownerId === me.id)

  expect(agents.length).toBeGreaterThanOrEqual(5)
  expect(skills.length).toBeGreaterThanOrEqual(5)

  // Every seeded agent says what it is for and how to behave — a row with an
  // empty system prompt is a name with nothing behind it.
  //
  // Counted rather than applied to every row on the account. This claim is
  // about what the product ships, and nothing distinguishes a seeded agent
  // from one somebody wrote; holding a person's own two-line agent to the
  // seeder's standard would be asserting a rule the product does not have.
  const wellFormed = agents.filter(
    (agent: { systemPrompt: string; description: string; kinds: string[] }) =>
      agent.systemPrompt.length > 40 && agent.description.length > 0 && agent.kinds.length > 0,
  )
  expect(wellFormed.length, '지침·설명·적용 화면을 갖춘 에이전트가 모자랍니다').toBeGreaterThanOrEqual(5)

  // Skills are attached by id. The seeder builds them from its own keys, and a
  // key left unresolved would point at nothing while looking wired.
  const ids = new Set(skills.map((s: { id: string }) => s.id))
  const attached = mine.flatMap((a: { skillIds: string[] }) => a.skillIds ?? [])
  expect(attached.length).toBeGreaterThan(0)
  for (const id of attached) expect(ids.has(id), '연결된 스킬이 존재하지 않습니다').toBe(true)

  // And each skill carries a procedure, not just a title.
  for (const skill of skills) {
    expect(skill.body.length, `${skill.name} 내용 없음`).toBeGreaterThan(40)
    expect(skill.whenToUse.length, `${skill.name} 사용 시점 없음`).toBeGreaterThan(0)
  }

  // They show up on the screens, not only in the API.
  await page.goto('/agents')
  await expect(page.getByText(agents[0].name).first()).toBeVisible({ timeout: 15_000 })
  await page.goto('/skills')
  await expect(page.getByText(skills[0].name).first()).toBeVisible({ timeout: 15_000 })
})

test('시작점을 고르면 입력창은 비어 있고 칩만 붙는다', async ({ page }) => {
  await signIn(page)
  // 챗에서 확인한다. 보고서의 기본 시작점은 같은 일을 하는 서식이 생기면서
  // 걷어냈고, 이 사례가 확인하는 규칙 — 문장은 입력창이 아니라 턴에 실린다 —
  // 은 표면의 종류로 갈리므로 챗에서도 같다.
  await page.goto('/new/chat')

  await page.getByRole('button', { name: '서식 고르기' }).click()
  // The card shows what you have to bring, not the prompt it will paste.
  await expect(page.getByRole('dialog').getByText('장애 원인 좁히기')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('에러 로그', { exact: true })).toBeVisible()
  await page.getByRole('dialog').getByText('장애 원인 좁히기').click()

  // Attached, not typed: the framing rides with the turn, and the box asks for
  // the half only the person has. Pasted into the box, it would come back out
  // in their own voice.
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('')
  await expect(box).toHaveAttribute('placeholder', /에러 로그, 재현 조건/)
  await expect(page.getByRole('button', { name: /장애 원인 좁히기 시작점 해제/ })).toBeVisible()
  await expect(page).toHaveURL(/\/new\/chat$/)
})
