import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { officeFixture } from './office-fixture'

/**
 * The HWP panel beside a chat, against a stand-in editor (`fixtures/fake-rhwp.html`): it
 * loads the saved version, saves from the version it loaded, gives way to the agent's
 * versions, locks while a turn runs, and asks about unsaved edits before one starts.
 */

test.use({ serviceWorkers: 'block' })

const hwpx = readFileSync(new URL('./fixtures/hwp-official.hwpx', import.meta.url))
const studio = readFileSync(new URL('./fixtures/fake-rhwp.html', import.meta.url), 'utf8')

type Options = { conflict?: boolean; holdTurn?: Promise<void>; fromHome?: boolean }

async function fixture(page: Page, options: Options = {}) {
  await page.route('**/rhwp/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: studio }))
  return officeFixture(page, {
    kind: 'hwp', file: hwpx, agentFile: Buffer.concat([hwpx, Buffer.from('agent')]),
    title: '직원 연수 참석 요청', ...options,
  })
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const editor = (page: Page) => page.frameLocator('[data-hwp-panel] iframe')
const studioCalls = (page: Page) =>
  page.frames().find((f) => f.url().includes('/rhwp/'))!.evaluate(
    () => (window as unknown as { fakeStudio: { calls: { method: string; size: number | null }[] } })
      .fakeStudio.calls,
  )
const handEdit = (page: Page, text: string) =>
  page.frames().find((f) => f.url().includes('/rhwp/'))!.evaluate(
    (value) => (window as unknown as { fakeStudio: { edit: (t: string) => void } }).fakeStudio.edit(value),
    text,
  )

test('the saved file opens in the editor and a hand edit saves as the next version', async ({ page }) => {
  const state = await fixture(page)
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  const header = panel(page).locator('header')
  await expect(header.getByText('v1', { exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: '저장' })).toBeDisabled()

  await handEdit(page, '손으로 고친 공문')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await header.getByRole('button', { name: '저장' }).click()
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toBeHidden()
  expect(state.saves).toEqual([{ expected: '1', size: 4 + Buffer.byteLength('손으로 고친 공문') }])
  expect((await studioCalls(page)).map((c) => c.method)).toEqual(
    ['loadFile', 'exportHwpx', 'notifySaved'],
  )
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
})

test('a save that lost to a newer version says so and loads the latest on request', async ({ page }) => {
  const state = await fixture(page, { conflict: true })
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  await handEdit(page, '늦은 편집')
  const header = panel(page).locator('header')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await header.getByRole('button', { name: '저장' }).click()
  const notice = panel(page).getByRole('status')
  await expect(notice).toContainText('그사이 다른 판이 저장되어 저장하지 않았습니다.')
  await notice.getByRole('button', { name: '최신 판 불러오기' }).click()
  await expect(notice).toBeHidden()
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  expect(state.unexpected).toEqual([])
})

test('the editor locks while the agent works, then shows its version; the trail counts the steps', async ({ page }) => {
  let release!: () => void
  const holdTurn = new Promise<void>((resolve) => (release = resolve))
  const state = await fixture(page, { holdTurn })
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)

  // a document order, answered here in the editor rather than handed to the report screen
  await page.getByRole('textbox').first().fill('직원 연수 참석을 요청하는 공문을 작성해 주세요')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('hwp-agent-lock')).toBeVisible()
  await expect(panel(page).getByText('에이전트가 문서를 고치는 중…')).toBeVisible()
  release()

  await expect(page.getByTestId('hwp-agent-lock')).toBeHidden()
  await expect(panel(page).locator('header').getByText('v2', { exact: true })).toBeVisible()
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length + 5}`)
  await expect(page.getByText('· 문서 확인 2회 · 문서 편집 2회')).toBeVisible()
  expect(state.sent).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('unsaved hand edits are saved before the agent turn when the person says so', async ({ page }) => {
  const state = await fixture(page)
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  await handEdit(page, '보내기 전 편집')
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()

  await page.getByRole('textbox').first().fill('일시는 2027년 2월 11일 10:00-12:00')
  await page.keyboard.press('Enter')
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  expect(state.sent).toHaveLength(0)
  await ask.getByRole('button', { name: '저장하고 보내기' }).click()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  // the agent's version came after the hand save
  await expect(panel(page).locator('header').getByText('v3', { exact: true })).toBeVisible()
  expect(state.unexpected).toEqual([])
})

test('cancelling keeps the edits and sends nothing', async ({ page }) => {
  const state = await fixture(page)
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  await handEdit(page, '아직 쓰는 중')
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()
  await page.getByRole('textbox').first().fill('장소는 중앙홀')
  await page.keyboard.press('Enter')
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: '취소' }).click()
  await expect(ask).toBeHidden()
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()
  expect(state.sent).toHaveLength(0)
  expect(state.saves).toHaveLength(0)
})

test('on a phone the document opens to read, without a save button @phone', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
  const state = await fixture(page)
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  const header = panel(page).locator('header')
  await expect(header.getByText('직원 연수 참석 요청')).toBeVisible()
  await expect(header.getByRole('button', { name: '저장' })).toHaveCount(0)
  await expect(header.getByRole('button', { name: '내려받기' })).toBeEnabled()
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
})

test('home opens a blank HWP document from the report surface\'s 한글 pick', async ({ page }) => {
  const state = await fixture(page, { fromHome: true })
  // no document card beside the surfaces: the file is the report's
  await expect(page.getByRole('button', { name: '한글 문서', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '빈 문서로 시작' })).toHaveCount(0)
  await page.getByRole('group', { name: '만들 것' }).getByRole('button', { name: '보고서' }).click()
  // the 형식 chip in the prompt box, as the picture options are chips
  await expect(page.getByRole('button', { name: /^형식/ })).toContainText('웹 보고서')
  await page.getByRole('button', { name: /^형식/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '한글', exact: true }).click()
  await page.getByRole('button', { name: '빈 문서로 시작' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-z_-]+/)
  await expect(editor(page).locator('#doc')).toHaveText(`document.hwpx ${hwpx.length}`)
  expect(state.created).toEqual([{ sessionId: expect.any(String), title: '새 한글 문서' }])
  expect(state.unexpected).toEqual([])
})
