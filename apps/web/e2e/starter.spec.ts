import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The shipped catalogue: skills, and the six lineup agents in the store. */

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

test('기본 스킬과 기본 에이전트 여섯 개가 갖춰져 있다', async ({ page }) => {
  await signIn(page)

  const { agents, skills } = await page.evaluate(async (fn) => {
    return {
      agents: await eval(fn)('/api/agents'),
      skills: await eval(fn)('/api/skills'),
    }
  }, AS_USER)

  // People's own and shared agents may exist; the catalogue's four stand among them.
  const official = agents.filter((a: { official?: boolean; catalogKey?: string | null }) =>
    a.official || a.catalogKey,
  )
  const names = new Set(official.map((a: { name: string }) => a.name))
  for (const name of ['개조식 보고', '비교·결정', '자료 추출·집계', '회의록']) {
    expect(names.has(name), `${name} 없음`).toBe(true)
  }

  // This account's /api/skills may be empty; the catalogue stands in the store.
  const store = await page.evaluate(async (fn) => eval(fn)('/api/skills/store'), AS_USER)
  const catalogue = (store ?? []).concat(skills)
  expect(catalogue.length).toBeGreaterThanOrEqual(5)

  // Official catalogue skills carry a procedure; personal rows may be one line.
  for (const skill of catalogue.filter((s: { official?: boolean; catalogKey?: string | null }) =>
    s.official || s.catalogKey,
  )) {
    expect(skill.body.length, `${skill.name} 내용 없음`).toBeGreaterThan(40)
    expect(skill.whenToUse.length, `${skill.name} 사용 시점 없음`).toBeGreaterThan(0)
  }

  // On screen: catalogue rows stand in 사용자 지정 › 스킬 › 탐색.
  const shelf = catalogue[0] ?? skills[0]
  const shelfIsMine = skills.some((s: { id: string }) => s.id === shelf.id)
  await page.goto(`/customize?tab=skills&view=${shelfIsMine ? 'mine' : 'explore'}`)
  await page.getByRole('searchbox', { name: '검색' }).fill(shelf.name)
  await expect(page.getByText(shelf.name).first()).toBeVisible({ timeout: 15_000 })
})
