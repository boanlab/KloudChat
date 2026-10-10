import { expect, test } from '@playwright/test'
import { conversationRows, hubFixture, myAgents, storeAgents } from './customize-fixture'

/**
 * 「사용자 지정」: one hub for agents (and the 묶음 they tick), skills and connectors. Its tab,
 * view, search, filter and sort live in the address; every old screen's address lands where that
 * screen went (themes and memory in 설정, workflows in the agents' 진행 과정).
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
  await expect(page.getByTestId('hub-banner')).toHaveCount(0)
  await expect(page.getByRole('region', { name: '추천', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '가장 많이 추가된 에이전트' })).toContainText(storeAgents[0].name)
  await expect(page.getByRole('region', { name: '새 에이전트', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '가장 많이 추가된 에이전트' }).getByTestId('item-card').first()).toContainText('제작: 공식 · 42회 추가')

  if (phone) await page.getByRole('button', { name: '검색', exact: true }).click()
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

  // Three tabs, like an assistant's own settings: no 워크플로, 메모리, 양식 or 디자인 tab.
  const strip = page.getByRole('tablist', { name: '사용자 지정' })
  await expect(strip.getByRole('tab')).toHaveText(['에이전트', '스킬', '커넥터'])
  await expect(page.getByText(/워크플로|작동 단계/)).toHaveCount(0)

  // 묶음 live with the agents that tick them.
  await page.getByRole('button', { name: '내 항목' }).click()
  await page.getByTestId('bundles-link').click()
  await expect(page).toHaveURL(/\/customize\/bundles$/)
  await expect(page.getByRole('region', { name: '기본 제공 묶음' }).getByTestId('bundle-card')).toHaveText([
    /쓰기 전에 준비하기/, /다 쓴 뒤 검토하기/, /마무리하기/])
  await expect(page.getByRole('region', { name: '내 묶음' })).toContainText('두 번 검토하고 PDF로')
  await page.goBack()

  await page.getByRole('tab', { name: '커넥터' }).click()
  await expect(page.getByRole('button', { name: '내 항목' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: /내 커넥터/ })).toBeVisible()
  // The catalogue is one flat list, official first: no category headings, no category chips.
  await page.getByRole('tab', { name: /카탈로그/ }).click()
  const catalog = page.getByTestId('connector-catalog')
  await expect(catalog).toBeVisible()
  await expect(catalog).toContainText('YouTube')
  expect((await catalog.textContent())!.indexOf('YouTube')).toBeLessThan((await catalog.textContent())!.indexOf('arXiv'))
  await expect(page.getByRole('heading', { name: /^(미디어|연구|기본)$/ })).toHaveCount(0)
  await expect(catalog.getByText('미디어', { exact: true })).toHaveCount(0)
  await expect(catalog.getByText('stdio', { exact: true })).toHaveCount(0)

  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a tab with nothing of the person\'s own opens on 탐색', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { emptyMine: true })
  await page.goto('/customize?tab=agents')
  await expect(page.getByRole('region', { name: '새 에이전트', exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '탐색' })).toHaveAttribute('aria-pressed', 'true')
  expect(state.unexpected).toEqual([])
})

const redirects: [string, RegExp][] = [
  ['/agents', /\/customize\?tab=agents$/],
  ['/skills', /\/customize\?tab=skills$/],
  ['/connectors', /\/customize\?tab=connectors$/],
  // Workflows became each agent's 진행 과정.
  ['/pipelines', /\/customize\?tab=agents$/],
  ['/pipelines?tab=shipped&agent=a3333333333333333333333333333333', /\/customize\?tab=agents$/],
  ['/pipelines/p1111111111111111111111111111111', /\/customize\?tab=agents$/],
  ['/customize/workflows/p1111111111111111111111111111111', /\/customize\?tab=agents$/],
  ['/customize?tab=workflows', /\/customize\?tab=agents$/],
  // Templates are picked in the agent form and the composer.
  ['/customize?tab=templates', /\/customize\?tab=agents$/],
  // Themes and memory live in 설정.
  ['/designs', /\/settings\/themes$/],
  ['/customize?tab=designs', /\/settings\/themes$/],
  ['/memory', /\/settings\/memory$/],
  ['/customize?tab=memory', /\/settings\/memory$/],
]

test('every old address lands where its screen went', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  for (const [from, to] of redirects) {
    await page.goto(from)
    await expect(page, from).toHaveURL(to, { timeout: 15_000 })
  }
  await page.goto('/memory')
  await expect(page.getByRole('tab', { name: '메모리' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('button', { name: '새 메모리' })).toBeVisible()
  await page.goto('/designs')
  await expect(page.getByTestId('settings-themes')).toBeVisible()
  await expect(page.getByText('학과 공식 문서')).toBeVisible()
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

test('sidebar: pinned, then the 15 most recent, then 모든 대화 보기; the box opens search', async ({ page }, testInfo) => {
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

  // The 검색 box opens the search dialog (search-dialog.spec.ts covers what it finds).
  await side.getByRole('button', { name: '대화 빠른 검색' }).click()
  await expect(page.getByRole('dialog', { name: '검색' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '검색' })).toHaveCount(0)

  // An older conversation, once open, stays in view.
  await page.goto(`/s/${old.id}`)
  if (testInfo.project.name === 'phone') await page.getByRole('button', { name: '사이드바 토글' }).click()
  await expect(side.getByRole('button', { name: old.title, exact: true })).toBeVisible({ timeout: 15_000 })
  await side.getByRole('link', { name: '모든 대화 보기' }).click()
  await expect(page).toHaveURL(/\/history$/)
  expect(state.problems).toEqual([])
})

test('묶음 만들기 opens the bundle editor, its items from the same steps 직접 구성 offers', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop')
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=mine')
  await page.getByRole('button', { name: '추가', exact: true }).filter({ visible: true }).first().click()
  await page.getByRole('menuitem', { name: '묶음 만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/bundles\?new=1$/)
  const editor = page.getByTestId('bundle-editor')
  await expect(editor).toBeVisible()
  await editor.getByLabel('묶음 이름').fill('검토 두 번')
  await editor.getByRole('button', { name: '단계 추가' }).click()
  await page.getByRole('dialog', { name: '단계 추가' }).getByRole('button', { name: /이 에이전트/ }).click()
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect.poll(() => state.calls.find((c) => c.method === 'POST' && c.path === '/bundles')?.body).toMatchObject({
    name: '검토 두 번', stages: [{ kind: 'agent', ref: '@self' }],
  })
  expect(state.unexpected).toEqual([])
})
