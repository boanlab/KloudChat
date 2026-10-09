import { expect, test } from '@playwright/test'
import { conversationRows, hubFixture, myAgents, myPipelines, storeAgents } from './customize-fixture'

/**
 * 「사용자 지정」: one hub for agents, skills, workflows, connectors, memory and designs. Its tab,
 * view, search, filter and sort live in the address; every old screen's address lands on its tab.
 */

test.use({ serviceWorkers: 'block' })

test('tabs, 내 항목 / 탐색, search, filter and sort are kept in the address', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const phone = testInfo.project.name === 'phone'
  await page.goto('/customize')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  // The person has agents of their own, so the hub opens on 내 항목.
  await expect(page.getByRole('tab', { name: '에이전트' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('button', { name: '내 항목' })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('item-card')).toHaveCount(myAgents.length, { timeout: 15_000 })
  // The card carries no slug, model, temperature or tool badges.
  const first = page.getByTestId('item-card').first()
  await expect(first).not.toContainText('@a111')
  await expect(first).not.toContainText('temp')
  await expect(first).toContainText('12회 사용')

  await page.getByRole('button', { name: '탐색' }).click()
  await expect(page).toHaveURL(/tab=agents/)
  await expect(page).toHaveURL(/view=explore/)
  await expect(page.getByTestId('hub-banner')).toContainText(storeAgents[0].name)
  await expect(page.getByRole('region', { name: '추천', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '가장 많이 추가된 에이전트' })).toBeVisible()
  await expect(page.getByRole('region', { name: '새 에이전트', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '추천', exact: true }).getByTestId('item-card').first()).toContainText('제작: 공식 · 42회 추가')

  if (phone) await page.getByRole('button', { name: '검색' }).click()
  await page.getByRole('searchbox', { name: '검색' }).fill('보도')
  await expect(page).toHaveURL(/q=%EB%B3%B4%EB%8F%84/)
  await expect(page.getByTestId('item-card')).toHaveCount(1, { timeout: 15_000 })
  await page.getByRole('searchbox', { name: '검색' }).fill('')

  if (phone) {
    await page.getByRole('button', { name: '필터·정렬' }).click()
    const sheet = page.getByRole('dialog', { name: '필터·정렬' })
    await sheet.getByRole('button', { name: '보고서' }).click()
    await sheet.getByRole('button', { name: '이름' }).click()
    await sheet.getByRole('button', { name: '완료' }).click()
  } else {
    await page.getByRole('button', { name: '필터' }).click()
    await page.getByRole('menuitemcheckbox', { name: '보고서' }).click()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: /^정렬/ }).click()
    await page.getByRole('menuitemcheckbox', { name: '이름' }).click()
  }
  await expect(page).toHaveURL(/kind=report/)
  await expect(page).toHaveURL(/sort=name/)
  // A filter flattens 탐색 into one grid, ordered by name.
  await expect(page.getByTestId('item-card')).toHaveCount(2, { timeout: 15_000 })
  await expect(page.getByTestId('item-card').first()).toContainText('보도자료 초안')

  // A reload keeps all of it.
  await page.reload()
  await expect(page.getByTestId('item-card')).toHaveCount(2, { timeout: 15_000 })

  await page.getByRole('tab', { name: '워크플로' }).click()
  await expect(page).toHaveURL(/tab=workflows/)
  await expect(page.getByTestId('item-card')).toHaveCount(myPipelines.length, { timeout: 15_000 })
  await expect(page.getByText('기본 워크플로 (잠김)')).toBeVisible()

  await page.getByRole('tab', { name: '커넥터' }).click()
  await expect(page.getByRole('button', { name: '내 항목' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: /내 커넥터/ })).toBeVisible()
  await page.getByRole('tab', { name: '메모리' }).click()
  await expect(page.getByRole('button', { name: '새 메모리' })).toBeVisible()
  await page.getByRole('tab', { name: '디자인' }).click()
  await expect(page.getByText('학과 공식 문서')).toBeVisible()
  await page.getByRole('button', { name: '탐색' }).click()
  await expect(page.getByRole('region', { name: '서식 · 보고서' })).toBeVisible()
  await expect(page.getByRole('region', { name: '공유된 디자인' })).toContainText('기관 공용')

  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a tab with nothing of the person\'s own opens on 탐색', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { emptyMine: true })
  await page.goto('/customize?tab=agents')
  await expect(page.getByTestId('hub-banner')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '탐색' })).toHaveAttribute('aria-pressed', 'true')
  expect(state.unexpected).toEqual([])
})

const redirects: [string, RegExp][] = [
  ['/agents', /\/customize\?tab=agents$/],
  ['/skills', /\/customize\?tab=skills$/],
  ['/pipelines', /\/customize\?tab=workflows$/],
  ['/pipelines?tab=shipped&agent=a3333333333333333333333333333333', /\/customize\?tab=workflows&view=mine&focus=a3333333333333333333333333333333$/],
  ['/pipelines?tab=store', /\/customize\?tab=workflows&view=explore$/],
  [`/pipelines/${myPipelines[0].id}`, new RegExp(`/customize/workflows/${myPipelines[0].id}$`)],
  ['/designs', /\/customize\?tab=designs$/],
  ['/designs?tab=template', /\/customize\?tab=designs&view=explore$/],
  ['/connectors', /\/customize\?tab=connectors$/],
  ['/memory', /\/customize\?tab=memory$/],
]

test('every old address lands on its tab', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  for (const [from, to] of redirects) {
    await page.goto(from)
    await expect(page, from).toHaveURL(to, { timeout: 15_000 })
  }
  // The shipped flow named in the old address is opened and picked out.
  await page.goto('/pipelines?tab=shipped&agent=a3333333333333333333333333333333')
  await expect(page.locator('[data-agent="a3333333333333333333333333333333"]')).toBeVisible({ timeout: 15_000 })
  expect(state.problems).toEqual([])
})

test('account menu: one 사용자 지정 item, 대화 기록 beside it, 계정 unchanged; sidebar has it too', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  if (testInfo.project.name === 'phone') await page.getByRole('button', { name: '사이드바 토글' }).click()
  await expect(page.getByRole('link', { name: '사용자 지정' })).toBeVisible()
  await page.getByRole('button', { name: /^계정 메뉴/ }).filter({ visible: true }).first().click()
  const menu = page.getByRole('menu')
  const names = (await menu.getByRole('menuitem').allTextContents()).map((n) => n.replace(/(Ctrl|⌘) \/$/, '').trim())
  expect(names).toEqual(['사용자 지정', '대화 기록', 'AI 에이전트 연동', 'API 연동', '사용량', '설정', '문의·신고', '키보드 단축키', '로그아웃'])
  for (const gone of ['에이전트', '스킬', '워크플로', '커넥터', '메모리', '디자인']) {
    await expect(menu.getByRole('menuitem', { name: gone, exact: true })).toHaveCount(0)
  }
  await menu.getByRole('menuitem', { name: '사용자 지정' }).click()
  await expect(page).toHaveURL(/\/customize$/)
  expect(state.problems).toEqual([])
})

test('sidebar: pinned, then the 15 most recent, then 모든 대화 보기; search covers them all', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { conversations: 40 })
  const rows = conversationRows(40)
  const old = rows[33]
  await page.goto('/customize')
  if (testInfo.project.name === 'phone') await page.getByRole('button', { name: '사이드바 토글' }).click()
  const side = page.locator('aside')
  await expect(side.getByText('고정됨')).toBeVisible({ timeout: 15_000 })
  await expect(side.getByText('최근', { exact: true })).toBeVisible()
  // 1 pinned + 15 recent.
  await expect(side.getByRole('button', { name: /^대화 \d+$/ })).toHaveCount(16)
  await expect(side.getByRole('button', { name: old.title, exact: true })).toHaveCount(0)
  await expect(side.getByRole('link', { name: '모든 대화 보기' })).toHaveAttribute('href', '/history')

  await side.getByRole('textbox', { name: '대화 빠른 검색' }).fill('대화 34')
  await expect(side.getByText('검색 결과', { exact: true })).toBeVisible()
  await expect(side.getByRole('button', { name: old.title, exact: true })).toBeVisible()
  await side.getByRole('textbox', { name: '대화 빠른 검색' }).fill('')

  // An older conversation, once open, stays in view.
  await page.goto(`/s/${old.id}`)
  if (testInfo.project.name === 'phone') await page.getByRole('button', { name: '사이드바 토글' }).click()
  await expect(side.getByRole('button', { name: old.title, exact: true })).toBeVisible({ timeout: 15_000 })
  await side.getByRole('link', { name: '모든 대화 보기' }).click()
  await expect(page).toHaveURL(/\/history$/)
  expect(state.problems).toEqual([])
})
