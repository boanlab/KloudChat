import { expect, test } from '@playwright/test'
import { hubFixture } from './customize-fixture'

/**
 * The short forms: a skill is a name, a description and its steps; an agent is four essentials
 * with the rest folded away. Tools are named in words, grouped, and raw names stay hidden.
 */

test.use({ serviceWorkers: 'block' })

test('skill form: exactly 이름, 설명 and the editor; 초안 keeps it out of the composer', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=skills')
  await expect(page.getByTestId('item-card').first()).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '추가', exact: true }).filter({ visible: true }).first().click()
  await page.getByRole('menuitem', { name: '스킬 만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/skills\/new$/)
  const form = page.getByTestId('skill-form')
  // Visible text fields: 이름, 설명, 절차. 사용 시점 waits under 「더 설정하기」, closed.
  const fields = form.locator('input[type="text"], input:not([type]), textarea').filter({ visible: true })
  await expect(fields).toHaveCount(3)
  await expect(form.getByRole('textbox', { name: '절차' })).toBeVisible()
  await expect(form.getByText('공개 범위')).toHaveCount(0)
  await expect(form.getByText('필요한 도구')).toBeHidden()

  await form.getByRole('textbox', { name: '이름' }).fill('주간 보고 정리')
  await form.getByRole('textbox', { name: '설명' }).fill('지난주 작업을 세 갈래로 정리합니다.')
  await form.getByRole('textbox', { name: '절차' }).fill('1. 성과\n2. 막힌 점\n3. 다음 할 일')
  // Line numbers follow the text.
  await expect(form.getByText('3', { exact: true }).first()).toBeVisible()
  await form.getByRole('switch', { name: '초안으로 두기' }).click()
  await expect(form.getByText('초안 켜짐 · 입력창에 나오지 않습니다')).toBeVisible()
  await form.getByRole('button', { name: '만들기' }).click()

  await expect(page).toHaveURL(/\/customize\/skills\/k9999999999999999999999999999999$/)
  const created = state.calls.find((c) => c.method === 'POST' && c.path === '/skills')
  expect(created?.body).toMatchObject({ name: '주간 보고 정리', whenToUse: '지난주 작업을 세 갈래로 정리합니다.', enabled: false,
    requiredTools: [], visibility: 'private' })
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('agent form: essentials and 진행 과정 in view, folded groups, tools by name with raw names under 기술 정보', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('heading', { level: 1, name: '새 에이전트' })).toBeVisible()
  await expect(form.getByRole('textbox', { name: '이름' })).toBeVisible()
  await expect(form.getByRole('textbox', { name: '한 줄 설명' })).toBeVisible()
  await expect(form.getByRole('textbox', { name: '지시' })).toBeVisible()
  // 진행 과정 starts at 바로 답하기; the words 워크플로 and 작동 단계 are nowhere.
  await expect(form.getByRole('radio', { name: /바로 답하기/ })).toBeChecked()
  await expect(form.getByRole('radio', { name: /기본 과정에서 시작/ })).toBeVisible()
  await expect(form.getByRole('radio', { name: /직접 구성/ })).toBeVisible()
  await expect(form.getByText(/워크플로|작동 단계/)).toHaveCount(0)
  const visibleFields = form.locator('input:not([type="radio"]):not([type="checkbox"]):not([type="range"]):not([type="file"]), textarea, select').filter({ visible: true })
  await expect(visibleFields).toHaveCount(3)
  // Every group starts closed.
  const groups = page.getByTestId('agent-advanced').locator('details')
  await expect(page.getByTestId('agent-advanced').locator('summary')).toHaveText([
    /템플릿·테마/, /대화 시작/, /자료/, /스킬과 도구/, /고급/, /공유/])
  for (const group of await groups.all()) await expect(group).not.toHaveAttribute('open', '')
  await expect(page.getByText(/mcp__/)).toHaveCount(0)

  await form.getByRole('textbox', { name: '이름' }).fill('이슈 정리 도우미')
  await expect(form.getByText('@이슈-정리-도우미')).toBeVisible()
  await form.getByRole('button', { name: '바꾸기' }).click()
  await expect(form.getByRole('textbox', { name: '핸들' })).toBeVisible()
  await form.getByRole('textbox', { name: '지시' }).fill('열린 이슈를 우선순위로 정리한다.')

  await page.getByText('스킬과 도구', { exact: true }).click()
  const picker = page.getByTestId('tool-picker')
  await expect(picker.getByRole('button', { name: '기본값 따르기' })).toHaveAttribute('aria-pressed', 'true')
  await picker.getByRole('button', { name: '직접 고르기' }).click()
  await picker.getByRole('button', { name: '도구 고르기' }).click()
  const panel = page.getByRole('dialog', { name: '도구 고르기' })
  await expect(panel.locator('legend')).toHaveText(['기본 도구', 'GitHub'])
  await expect(panel.getByText('웹에서 최신 자료를 찾아 출처와 함께 답합니다.')).toBeVisible()
  await expect(page.getByText(/mcp__/)).toHaveCount(0)
  await panel.getByRole('textbox', { name: '도구 검색' }).fill('issues')
  await panel.getByRole('checkbox', { name: /list open issues/ }).check()
  await page.keyboard.press('Escape')
  await expect(picker.getByText('list open issues')).toBeVisible()
  await expect(page.getByText(/mcp__/).filter({ visible: true })).toHaveCount(0)
  await picker.getByText('기술 정보').click()
  await expect(picker.getByText('mcp__github__list_open_issues')).toBeVisible()

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  const created = state.calls.find((c) => c.method === 'POST' && c.path === '/agents')
  expect(created?.body).toMatchObject({ name: '이슈 정리 도우미', tools: ['mcp__github__list_open_issues'], skillIds: null, kinds: ['chat'] })
  // A new agent's 진행 과정 is saved with it.
  expect(state.calls.find((c) => c.method === 'PUT' && c.path.endsWith('/process'))?.body).toMatchObject({ mode: 'direct', bundles: [] })
  // The detail screen shows what it does and, for its owner, its settings.
  await expect(page.getByRole('heading', { level: 1, name: '이슈 정리 도우미' })).toBeVisible()
  await expect(page.getByRole('region', { name: '설정' })).toBeVisible()
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('agent detail: sealed instructions stay private; a store agent offers 추가', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/a4444444444444444444444444444444')
  await expect(page.getByRole('heading', { level: 1, name: '보도자료 초안' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('내용 비공개')).toBeVisible()
  await expect(page.getByRole('region', { name: '설정' })).toHaveCount(0)
  await page.getByRole('button', { name: '추가', exact: true }).filter({ visible: true }).first().click()
  await expect(page).toHaveURL(/\/customize\/agents\/copy-a4444/)
  expect(state.calls.map((c) => `${c.method} ${c.path}`)).toContain('POST /agents/a4444444444444444444444444444444/install')
  expect(state.problems).toEqual([])
})
