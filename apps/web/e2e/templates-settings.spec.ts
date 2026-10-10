import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { hubFixture } from './customize-fixture'

/**
 * What a person can now set as the built-ins do: a template of their own made from every field
 * a shipped one sets (G5b/G7), an agent's 진행 과정 설정 (G6/G4), and the connector catalogue an
 * administrator keeps (G10). Fixture data only; the catalogue and the start values are the
 * API's own (`template-fixture.json`).
 */

test.use({ serviceWorkers: 'block' })

async function shot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: `test-results/gaps2/${name}-${testInfo.project.name}.png`, fullPage: true })
}

test('a template made from 회의록 in the editor carries every field the shipped one sets', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/templates/new')
  const form = page.getByTestId('template-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('combobox', { name: '기본 템플릿에서 시작' }).selectOption({ label: '회의록' })
  await expect(form.getByRole('textbox', { name: /머리 항목/ })).toHaveValue(/회의명\n일시\n장소\n참석자\n작성자/)
  await form.getByRole('textbox', { name: '이름', exact: true }).fill('우리 팀 회의록')
  // The document format and the 진행 과정 설정, open to edit.
  await expect(form.getByRole('combobox', { name: '머리 모양' })).toHaveValue('memo')
  await form.getByTestId('template-settings').locator('summary').click()
  await expect(form.getByTestId('settings-stages').getByRole('checkbox', { name: /실행 항목 칸 보정/ })).toBeChecked()
  await form.getByRole('textbox', { name: '검토 규칙' }).fill('결정마다 근거가 된 발언을 적는다.')
  await shot(page, testInfo, 'template-editor')
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/templates\/ut-/)
  const posted = state.calls.find((c) => c.method === 'POST' && c.path === '/user-templates')?.body as Record<string, any>
  expect(posted.name).toBe('우리 팀 회의록')
  expect(posted.docFormat).toMatchObject({ head: 'memo', numbering: 'none', fields: ['회의명', '일시', '장소', '참석자', '작성자'] })
  expect(posted.questions).toHaveLength(3)
  expect(posted.process).toMatchObject({ stages: ['minutes-repair', 'minutes-reground'], date_base: 'meeting',
    rules: ['결정마다 근거가 된 발언을 적는다.'] })
  expect(state.problems).toEqual([])
})

test('an agent sets its own 진행 과정 설정 over its template\'s', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  await form.getByRole('textbox', { name: '이름', exact: true }).fill('주간 보고 요약')
  await form.getByRole('radio', { name: /기본 과정에서 시작/ }).check()
  const group = page.getByTestId('agent-settings-group')
  await group.locator('summary').click()
  await group.getByRole('checkbox', { name: /이 에이전트에서 따로 정하기/ }).check()
  const editor = group.getByTestId('process-settings')
  await editor.getByTestId('settings-prepass').getByRole('checkbox', { name: /날짜 계산/ }).check()
  await editor.getByRole('checkbox', { name: /웹 검색 안 함/ }).check()
  await editor.getByRole('textbox', { name: '검토 규칙' }).fill('수치마다 단위를 붙인다.')
  await editor.getByRole('textbox', { name: /목차/ }).fill('현황\n검토\n결정 요청')
  await editor.getByRole('checkbox', { name: /고정 목차/ }).check()
  await shot(page, testInfo, 'agent-process-settings')
  await form.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/a9999/)
  const posted = state.calls.find((c) => c.method === 'POST' && c.path === '/agents')?.body as Record<string, any>
  expect(posted.processSettings).toMatchObject({ prepass: ['date-calc'], search: false, rules: ['수치마다 단위를 붙인다.'],
    fixed_outline: true, structure: { sections: ['현황', '검토', '결정 요청'] } })
  expect(state.problems).toEqual([])
})

test('an administrator adds a catalogue entry; stored values never come back', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { admin: true })
  await page.goto('/admin/system/connectors')
  const admin = page.getByTestId('connector-catalog-admin')
  await expect(admin.getByTestId('catalog-entry')).toHaveCount(2, { timeout: 15_000 })
  await expect(admin).toContainText('게이트웨이 키: stt')
  await shot(page, testInfo, 'connector-catalog')
  await admin.getByRole('button', { name: '항목 추가' }).click()
  const dialog = page.getByTestId('catalog-entry-form')
  await dialog.getByRole('textbox', { name: '이름', exact: true }).fill('사내 위키')
  await dialog.getByRole('textbox', { name: /슬러그/ }).fill('wiki')
  await dialog.getByRole('textbox', { name: /주소/ }).fill('https://wiki.example.com/mcp')
  await dialog.getByRole('button', { name: '변수 추가' }).click()
  await dialog.getByRole('textbox', { name: '변수 이름' }).fill('API_TOKEN')
  await dialog.getByRole('textbox', { name: '값' }).fill('s3cret')
  await shot(page, testInfo, 'connector-catalog-entry')
  await page.getByRole('button', { name: '저장' }).click()
  await expect(admin.getByTestId('catalog-entry')).toHaveCount(3)
  expect(state.calls.find((c) => c.method === 'POST' && c.path === '/admin/connector-catalog')?.body).toMatchObject({
    slug: 'wiki', transport: 'http', endpoint: 'https://wiki.example.com/mcp', env: { API_TOKEN: 's3cret' } })
  // Edited again, the stored value is not shown: only that one is set.
  await admin.getByRole('button', { name: '사내 위키 편집' }).click()
  await expect(page.getByTestId('catalog-entry-form').getByRole('textbox', { name: '값' })).toHaveValue('')
  await expect(page.getByTestId('catalog-entry-form').getByRole('textbox', { name: '값' })).toHaveAttribute('placeholder', /저장됨/)
  expect(state.problems).toEqual([])
})
