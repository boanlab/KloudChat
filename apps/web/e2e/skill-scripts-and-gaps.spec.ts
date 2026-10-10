import { expect, test, type Locator, type Page } from '@playwright/test'
import { hubFixture, myAgents, mySkills } from './customize-fixture'

/**
 * A person can set up what built-ins have: the skill form names shipped scripts and checks for
 * the server to run (「서버가 돌리는 점검」) and lists the skill's own scripts, which run only in
 * the sandbox; an upload shows its scripts with their scan; the agent form edits its
 * 「빠진 정보 질문」 beside the ones its 템플릿 asks. Fixture data only; nothing reaches a server.
 * With GAPS_SHOTS set to a directory, each screen is saved there.
 */

test.use({ serviceWorkers: 'block' })

const shots = process.env.GAPS_SHOTS ?? ''

async function shot(page: Page, name: string, tag: string, part?: Locator) {
  if (!shots) return
  await page.waitForTimeout(300)
  if (part) {
    // A tall window, so the part is drawn whole rather than cut by the scrolling frame.
    const size = page.viewportSize()!
    await page.setViewportSize({ width: size.width, height: 3200 })
    await page.waitForTimeout(300)
    await part.screenshot({ path: `${shots}/${tag}-${name}.png` })
    await page.setViewportSize(size)
  }
  else await page.screenshot({ path: `${shots}/${tag}-${name}.png`, fullPage: true })
}

const TIDY = '"""표의 빈 행과 합계 행을 지운다."""\nimport json\nimport sys\n\nrows = json.loads(sys.argv[1]).get("rows", [])\nprint(json.dumps({"ok": True, "rows": len(rows)}))\n'

test('skill form: shipped scripts and checks by name, own scripts listed for the sandbox', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const mine = {
    ...mySkills[1], scriptFiles: [{ name: 'tidy', summary: '표의 빈 행과 합계 행을 지운다.', size: 160, source: TIDY }],
    review: 'pending', visibility: 'private',
    server: { stage: '', entry: '', postCheck: [], postJudge: false, copyCheck: false, copyKinds: [], tables: false, ownStage: false, uses: ['tidy'] },
  }
  await page.route('**/api/skills', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [mySkills[0], mine, mySkills[2]] }) : route.fallback())
  await page.route(`**/api/skills/${mine.id}`, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    state.calls.push({ method: 'PATCH', path: `/skills/${mine.id}`, body })
    await route.fulfill({ json: { ...mine, ...body } })
  })
  await page.goto(`/customize/skills/${mine.id}`)
  const form = page.getByTestId('skill-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  const group = page.getByTestId('skill-server-group')
  // Opened by the skill's own scripts; the review notice says why sharing waits.
  await expect(group).toHaveAttribute('open', '')
  await expect(group.getByText('스크립트가 있어 관리자가 승인한 뒤에 다른 사람에게 공유됩니다.')).toBeVisible()
  const server = page.getByTestId('skill-server')
  await expect(server.getByTestId('skill-script')).toContainText('scripts/tidy.py')
  await server.getByRole('checkbox', { name: /출처 점검/ }).check()
  await server.getByRole('switch', { name: '자료에 없는 문장도 모델이 찾기' }).click()
  await server.getByRole('textbox', { name: '특히 볼 것' }).fill('가격과 일정')
  await server.getByRole('combobox', { name: '검사 스킬로 쓰기' }).selectOption('post')
  await expect(server.getByTestId('skill-entry')).toHaveValue('grounding-check/trace')
  await server.getByRole('checkbox', { name: /날짜 계산/ }).check()
  await shot(page, 'skill-form-scripts', testInfo.project.name, group)
  await form.getByRole('button', { name: '저장', exact: true }).click()
  await expect(form.getByText('저장했습니다.')).toBeVisible()
  const saved = state.calls.find((c) => c.method === 'PATCH' && c.path === `/skills/${mine.id}`)
  expect(saved?.body).toMatchObject({ server: { postCheck: ['source-check/post'], postJudge: '가격과 일정', stage: 'post',
    entry: 'grounding-check/trace', uses: ['tidy', 'date-calc/dates'] } })
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('upload: a kept script shows its code and its findings by file and line', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const item = {
    filename: 'table-tidy.zip', ok: true, name: '표 정리', description: '첨부한 표의 빈 행과 합계 행을 정리합니다.',
    whenToUse: '첨부한 표를 정리할 때', kinds: ['chat'], requiredTools: ['run_skill_script'], version: '1.0.0', bodyChars: 380,
    estimatedTokens: 150, kept: ['scripts/tidy.py', 'scripts/fetch.py', 'SKILL.md'], excluded: [{ path: 'scripts/setup.sh', reason: 'script' }],
    findings: [
      { severity: 'warn', code: 'scripts_excluded', message: '', file: 'table-tidy.zip', line: null },
      { severity: 'warn', code: 'script_network', message: '', file: 'scripts/fetch.py', line: 1 },
      { severity: 'warn', code: 'script_file_write', message: '', file: 'scripts/fetch.py', line: 6 },
    ],
    server: { stage: '', entry: '', postCheck: ['source-check/post'], postJudge: true, copyCheck: false, copyKinds: [], tables: true,
      ownStage: false, uses: ['tidy', 'fetch'] },
    scripts: [
      { name: 'fetch', summary: '공개 통계 표를 받아 온다.', size: 240, source: 'import urllib.request\n\nurl = "https://example.test/stats.csv"\ndata = urllib.request.urlopen(url).read()\n\nopen("/tmp/stats.csv", "wb").write(data)\n' },
      { name: 'tidy', summary: '표의 빈 행과 합계 행을 지운다.', size: 160, source: TIDY },
    ],
  }
  await page.route('**/api/skills/import**', (route) =>
    route.fulfill({ json: { dryRun: true, items: [item], created: [] } }))
  await page.goto('/customize/skills/upload')
  await expect(page.getByRole('heading', { level: 1, name: '스킬 업로드' })).toBeVisible({ timeout: 15_000 })
  await page.getByLabel('스킬 파일 고르기').setInputFiles({ name: 'table-tidy.zip', mimeType: 'application/zip', buffer: Buffer.from('PK') })
  const card = page.getByTestId('skill-preview')
  await expect(card.getByTestId('skill-preview-scripts')).toContainText('스크립트 2개 · 코드 실행 샌드박스에서만 실행')
  await expect(card.locator('[data-severity="warn"]').filter({ hasText: '네트워크' })).toContainText('scripts/fetch.py')
  await expect(card.getByTestId('skill-preview-server')).toContainText('source-check/post')
  await card.getByTestId('skill-script').first().getByRole('button', { name: '코드 보기' }).click()
  await expect(card.locator('pre')).toContainText('urlopen')
  await shot(page, 'upload-preview', testInfo.project.name, card)
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('agent form: 빠진 정보 질문 editor beside the template\'s questions', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름' }).fill('행사 안내문')
  const group = page.getByTestId('agent-gap-group')
  await group.locator('summary').click()
  await group.getByRole('button', { name: '질문 추가' }).click()
  const first = group.getByTestId('gap-question').first()
  await first.getByRole('textbox', { name: '물어볼 것' }).fill('행사 장소는 어디인가요?')
  await first.getByRole('textbox', { name: '짧은 이름' }).fill('장소')
  await first.getByRole('textbox', { name: '답이 없으면 남길 빈칸' }).fill('[장소]')
  await first.getByRole('textbox', { name: '왜 묻는지' }).fill('안내문 첫 줄에 장소가 들어갑니다.')
  const words = first.getByRole('textbox', { name: '이 낱말이 있으면 답한 것' })
  await words.fill('장소, 강당, 홀')
  await words.blur()
  await group.getByRole('button', { name: '질문 추가' }).click()
  const second = group.getByTestId('gap-question').nth(1)
  await second.getByRole('textbox', { name: '물어볼 것' }).fill('예산 상한이 있나요?')
  await second.getByTestId('gap-detector').selectOption('judge')
  await group.getByRole('button', { name: '질문 추가' }).click()
  const third = group.getByTestId('gap-question').nth(2)
  await third.getByRole('textbox', { name: '물어볼 것' }).fill('참석자는 누구인가요?')
  await third.getByTestId('gap-detector').selectOption('builtin')
  await third.getByRole('combobox', { name: '찾는 것' }).selectOption('attendees')
  await shot(page, 'agent-gap-questions', testInfo.project.name, group)
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999999999999999999999999999999$/)
  const created = state.calls.find((c) => c.method === 'POST' && c.path === '/agents')
  expect(created?.body.gapQuestions).toEqual([
    { id: 'q1', label: '장소', question: '행사 장소는 어디인가요?', why: '안내문 첫 줄에 장소가 들어갑니다.', placeholder: '[장소]',
      detector: { type: 'keyword', words: ['장소', '강당', '홀'] } },
    { id: 'q2', label: '', question: '예산 상한이 있나요?', why: '', placeholder: '', detector: { type: 'judge' } },
    { id: 'q3', label: '', question: '참석자는 누구인가요?', why: '', placeholder: '', detector: { type: 'builtin', name: 'attendees' } },
  ])
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
  if (testInfo.project.name === 'phone') {
    const width = await page.evaluate(() => document.documentElement.scrollWidth)
    expect(width).toBeLessThanOrEqual(390)
  }
})

test('agent form: the template\'s questions stay read only; the base\'s seed the agent\'s own list', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const agent = {
    ...myAgents[0], renderTemplateId: 'doc-gaejo-report', gapQuestions: null,
    inheritedQuestions: [
      { id: 'addressee', label: '보고 대상', question: '누구에게 올리는 보고인가요? (예: 시설과장님)', why: '', placeholder: '[보고 대상]',
        detector: { type: 'regex', pattern: '보고\\s{0,2}대상' } },
      { id: 'reporter', label: '보고자', question: '보고자(부서·이름)는 누구인가요?', why: '', placeholder: '[보고자·부서]',
        detector: { type: 'regex', pattern: '보고자' } },
    ],
    baseQuestions: [
      { id: 'deadline_basis', label: '기한 기준', question: '며칠을 넘으면 기한을 넘긴 것으로 볼까요?', why: '', placeholder: '[기한 기준]',
        detector: { type: 'builtin', name: 'meeting_date' } },
    ],
  }
  await page.route('**/api/agents', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: [agent, myAgents[1]] }) : route.fallback())
  await page.goto(`/customize/agents/${agent.id}`)
  const group = page.getByTestId('agent-gap-group')
  await expect(group).toBeVisible({ timeout: 15_000 })
  await group.locator('summary').click()
  await expect(group.getByTestId('gap-inherited')).toContainText('누구에게 올리는 보고인가요?')
  await expect(group.getByTestId('gap-question')).toHaveCount(1)
  await expect(group.getByTestId('gap-question').getByRole('textbox', { name: '물어볼 것' })).toHaveValue('며칠을 넘으면 기한을 넘긴 것으로 볼까요?')
  await shot(page, 'agent-gap-inherited', testInfo.project.name, group)
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})
