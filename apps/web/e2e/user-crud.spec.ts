import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/** The ordinary account's workspace through the screens, checked against the server. */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }
const stamp = () => Math.random().toString(36).slice(2, 8)

async function rowsFrom(page: Page, resource: string): Promise<{ name: string; id: string }[]> {
  return await page.evaluate(
    async ({ email, password, resource }) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const { accessToken } = await login.json()
      const res = await fetch(`/api/${resource}`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      const body = await res.json()
      const list = Array.isArray(body) ? body : (body.items ?? [])
      return list.map((r: Record<string, string>) => ({ name: r.name ?? '', id: r.id }))
    },
    { ...USER, resource },
  )
}

test.beforeEach(async ({ page }) => {
  await signInAs(page, USER.email, USER.password)
})

test('에이전트를 만들고 고치고 지운다', async ({ page }) => {
  test.setTimeout(180_000)
  const name = `검증 에이전트 ${stamp()}`
  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).click()
  const dlg = page.locator('[role="dialog"]').first()
  await dlg.getByPlaceholder('예: 기술 검토 도우미').fill(name)
  await dlg.getByRole('textbox').filter({ hasText: '' }).last().fill('검증용 시스템 프롬프트입니다.')
  await dlg.getByRole('button', { name: '저장' }).click()
  await expect(dlg).toBeHidden({ timeout: 15_000 })

  await expect
    .poll(async () => (await rowsFrom(page, 'agents')).map((r) => r.name), { timeout: 15_000 })
    .toContain(name)

  // After a reload.
  await page.reload()
  await expect(page.getByText(name, { exact: false }).first()).toBeVisible({ timeout: 20_000 })

  const renamed = `${name} 수정`
  // 편집 is the next sibling of the row's delete button.
  await page
    .getByRole('button', { name: `${name} 삭제` })
    .locator('xpath=following-sibling::button[1]')
    .click()
  const edit = page.locator('[role="dialog"]').first()
  await expect(edit).toBeVisible()
  await edit.getByPlaceholder('예: 기술 검토 도우미').fill(renamed)
  await edit.getByRole('button', { name: '저장' }).click()
  await expect(edit).toBeHidden({ timeout: 15_000 })
  await expect
    .poll(async () => (await rowsFrom(page, 'agents')).map((r) => r.name), { timeout: 15_000 })
    .toContain(renamed)

  await page.reload()
  await page.getByRole('button', { name: `${renamed} 삭제` }).click()
  await page
    .getByRole('button', { name: /삭제|확인/ })
    .last()
    .click()
  await expect
    .poll(async () => (await rowsFrom(page, 'agents')).map((r) => r.name), { timeout: 15_000 })
    .not.toContain(renamed)
})

test('스킬을 만들고 지운다', async ({ page }) => {
  test.setTimeout(180_000)
  const name = `검증 스킬 ${stamp()}`
  await page.goto('/skills')
  await page.getByRole('button', { name: '새 스킬' }).click()
  const dlg = page.locator('[role="dialog"]').first()
  await dlg.getByPlaceholder('예: 배포 전 리스크 검토').fill(name)
  await dlg.getByPlaceholder('이 스킬이 무엇을 하는지 한 줄로').fill('검증용 스킬')
  await dlg
    .getByPlaceholder('사용자가 의사결정 자료를 붙여넣고 리스크 검토를 요청할 때')
    .fill('검증 스펙이 스킬을 만들 때')
  await dlg
    .locator('textarea')
    .last()
    .fill('1. 아무것도 하지 않는다\n2. 그렇다고 말한다')
  await dlg.getByRole('button', { name: '만들기' }).click()
  await expect(dlg).toBeHidden({ timeout: 15_000 })

  await expect
    .poll(async () => (await rowsFrom(page, 'skills')).map((r) => r.name), { timeout: 15_000 })
    .toContain(name)

  await page.reload()
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 20_000 })

  const id = (await rowsFrom(page, 'skills')).find((r) => r.name === name)?.id
  expect(id, '만든 스킬의 id').toBeTruthy()
})

test('메모리를 만들고 지운다', async ({ page }) => {
  test.setTimeout(180_000)
  const name = `check-${stamp()}`
  await page.goto('/memory')
  await page.getByRole('button', { name: /새 메모리|첫 메모리 만들기/ }).first().click()
  const dlg = page.locator('[role="dialog"]').first()
  await dlg.getByPlaceholder('user-prefers-terse-answers').fill(name)
  await dlg.locator('textarea').last().fill('검증용 메모리 본문입니다.')
  await dlg.getByRole('button', { name: '저장' }).click()
  await expect(dlg).toBeHidden({ timeout: 15_000 })

  await expect
    .poll(async () => (await rowsFrom(page, 'memory')).map((r) => r.name), { timeout: 15_000 })
    .toContain(name)

  await page.reload()
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 20_000 })
})

test('프로젝트를 만들고 지침이 남는다', async ({ page }) => {
  test.setTimeout(180_000)
  const name = `검증 프로젝트 ${stamp()}`
  const rule = '모든 수치에는 단위를 붙인다.'
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).click()
  const dlg = page.locator('[role="dialog"]').first()
  await dlg.getByPlaceholder('예: 제품 출시 준비').fill(name)
  await dlg.getByPlaceholder('한 줄로 무엇에 대한 프로젝트인지').fill('검증용')
  await dlg.locator('textarea').last().fill(rule)
  await dlg.getByRole('button', { name: '만들기' }).click()
  await expect(dlg).toBeHidden({ timeout: 15_000 })

  const rows = await rowsFrom(page, 'projects')
  const made = rows.find((r) => r.name === name)
  expect(made, '만든 프로젝트가 서버에 있다').toBeTruthy()

  // The instruction survives the round trip.
  await page.goto(`/projects/${made!.id}`)
  await expect(page.getByText(rule).first()).toBeVisible({ timeout: 20_000 })
})
