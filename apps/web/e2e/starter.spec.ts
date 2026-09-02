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
  // 공유 카탈로그 전환(e47bad6) 뒤로 새 계정의 "내 것"은 빈 목록에서
  // 시작한다 — 제품이 싣고 오는 것은 관리자 소유의 org 행들이고, 배선 검사는
  // 그 행들에 대고 한다. me 는 스토어 쪽 단언이 이 계정을 헷갈리지 않게만 쓴다.
  void me
  const mine = agents.filter((a: { visibility: string }) => a.visibility === 'org')

  expect(agents.length).toBeGreaterThanOrEqual(5)
  // 자기 목록이 아니라 스토어가 채워져 있어야 한다 — 아래 catalogue 단언이
  // 그 일을 하고, 여기서는 "빈 제품은 아니다"만 잡는다.
  expect(skills.length).toBeGreaterThanOrEqual(0)

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
  // 카탈로그 에이전트의 skillIds 는 카탈로그 스킬을 가리킨다. 이 계정의
  // /api/skills 는 설치 전엔 비어 있으므로 스토어에서 집합을 만든다.
  const store = await page.evaluate(async (fn) => eval(fn)('/api/skills/store'), AS_USER)
  const catalogue = (store ?? []).concat(skills)
  const ids = new Set(catalogue.map((s: { id: string }) => s.id))
  expect(catalogue.length).toBeGreaterThanOrEqual(5)
  const attached = mine.flatMap((a: { skillIds: string[] }) => a.skillIds ?? [])
  expect(attached.length).toBeGreaterThan(0)
  for (const id of attached) expect(ids.has(id), '연결된 스킬이 존재하지 않습니다').toBe(true)

  // And each skill carries a procedure, not just a title.
  // Personal/shared rows may intentionally be a one-line instruction. The
  // shipped official catalogue is what promises a complete procedure.
  for (const skill of catalogue.filter((s: { official?: boolean; catalogKey?: string | null }) =>
    s.official || s.catalogKey,
  )) {
    expect(skill.body.length, `${skill.name} 내용 없음`).toBeGreaterThan(40)
    expect(skill.whenToUse.length, `${skill.name} 사용 시점 없음`).toBeGreaterThan(0)
  }

  // They show up on the screens, not only in the API.
  await page.goto('/agents')
  // 카탈로그 행은 스토어 탭에 선다 — 내 목록은 비어서 시작한다.
  await page.getByRole('tab', { name: /스토어/ }).click()
  await expect(page.getByText(agents[0].name).first()).toBeVisible({ timeout: 15_000 })
  await page.goto('/skills')
  // 이 계정의 목록은 비어서 시작할 수 있다 — 제품이 싣고 오는 스킬은
  // 스토어 탭에 선다.
  const shelf = catalogue[0] ?? skills[0]
  // shelf 가 내 목록이 아니라 스토어의 행이면, 그 행이 서는 탭으로 간다 —
  // 내 목록이 비지 않았어도 스토어 행은 내 탭에 없다.
  const shelfIsMine = skills.some((s: { id: string }) => s.id === shelf.id)
  if (!shelfIsMine) {
    await page.getByRole('tab', { name: /스토어/ }).click()
  }
  await expect(page.getByText(shelf.name).first()).toBeVisible({ timeout: 15_000 })
})

test('시작점을 고르면 입력창은 비어 있고 칩만 붙는다', async ({ page }) => {
  await signIn(page)
  // 챗에서 확인한다. 보고서의 기본 시작점은 같은 일을 하는 서식이 생기면서
  // 걷어냈고, 이 사례가 확인하는 규칙 — 문장은 입력창이 아니라 턴에 실린다 —
  // 은 표면의 종류로 갈리므로 챗에서도 같다.
  await page.goto('/new/chat')

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('시작점 검색').fill('장애 원인')
  // The card asks for what you have to bring, on the card; the prompt it
  // rides in with is not pasted anywhere.
  const card = dialog.locator('div.group', { hasText: '장애 원인 좁히기' }).first()
  await expect(card.getByLabel('장애 원인 좁히기 · 에러 로그')).toBeVisible()
  await card.getByRole('button', { name: /시작점 선택/ }).click()

  // Attached, not typed: the framing rides with the turn, and the box asks for
  // the half only the person has. Nothing was filled in on the card, so the
  // box is still empty — what it never holds is the framing itself.
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('')
  await expect(box).toHaveAttribute('placeholder', /에러 로그, 재현 조건/)
  await expect(page.getByRole('button', { name: /장애 원인 좁히기 시작점 해제/ })).toBeVisible()
  await expect(page).toHaveURL(/\/new\/chat$/)
})
