/**
 * Create, edit and delete on every screen.
 *
 * Walks each screen checking that the actions are reachable from the card
 * itself rather than buried inside a modal.
 */
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.describe.configure({ mode: 'serial' })
test.use({ actionTimeout: 10_000 })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const stamp = () => Date.now().toString(36).slice(-5)
const dialog = (page: import('@playwright/test').Page) => page.getByRole('dialog')

test('스킬을 카드에서 바로 지운다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/skills')
  await page.getByRole('button', { name: '새 스킬' }).first().click()
  await dialog(page).getByLabel('이름').fill(`스킬${tag}`)
  await dialog(page).getByRole('button', { name: '만들기' }).click()

  await page.getByRole('button', { name: `스킬${tag} 삭제` }).click()
  await page.goto('/skills')
  await expect(page.getByText(`스킬${tag}`)).toHaveCount(0, { timeout: 15_000 })
})

test('에이전트를 카드에서 바로 지운다', async ({ page }) => {
  const tag = stamp()
  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).first().click()
  await dialog(page).getByLabel('이름').fill(`요원${tag}`)
  await dialog(page).getByRole('button', { name: '저장' }).last().click()

  await page.getByRole('button', { name: `요원${tag} 삭제` }).click()
  await page.goto('/agents')
  await expect(page.getByText(`요원${tag}`)).toHaveCount(0, { timeout: 15_000 })
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
  // Titles are generated from the first turn, so wait for the one being renamed
  // to settle — otherwise the generated title lands on top of the new name.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  const row = page.locator('aside').locator('div.group').first()
  await row.hover()
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

  await page.getByRole('button', { name: '이름 · 설명' }).click()
  await dialog(page).last().getByLabel('이름').fill(`검수${tag}v2`)
  await dialog(page).last().getByRole('button', { name: '📚', exact: true }).first().click()
  await dialog(page).last().getByRole('button', { name: '저장' }).click()

  await page.goto(url)
  await expect(page.getByText(`📚 검수${tag}v2`).first()).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '프로젝트 삭제' }).click()
})

test('아티팩트를 카드에서 지운다', async ({ page }) => {
  // Chat is the only producer, so the artifact this test deletes is one it made.
  await page.goto('/new/chat')
  await page
    .getByLabel('프롬프트 입력')
    .fill('JSON 을 읽어 키별 개수를 세는 파이썬 함수를 예외 처리 포함해 20줄 이상으로 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 15_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })

  await page.goto('/artifacts')
  const remove = page.getByRole('button', { name: /삭제$/ })
  await expect(remove.first()).toBeVisible({ timeout: 20_000 })
  const before = await remove.count()

  await remove.first().click()
  await page.goto('/artifacts')
  await expect(page.getByRole('button', { name: /삭제$/ })).toHaveCount(before - 1, {
    timeout: 15_000,
  })
})

test('직접 등록한 커넥터의 자격증명을 다시 넣는다', async ({ page }) => {
  // Credentials were supplied once at install and never again, so a rotated key
  // meant removing the connector and rebuilding its tool settings from scratch.
  //
  // Tested against a self-registered server rather than a catalogue one: the
  // catalogue holds only servers that need no credential. The button reads the
  // connector's own key names, so a custom connector — the one kind that must
  // carry your credentials — can have them changed.
  const name = `커스텀 ${Date.now().toString(36)}`
  // Wait for the workspace fetch, not for a rendered count — the counts come
  // from the store, and clicking through before it lands leaves the page
  // showing only what the optimistic insert knew.
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
  // The button exists because the connector carries credential *names* of its
  // own — a custom server has no catalogue entry to read them from.
  await card.getByRole('button', { name: '자격증명' }).click()

  // The stored value is never read back, so the field asks for the new one.
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
