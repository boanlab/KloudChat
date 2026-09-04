/** Create, edit and delete on every screen, reachable from the card itself. */
import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })
test.use({ actionTimeout: 10_000 })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const stamp = () => Date.now().toString(36).slice(-5)
const dialog = (page: import('@playwright/test').Page) => page.getByRole('dialog')

test('스킬은 카드에서 지우되, 지우기 전에 물어본다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/skills')
  await page.getByRole('button', { name: '새 스킬' }).first().click()
  await dialog(page).getByLabel('이름').fill(`스킬${tag}`)
  await dialog(page).getByRole('button', { name: '만들기' }).click()

  // Asks first: nothing restores a skill.
  await page.getByRole('button', { name: `스킬${tag} 삭제` }).click()
  await expect(dialog(page).getByRole('heading', { name: `스킬${tag} 삭제` })).toBeVisible()
  await dialog(page).getByRole('button', { name: '취소' }).click()
  await expect(page.getByText(`스킬${tag}`).first()).toBeVisible()

  await page.getByRole('button', { name: `스킬${tag} 삭제` }).click()
  await dialog(page).getByRole('button', { name: '삭제' }).click()
  await page.goto('/skills')
  await expect(page.getByText(`스킬${tag}`)).toHaveCount(0, { timeout: 15_000 })
})

test('에이전트는 카드에서 지우되, 지우기 전에 물어본다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  await dialog(page).getByLabel('이름').fill(`요원${tag}`)
  await dialog(page).getByRole('button', { name: '저장' }).last().click()

  await page.getByRole('button', { name: `요원${tag} 삭제` }).click()
  await expect(dialog(page).getByRole('heading', { name: `요원${tag} 삭제` })).toBeVisible()
  await dialog(page).getByRole('button', { name: '삭제' }).click()
  await page.goto('/agents')
  await expect(page.getByText(`요원${tag}`)).toHaveCount(0, { timeout: 15_000 })
})

test('디자인을 지우기 전에, 기본 모양으로 돌아갈 프로젝트 수를 말한다', async ({ page }) => {
  const tag = stamp()

  await page.goto('/designs')
  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await designs.getByRole('button', { name: '디자인 추가' }).click()
  await designs.getByLabel('이름', { exact: true }).fill(`디자인${tag}`)
  await designs.getByRole('button', { name: '저장', exact: true }).click()
  await expect(designs.locator('li', { hasText: `디자인${tag}` })).toBeVisible({ timeout: 15_000 })

  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).first().click()
  await dialog(page).getByLabel('이름').fill(`옷입은${tag}`)
  await dialog(page).getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 15_000 })
  const projectUrl = page.url()

  // The picker writes optimistically; wait for the PATCH.
  const picker = page.getByLabel('디자인', { exact: true })
  await expect(picker).toBeVisible({ timeout: 15_000 })
  await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/projects\/[0-9a-f]{32}$/.test(r.url()) && r.request().method() === 'PATCH',
    ),
    picker.selectOption({ label: `디자인${tag}` }),
  ])

  await page.goto('/designs')
  await page.getByRole('button', { name: `디자인${tag} 삭제` }).click()
  await expect(dialog(page).getByRole('heading', { name: `디자인${tag} 삭제` })).toBeVisible()
  await expect(dialog(page).getByText('프로젝트 1개가 기본 모양으로 돌아갑니다')).toBeVisible()

  await dialog(page).getByRole('button', { name: '취소' }).click()
  await expect(page.getByRole('button', { name: `디자인${tag} 삭제` })).toBeVisible()

  await page.getByRole('button', { name: `디자인${tag} 삭제` }).click()
  await dialog(page).getByRole('button', { name: '삭제' }).click()
  await page.goto('/designs')
  await expect(page.getByText(`디자인${tag}`)).toHaveCount(0, { timeout: 15_000 })

  // The project falls back to the default look.
  await page.goto(projectUrl)
  await expect(page.getByLabel('디자인', { exact: true })).toHaveValue('', { timeout: 15_000 })
})

test('대화 이름을 사이드바에서 바꾼다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST'),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 15_000 })
  // Let the generated title settle, or it lands on top of the new name.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  // No `hover()`: the row menu has to answer to a finger.
  await openSidebar(page)
  const row = page.locator('aside').locator('div.group').first()
  await row.getByRole('button', { name: '메뉴' }).click()
  await page.getByRole('menuitem', { name: '이름 바꾸기' }).click()

  const box = page.getByLabel('대화 이름')
  await box.fill(`이름${tag}`)
  await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/sessions\/[0-9a-f]{32}$/.test(r.url()) && r.request().method() === 'PATCH',
    ),
    box.press('Enter'),
  ])

  await page.reload()
  await openSidebar(page)
  await expect(page.getByText(`이름${tag}`).first()).toBeVisible({ timeout: 15_000 })
})

test('프로젝트의 이름과 아이콘을 바꾼다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).first().click()
  await dialog(page).getByLabel('이름').fill(`검수${tag}`)
  await dialog(page).getByRole('button', { name: '만들기' }).click()

  await page.getByText(`검수${tag}`).first().click()
  await page.waitForURL(/\/projects\/[0-9a-f]{32}/, { timeout: 15_000 })
  const url = page.url()

  // Rename and delete live under 더 보기.
  await page.getByRole('button', { name: '더 보기', exact: true }).click()
  await page.getByRole('menuitem', { name: '이름 · 설명' }).click()
  await dialog(page).last().getByLabel('이름').fill(`검수${tag}v2`)
  await dialog(page).last().getByRole('button', { name: '저장' }).click()

  // The icon is picked on the icon itself.
  await page.getByRole('button', { name: '아이콘 바꾸기' }).click()
  await page.getByRole('menu').getByRole('button', { name: '📚', exact: true }).click()

  await page.goto(url)
  // Icon and title are separate elements.
  await expect(page.getByRole('heading', { name: `검수${tag}v2` })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByRole('button', { name: '아이콘 바꾸기' })).toHaveText('📚')

  // Deletion asks first: instructions and knowledge files do not come back.
  await page.getByRole('button', { name: '더 보기', exact: true }).click()
  await page.getByRole('menuitem', { name: '프로젝트 삭제' }).click()
  await expect(dialog(page).getByRole('heading', { name: `검수${tag}v2 삭제` })).toBeVisible()
  await dialog(page).getByRole('button', { name: '취소' }).click()
  await expect(page).toHaveURL(url)

  await page.getByRole('button', { name: '더 보기', exact: true }).click()
  await page.getByRole('menuitem', { name: '프로젝트 삭제' }).click()
  await dialog(page).getByRole('button', { name: '삭제', exact: true }).click()
  await expect(page).toHaveURL(/\/projects$/, { timeout: 15_000 })
  await expect(page.getByText(`검수${tag}v2`)).toHaveCount(0)
})

test('아티팩트를 카드에서 지운다', async ({ page }) => {
  // Made here, so there is one to delete.
  await page.goto('/new/chat')
  await page
    .getByLabel('프롬프트 입력')
    .fill('JSON 을 읽어 키별 개수를 세는 파이썬 함수를 예외 처리 포함해 20줄 이상으로 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 15_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })

  await page.goto('/artifacts')
  // Counted off the tab, which counts everything; the grid is one page.
  const all = page.getByRole('tab', { name: /전체/ })
  await expect(all).toHaveText(/전체\s*\d+/, { timeout: 20_000 })
  const before = Number((await all.textContent())?.match(/\d+/)?.[0] ?? '0')
  expect(before).toBeGreaterThan(0)

  const remove = page.getByRole('button', { name: /삭제$/ })
  await expect(remove.first()).toBeVisible({ timeout: 20_000 })
  await remove.first().click()
  await dialog(page).getByRole('button', { name: '삭제' }).click()
  await page.goto('/artifacts')
  await expect(all).toHaveText(new RegExp(`전체\\s*${before - 1}(\\D|$)`), { timeout: 15_000 })
})

test('직접 등록한 커넥터의 자격증명을 다시 넣는다', async ({ page }) => {
  // A self-registered server: catalogue servers need no credential.
  const name = `커스텀 ${Date.now().toString(36)}`
  // Wait for the workspace fetch before clicking through.
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/connectors') && r.ok()),
    page.goto('/connectors'),
  ])
  await page.getByRole('button', { name: /직접 추가|서버 추가|커스텀/ }).first().click()

  const form = dialog(page)
  await form.getByLabel(/이름/).first().fill(name)
  await form.getByRole('button', { name: 'stdio', exact: true }).click()
  await form.getByLabel(/실행 명령/).fill("uvx --with 'mcp<2' mcp-server-time")
  await form.getByLabel(/환경 변수/).fill('DEMO_TOKEN=first-value')
  await form.getByRole('button', { name: /^추가$/ }).last().click()
  await expect(dialog(page)).toHaveCount(0, { timeout: 30_000 })

  const card = page.locator('[data-connector]').filter({ hasText: name })
  await expect(card).toBeVisible({ timeout: 20_000 })
  // The button reads the connector's own credential names.
  await card.getByRole('button', { name: '자격증명' }).click()

  // The stored value is never read back.
  await expect(dialog(page).getByText('저장한 값은 보안을 위해 표시하지 않습니다')).toBeVisible()
  await dialog(page).getByRole('textbox').first().fill('rotated-value')
  await dialog(page).getByRole('button', { name: /저장하고 다시 연결/ }).click()
  await expect(dialog(page)).toHaveCount(0, { timeout: 30_000 })

  await card.getByRole('button', { name: /삭제|제거/ }).first().click().catch(() => {})
  const confirm = dialog(page)
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.getByRole('button', { name: /^삭제$/ }).last().click().catch(() => {})
  }
})
