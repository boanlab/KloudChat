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
  // The first base writes 개조식, a 공문-family format: its file is HWPX unless the person picks.
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('hwpx')
  await expect(finish.getByTestId('bundle-cannot')).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('B-7: a base is named with what it makes', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  const select = page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' })
  // R3: what it makes is in brackets only when it adds words: 「개조식 보고서」 adds none.
  await expect(select.locator('option', { hasText: /^개조식 보고$/ })).toHaveCount(1)
  await expect(select.locator('option', { hasText: '(개조식 보고서)' })).toHaveCount(0)
  // A base named for what it makes says it once.
  await expect(select.locator('option', { hasText: /^회의록$/ })).toHaveCount(1)
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

test('B-6, B-9: 사용자 지정 does not narrate where things moved; the brightness toggle is not called 테마', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await page.goto('/customize')
  await expect(page.getByRole('tabpanel')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/옮겼습니다/)).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^테마: / })).toHaveCount(0)
})

// ── persona A/B, 2nd run ─────────────────────────────────────────────────

const shotDir = process.env.SHOT_DIR ?? 'test-results/shots'
async function shot(page: Page, name: string, target?: import('@playwright/test').Locator) {
  const file = `${shotDir}/${name}-${test.info().project.name}.png`
  if (target) await target.screenshot({ path: file })
  else await page.screenshot({ path: file, fullPage: false })
}

test('N3: 바로 답하기 says it answers in the chat, and a file asked for in the 지시 offers a document base', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await expect(form.getByRole('radio', { name: /바로 답하기/ })).toBeChecked()
  await expect(form.getByRole('radio', { name: /바로 답하기/ })).toHaveAccessibleName(/파일로 받으려면 기본 과정을 고르세요/)
  // 마무리하기 names the export before anything is ticked, with where it runs.
  const bundles = page.getByTestId('agent-bundles')
  const finish = bundles.getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  await expect(finish).toContainText('파일로 내보내기(문서 과정에서)')
  await expect(page.getByTestId('file-suggestion')).toHaveCount(0)

  await form.getByRole('textbox', { name: '이름' }).fill('과장 보고 1쪽')
  await form.getByRole('textbox', { name: '지시' }).fill('과장님께 올릴 1쪽 요약을 쓴다. 다 쓰면 워드(DOCX) 파일로 준다.')
  const suggestion = page.getByTestId('file-suggestion')
  await expect(suggestion).toContainText('바로 답하기는 대화로만 답하고 파일을 만들지 않습니다')
  await shot(page, 'n3-file-suggestion', page.getByTestId('agent-process'))
  await suggestion.getByRole('button', { name: /개조식 보고.*과정으로 바꾸기/ }).click()
  await expect(form.getByRole('radio', { name: /기본 과정에서 시작/ })).toBeChecked()
  await expect(page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' })).toHaveValue('gaejo-report')
  await expect(suggestion).toHaveCount(0)
  // M3: the person asked for a file, so the export comes ticked, as DOCX.
  const exportRow = finish.getByTestId('bundle-export')
  await expect(exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' })).toBeChecked()
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('docx')
  expect(state.problems).toEqual([])
})

test('M3: the switch a file asked for ticks the export in the format the 지시 names', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('주간 결과 보고')
  await form.getByRole('textbox', { name: '지시' }).fill('주간 결과를 정리한다. 한글 파일(HWPX)로 준다.')
  await page.getByTestId('file-suggestion').getByRole('button', { name: /과정으로 바꾸기/ }).click()
  const finish = page.getByTestId('agent-bundles').getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  const exportRow = finish.getByTestId('bundle-export')
  await expect(exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' })).toBeChecked()
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('hwpx')
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  const body = state.calls.find((c) => c.method === 'PUT' && /^\/agents\/[^/]+\/process$/.test(c.path))?.body as {
    bundles?: { bundle: string; items: Record<string, unknown> }[]
  }
  expect(body.bundles?.find((b) => b.bundle === 'finish')?.items.export).toBe('hwpx')
  expect(state.problems).toEqual([])
})

test('N3: the 마무리하기 warning switches to a document base in one click', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  const finish = page.getByTestId('agent-bundles').getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  await page.getByTestId('agent-bundles').getByRole('checkbox', { name: '마무리하기' }).check()
  await finish.getByTestId('bundle-cannot').getByRole('button', { name: '기본 과정(보고서)으로 바꾸기' }).click()
  await expect(form.getByRole('radio', { name: /기본 과정에서 시작/ })).toBeChecked()
  await expect(finish.getByTestId('bundle-export').getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' })).toBeChecked()
  expect(state.problems).toEqual([])
})

/** The server's verdict on a new agent's own steps, as `compose.validate` words it. */
async function judgeSteps(page: Page) {
  await page.route('**/api/user-pipelines/validate', async (route) => {
    const body = route.request().postDataJSON() as {
      stages: { kind: string; ref: string }[]; selfSurface?: string; selfName?: string; questions?: unknown[]
    }
    const exportAt = body.stages.findIndex((s) => s.kind === 'export') + 1
    const gapsAt = body.stages.findIndex((s) => s.kind === 'human' && s.ref === 'gaps') + 1
    const problems = exportAt && body.selfSurface !== 'report'
      ? [{ step: exportAt, code: 'needs_product', message: `${exportAt}번 내보내기(내보내기): 앞 단계에서 만든 문서가 있어야 합니다.` }]
      : []
    const warnings = gapsAt && !(body.questions ?? []).length
      ? [{ step: gapsAt, code: 'nothing_to_ask', message: `${gapsAt}번 사람 확인(확인 질문): 뒤 에이전트(${body.selfName})는 물을 항목이 정해져 있지 않아 이 단계는 건너뜁니다.` }]
      : []
    await route.fulfill({ json: { ok: problems.length === 0, surface: body.selfSurface === 'report' ? 'report' : 'chat', problems, warnings, titles: [] } })
  })
}

async function addStep(page: Page, group: string, name: string) {
  await page.getByTestId('process-custom').getByRole('button', { name: '단계 추가' }).click()
  const picker = page.getByRole('dialog', { name: '단계 추가' })
  await picker.getByRole('region', { name: group }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
  await expect(picker).toBeHidden()
}

test('B-5, N2, N1: 직접 구성 points at what fixes each problem, and a question is checked before saving', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await judgeSteps(page)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('새 보고 도우미')
  await form.getByRole('radio', { name: /직접 구성/ }).check()
  const custom = page.getByTestId('process-custom')
  await addStep(page, '사람 확인', '확인 질문')
  await addStep(page, '내보내기', '내보내기')
  // 확인 질문 before the agent.
  await custom.getByTestId('pipeline-step').nth(1).getByRole('button', { name: '위로' }).click()
  await expect(custom.getByTestId('pipeline-step')).toHaveText([/확인 질문/, /새 보고 도우미/, /내보내기/])

  // B-5 / N8: the export problem leads to 「이 에이전트가 만드는 것」.
  await expect(custom).toContainText('앞 단계에서 만든 문서가 있어야 합니다')
  await shot(page, 'b5-export-fix', custom)
  const makes = custom.getByRole('combobox', { name: '이 에이전트가 만드는 것' })
  await expect(makes).toHaveValue('chat')
  // M3: the button sets it, and the notice can put it back.
  await custom.getByTestId('export-fix').click()
  await expect(makes).toHaveValue('report')
  await expect(makes).toBeFocused()
  await expect(custom).not.toContainText('앞 단계에서 만든 문서가 있어야 합니다')
  const toast = page.getByTestId('toast')
  await expect(toast).toContainText('「이 에이전트가 만드는 것」을 보고서(문서)로 바꿨습니다.')
  await toast.getByRole('button', { name: '실행 취소' }).click()
  await expect(makes).toHaveValue('chat')
  await expect(custom).toContainText('앞 단계에서 만든 문서가 있어야 합니다')
  await custom.getByTestId('export-fix').click()
  await expect(makes).toHaveValue('report')
  await expect(custom).not.toContainText('앞 단계에서 만든 문서가 있어야 합니다')

  // N2: the 확인 질문 warning follows the questions on the form.
  await expect(custom).toContainText('이 단계는 건너뜁니다')
  await custom.getByTestId('ask-fix').click()
  const group = page.getByTestId('agent-gap-group')
  await expect(group).toHaveAttribute('open', '')
  const question = group.getByTestId('gap-question').first()
  await expect(question.getByRole('textbox', { name: '물어볼 것' })).toBeFocused()
  await question.getByRole('textbox', { name: '물어볼 것' }).fill('과장님이 결정해야 할 사항은 무엇인가요?')
  await expect(custom).not.toContainText('이 단계는 건너뜁니다')

  // N1: the words a 낱말 question needs are marked, and checked here, beside the field.
  await expect(question.getByRole('textbox', { name: '이 낱말이 있으면 답한 것' })).toHaveAttribute('aria-required', 'true')
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(form.getByRole('status').first()).toContainText('빠진 정보 질문에 채우지 않은 칸이 있습니다')
  const words = question.getByRole('textbox', { name: '이 낱말이 있으면 답한 것' })
  await expect(words).toHaveAttribute('aria-invalid', 'true')
  await expect(question.getByRole('alert')).toHaveText('답이 있다고 볼 낱말을 하나 이상 적으세요. 쉼표로 나눕니다.')
  await expect(words).toBeFocused()
  await shot(page, 'n1-gap-required', group)
  expect(state.calls.some((c) => c.method === 'POST' && c.path === '/agents')).toBe(false)
  await expect(page.getByText(/gap_[a-z_]+/)).toHaveCount(0)

  await words.fill('결정, 승인, 요청')
  await expect(question.getByRole('alert')).toHaveCount(0)
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  expect(state.calls.find((c) => c.method === 'POST' && c.path === '/agents')?.body).toMatchObject({
    gapQuestions: [{ question: '과장님이 결정해야 할 사항은 무엇인가요?', detector: { type: 'keyword', words: ['결정', '승인', '요청'] } }],
  })
  expect(state.problems).toEqual([])
})

test('N1: a refusal the form cannot foresee reads as a sentence and opens where to fix it', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await page.route('**/api/agents', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 422, json: { detail: 'gap_pattern_unsafe' } })
      : route.fallback())
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '이름' }).fill('패턴 질문')
  await page.getByTestId('agent-gap-group').locator('summary').click()
  await page.getByTestId('agent-gap-group').getByRole('button', { name: '질문 추가' }).click()
  const question = page.getByTestId('gap-question').first()
  await question.getByRole('textbox', { name: '물어볼 것' }).fill('기한은 언제인가요?')
  await question.getByTestId('gap-detector').selectOption('regex')
  // Nested repeats pass the form's own check; the server refuses them.
  await question.getByRole('textbox', { name: '패턴' }).fill('(\\d{1,2}일){1,3}')
  // Folded again, so the refusal has to open it.
  await page.getByTestId('agent-gap-group').locator('summary').click()
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(form.getByRole('status').first()).toHaveText('빠진 정보 질문: 패턴은 300자 안에서 반복 횟수를 {0,10}처럼 정해서 쓰고, 반복 안에 반복을 넣지 않습니다.')
  await expect(page.getByTestId('agent-gap-group')).toHaveAttribute('open', '')
  await expect(page.getByText('gap_pattern_unsafe')).toHaveCount(0)

  await page.unroute('**/api/agents')
  await page.route('**/api/agents', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 422, json: { detail: 'unknown_tools:mcp__gone__x' } })
      : route.fallback())
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(form.getByRole('status').first()).toHaveText('스킬과 도구 › 도구에 지금 쓸 수 없는 도구가 있습니다. 빼고 저장하세요.')
  await expect(page.getByTestId('agent-tools-group')).toHaveAttribute('open', '')
  await expect(page.getByText(/unknown_tools|mcp__gone/)).toHaveCount(0)
})

test('N5: the process\'s detailed settings are not called 진행 과정 설정', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  const group = page.getByTestId('agent-settings-group')
  await expect(group.locator('summary')).toContainText('세부 설정')
  await expect(group.locator('summary')).toContainText('진행 과정의 계산·검사·목차')
  await expect(page.getByText('진행 과정 설정', { exact: true })).toHaveCount(0)
  await shot(page, 'n5-settings-group', page.getByTestId('agent-process').locator('..'))
})

test('B-10: after choosing a base, the new agent\'s template list holds every template of that screen', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  const row = (id: string, surface: string, name: string, kind = 'document') => ({
    id, kind, surface, name, nameEn: name, category: '보고', categoryEn: 'Report', description: '', descriptionEn: '',
    fills: [], fillsEn: [], checks: [], hasPreview: false, slideCount: 0,
  })
  const report = [row('doc-gaejo-report', 'report', '개조식 보고서'), row('doc-minutes', 'report', '회의록'),
    row('doc-result-report', 'report', '결과 보고서'), row('doc-comparison', 'report', '비교·분석 보고서'),
    row('u-1', 'report', '내 주간 보고')]
  await page.route('**/api/design-templates', (route) =>
    route.fulfill({ json: [...report, row('deck-briefing', 'slides', '사내 브리핑', 'deck'), row('img-1', 'image', '그림', 'image')] }))
  const form = await openForm(page)
  const output = page.getByTestId('agent-output-group')
  await output.locator('summary').click()
  const select = output.getByRole('combobox', { name: /^템플릿/ })
  // 바로 답하기 on the chat screen: nothing to write in.
  await expect(select.locator('option')).toHaveText(['정하지 않음'])
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  await expect(select.locator('option')).toHaveText(['기본 과정의 템플릿', ...report.map((r) => `보고서 · ${r.name}`)])
  await select.selectOption('doc-result-report')
  await expect(select).toHaveValue('doc-result-report')
})

test('R2: ticking the export by hand after choosing a base picks the format the 지시 names', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const form = await openForm(page)
  await form.getByRole('textbox', { name: '지시' }).fill('과장님께 올릴 1쪽 요약을 쓴다. 다 쓰면 워드 파일로 준다.')
  // The base radio, not the switch button.
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  await expect(page.getByTestId('process-base').getByRole('combobox', { name: '기본 과정' })).toHaveValue('gaejo-report')
  const finish = page.getByTestId('agent-bundles').getByTestId('bundle-row').filter({ hasText: '마무리하기' })
  const exportRow = finish.getByTestId('bundle-export')
  await exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' }).check()
  // 개조식's own default is HWPX; the 지시 says 워드, so DOCX.
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('docx')
  await exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' }).uncheck()
  await form.getByRole('textbox', { name: '지시' }).fill('주간 결과를 정리한다. PDF로 준다.')
  await exportRow.getByRole('checkbox', { name: '다 쓰면 파일로 내보내기' }).check()
  await expect(exportRow.getByRole('combobox', { name: '파일 형식' })).toHaveValue('pdf')
  expect(state.problems).toEqual([])
})
