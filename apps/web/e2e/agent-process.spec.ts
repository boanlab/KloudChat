import { expect, test, type Page } from '@playwright/test'
import { hubFixture, myAgents } from './customize-fixture'

/**
 * An agent's 진행 과정 and 추가로 할 일: 바로 답하기, a base the official agents work by with its
 * 묶음 ticked as the official agent ticks them, or 직접 구성. Items that are the report flow's own
 * are shown as always on; what a surface cannot run is not shown. Fixture data only.
 */

test.use({ serviceWorkers: 'block' })

async function openForm(page: Page) {
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  return form
}

test('기본 과정에서 시작 ticks the base\'s own 묶음; the report flow\'s own checks are always on', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('주간 보고 요약')
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  const base = page.getByTestId('process-base')
  await expect(base.getByRole('combobox', { name: '기본 과정' })).toHaveValue('gaejo-report')
  // The base's own steps, at most five plain lines.
  const steps = base.getByRole('list', { name: '이렇게 일합니다' }).getByRole('listitem')
  await expect(steps).toHaveCount(5)
  await expect(steps.first()).toContainText('올린 자료를 읽습니다')

  const bundles = page.getByTestId('agent-bundles')
  await expect(bundles.getByTestId('bundle-row')).toHaveText([/쓰기 전에 준비하기/, /다 쓴 뒤 검토하기/, /마무리하기/, /두 번 검토하고 PDF로/])
  await expect(bundles.getByRole('checkbox', { name: '쓰기 전에 준비하기' })).toBeChecked()
  await expect(bundles.getByRole('checkbox', { name: '다 쓴 뒤 검토하기' })).toBeChecked()
  await expect(bundles.getByRole('checkbox', { name: '마무리하기' })).toBeChecked()
  await expect(bundles.getByRole('checkbox', { name: '두 번 검토하고 PDF로' })).not.toBeChecked()

  // 다 쓴 뒤 검토하기 › 자세히: its format checks and the repair loop are the flow's own.
  const review = bundles.getByTestId('bundle-row').filter({ hasText: '다 쓴 뒤 검토하기' })
  await review.getByRole('button', { name: '자세히' }).click()
  await expect(review.getByRole('checkbox', { name: '형식 검사' })).toBeDisabled()
  await expect(review.getByRole('checkbox', { name: '형식 검사' })).toBeChecked()
  await expect(review.getByText('이 과정은 늘 합니다').first()).toBeVisible()
  await expect(review.getByRole('checkbox', { name: '자료에 없는 내용이 없는지 근거 확인하기' })).toBeEnabled()
  await expect(bundles).toContainText('형식 검사: 개조식 문체 확인 · 날짜 확인')

  // 마무리하기 › 자세히: the export is off as the official agent has it; ticked, it asks a format.
  const finish = bundles.getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  await finish.getByRole('button', { name: '자세히' }).click()
  const exportBox = finish.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' })
  await expect(exportBox).not.toBeChecked()
  await exportBox.check()
  await finish.getByRole('combobox', { name: '파일 형식' }).selectOption('hwpx')

  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  const put = state.calls.find((c) => c.method === 'PUT' && c.path.endsWith('/process'))
  expect(put?.body).toMatchObject({
    mode: 'base', base: 'gaejo-report',
    bundles: [{ bundle: 'prepare' }, { bundle: 'review' }, { bundle: 'finish', items: { export: 'hwpx' } }],
  })
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a chat base shows what a chat can run: calculations as steps, no export', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  await page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' }).selectOption('data-analysis')
  const bundles = page.getByTestId('agent-bundles')
  await expect(bundles.getByRole('checkbox', { name: '마무리하기' })).not.toBeChecked()
  const prepare = bundles.getByTestId('bundle-row').filter({ hasText: '쓰기 전에 준비하기' })
  await prepare.getByRole('button', { name: '자세히' }).click()
  await expect(prepare.getByRole('checkbox', { name: '날짜 미리 계산하기' })).toBeEnabled()
  await expect(prepare.getByRole('checkbox', { name: '날짜 미리 계산하기' })).not.toBeChecked()
  const finish = bundles.getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  await finish.getByRole('button', { name: '자세히' }).click()
  await expect(finish.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' })).toHaveCount(0)
  const review = bundles.getByTestId('bundle-row').filter({ hasText: '다 쓴 뒤 검토하기' })
  await review.getByRole('button', { name: '자세히' }).click()
  await expect(review.getByRole('checkbox', { name: '수치는 도구로 계산한 결과에서만 인정' })).toBeChecked()
  await expect(review.getByRole('checkbox', { name: '형식 검사' })).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('직접 구성 starts from the agent itself and can be kept as a 묶음', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('두 번 보는 요약')
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  await form.getByRole('radio', { name: /직접 구성/ }).check()
  const custom = page.getByTestId('process-custom')
  await expect(custom.getByTestId('pipeline-step')).toHaveText([/개요 확인/, /두 번 보는 요약/])
  await expect(page.getByTestId('agent-bundles')).toHaveCount(0)
  await custom.getByRole('button', { name: '이 순서를 묶음으로 저장' }).click()
  const dialog = page.getByRole('dialog', { name: '묶음으로 저장' })
  await dialog.getByRole('textbox', { name: /묶음 이름/ }).fill('개요 먼저')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect.poll(() => state.calls.find((c) => c.method === 'POST' && c.path === '/bundles')?.body).toMatchObject({
    name: '개요 먼저', stages: [{ kind: 'human', ref: 'outline' }, { kind: 'agent', ref: '@self' }],
  })
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  expect(state.calls.find((c) => c.method === 'PUT' && c.path.endsWith('/process'))?.body).toMatchObject({
    mode: 'custom', base: 'gaejo-report', stages: [{ kind: 'human', ref: 'outline' }, { kind: 'agent', ref: '@self' }],
  })
  expect(state.problems).toEqual([])
})

test('the detail says how it works in one line, its 묶음 behind 자세히 보기', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto(`/customize/agents/${myAgents[0].id}`)
  const chain = page.getByTestId('process-chain')
  await expect(chain).toContainText('이렇게 일합니다: 올린 자료를 읽습니다 → 빠진 정보가 있으면 먼저 물어봅니다 → 정해진 네 부분으로 개조식 보고를 씁니다', { timeout: 15_000 })
  await chain.getByRole('button', { name: '자세히 보기' }).click()
  await expect(chain).toContainText('쓰기 전에 준비하기')
  await expect(chain).toContainText('마무리하기')
  await expect(page.getByText(/워크플로|작동 단계/)).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('설정 › 메모리 holds what the hub\'s 메모리 tab held', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/settings')
  const tabs = page.getByRole('tablist')
  await expect(tabs.getByRole('tab', { name: '메모리' })).toBeVisible({ timeout: 15_000 })
  await tabs.getByRole('tab', { name: '메모리' }).click()
  await expect(page).toHaveURL(/\/settings\/memory$/)
  await expect(page.getByRole('button', { name: '새 메모리' })).toBeVisible()
  await expect(tabs.getByRole('tab', { name: '테마' })).toBeVisible()
  expect(state.problems).toEqual([])
})

// ── persona A/B fixes ───────────────────────────────────────────────────

test('B-3: 바로 답하기 with 마무리하기 says it cannot export; a document base shows the export choice', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  const bundles = page.getByTestId('agent-bundles')
  const finish = bundles.getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  // Before ticking, the note promises only what this agent can do.
  await expect(finish).not.toContainText('파일로 내보냅니다')
  await bundles.getByRole('checkbox', { name: '마무리하기' }).check()
  await expect(finish.getByTestId('bundle-cannot')).toContainText('파일 내보내기는 문서를 쓰는 과정에서만 됩니다')

  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  // The export is its own choice on the row, not behind 자세히.
  const exportRow = finish.getByTestId('bundle-export')
  await expect(exportRow).toBeVisible()
  await exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' }).check()
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('docx')
  await expect(finish.getByTestId('bundle-cannot')).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('B-7: a base is named with what it makes', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  const select = page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' })
  await expect(select.locator('option', { hasText: '기록 정리 (회의록)' })).toHaveCount(1)
})

test('B-5: a new agent\'s 직접 구성 is judged as the agent it will be, and says what it makes', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('새 보고 도우미')
  await form.getByRole('radio', { name: /직접 구성/ }).check()
  const custom = page.getByTestId('process-custom')
  await custom.getByRole('combobox', { name: '이 에이전트가 만드는 것' }).selectOption('report')
  await expect.poll(() => state.calls.filter((c) => c.path === '/user-pipelines/validate').at(-1)?.body)
    .toMatchObject({ selfSurface: 'report', selfName: '새 보고 도우미' })
})

test('B-6, B-9: 메모리 has a pointer where it used to be; the brightness toggle is not called 테마', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await page.goto('/customize')
  await expect(page.getByTestId('memory-moved')).toContainText('메모리는 설정 › 메모리로 옮겼습니다.', { timeout: 15_000 })
  await expect(page.getByTestId('memory-moved').getByRole('link', { name: '메모리 열기' })).toHaveAttribute('href', '/settings/memory')
  await expect(page.getByRole('button', { name: /^테마: / })).toHaveCount(0)
})
