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

  // People's own and shared agents may exist; the catalogue's six stand among them.
  const official = agents.filter((a: { official?: boolean; catalogKey?: string | null }) =>
    a.official || a.catalogKey,
  )
  const names = new Set(official.map((a: { name: string }) => a.name))
  for (const name of ['고객 맞춤 제안서', '개조식 보고서', '회의록', '데이터 분석',
    '자기소개서·경력기술서', '비교·분석 보고서']) {
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

  // On screen: catalogue rows stand in the 스토어 tab.
  await page.goto('/skills')
  const shelf = catalogue[0] ?? skills[0]
  const shelfIsMine = skills.some((s: { id: string }) => s.id === shelf.id)
  if (!shelfIsMine) {
    await page.getByRole('tab', { name: /스토어/ }).click()
  }
  await expect(page.getByText(shelf.name).first()).toBeVisible({ timeout: 15_000 })
})

test('시작점을 고르면 입력창은 비어 있고 칩만 붙는다', async ({ page }) => {
  await signIn(page)
  await page.goto('/new/chat')

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('장애 원인')
  const card = dialog.locator('div.group', { hasText: '장애 원인 좁히기' }).first()
  await expect(card.getByText('에러 로그')).toBeVisible()
  await card.getByRole('button', { name: /시작점 선택/ }).click()

  // The framing rides with the turn; the box never holds it.
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('')
  // Questions sit above the box.
  await expect(page.getByRole('group', { name: '장애 원인 좁히기 시작점 질문' }).getByLabel('장애 원인 좁히기 · 에러 로그')).toBeVisible()
  await expect(page.getByRole('button', { name: /장애 원인 좁히기 시작점 해제/ })).toBeVisible()
  await expect(page).toHaveURL(/\/new\/chat$/)
})
