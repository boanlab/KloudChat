import { expect, test } from '@playwright/test'
import {
  evidenceKinds,
  followUps,
  workKinds,
  workPersonas,
  workScenarios,
} from './work-scenario-catalog'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('업무 시나리오 카탈로그가 1,000건 이상이고 모든 조합을 빠짐없이 식별한다', async () => {
  const expected = workPersonas.length * workKinds.length * evidenceKinds.length * followUps.length
  expect(workScenarios).toHaveLength(expected)
  expect(workScenarios.length).toBeGreaterThanOrEqual(1_000)
  expect(new Set(workScenarios.map((scenario) => scenario.id)).size).toBe(expected)
})

test('모든 시나리오가 실제로 타이핑할 만한 요청을 들고 있다', async () => {
  // A prompt is a request, not a description of the job.
  const meta = /로서 .*를 근거로 .*결과를 확인한 뒤/
  for (const scenario of workScenarios) {
    expect(scenario.prompt.length, scenario.id).toBeGreaterThan(25)
    expect(scenario.prompt, scenario.id).not.toMatch(meta)
    // A request names its subject rather than the person making it.
    expect(scenario.prompt.includes(scenario.persona), scenario.id).toBe(false)
  }

  // One distinct request per persona × job.
  const written = new Set(workScenarios.map((scenario) => scenario.prompt))
  expect(written.size).toBe(workPersonas.length * workKinds.length)
})

test('모든 업무 시나리오가 사용 가능한 작업 화면으로 연결된다', async ({ page }) => {
  // One visit per surface; every row is checked against its surface's contract.
  const contracts = new Map<string, string>()
  for (const [, , surface] of workKinds) contracts.set(surface, `/new/${surface}`)

  for (const [surface, route] of contracts) {
    await page.goto(route)
    await expect(page).toHaveURL(new RegExp(`${route}$`))
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    // One entry point on every surface: a 시작점 carries the 서식 it needs.
    await expect(page.getByRole('button', { name: '작업 시작하기' })).toBeVisible()
    if (surface !== 'image' && surface !== 'av') {
      await expect(page.getByRole('button', { name: '웹 검색' })).toBeVisible()
    }
  }

  for (const scenario of workScenarios) {
    expect(contracts.has(scenario.surface), scenario.id).toBe(true)
  }
})

test('근거와 후속 작업에 필요한 공통 UI가 실제 계정에서 발견된다', async ({ page }) => {
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: '첨부' })).toBeVisible()
  await expect(page.getByRole('button', { name: '웹 검색' })).toBeVisible()

  await page.goto('/history')
  await expect(page.getByRole('heading', { name: /기록|대화/ })).toBeVisible()
  await page.goto('/projects')
  await expect(page.getByRole('button', { name: '새 프로젝트' }).first()).toBeVisible()
  await page.goto('/artifacts')
  await expect(page.getByRole('heading', { name: '아티팩트' })).toBeVisible()
})
