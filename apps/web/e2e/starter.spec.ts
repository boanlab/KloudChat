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

  const { agents, skills } = await page.evaluate(async (fn) => {
    return {
      agents: await eval(fn)('/api/agents'),
      skills: await eval(fn)('/api/skills'),
    }
  }, AS_USER)

  expect(agents.length).toBeGreaterThanOrEqual(5)
  expect(skills.length).toBeGreaterThanOrEqual(5)

  // Every agent says what it is for and how to behave — a row with an empty
  // system prompt is a name with nothing behind it.
  for (const agent of agents) {
    expect(agent.systemPrompt.length, `${agent.name} 지침 없음`).toBeGreaterThan(40)
    expect(agent.description.length, `${agent.name} 설명 없음`).toBeGreaterThan(0)
    expect(agent.kinds.length, `${agent.name} 적용 화면 없음`).toBeGreaterThan(0)
  }

  // Skills are attached by id. The seeder builds them from its own keys, and a
  // key left unresolved would point at nothing while looking wired.
  const ids = new Set(skills.map((s: { id: string }) => s.id))
  const attached = agents.flatMap((a: { skillIds: string[] }) => a.skillIds ?? [])
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

test('템플릿을 고르면 보내지 않고 입력창에 채워진다', async ({ page }) => {
  await signIn(page)
  await page.goto('/new/report')

  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  // The card shows what you have to bring, not the prompt it will paste.
  await expect(page.getByRole('dialog').getByText('업무·기술 보고서')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('독자', { exact: true })).toBeVisible()
  await page.getByRole('dialog').getByText('업무·기술 보고서').click()

  // Filled, not sent. Every template stops where the person takes over, so
  // sending one delivered a sentence that ended at a colon.
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue(/목적과 독자: $/)
  await expect(page).toHaveURL(/\/new\/report$/)
})
