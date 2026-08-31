/**
 * The checks panel, and whether anything can be done from it.
 *
 * It listed problems and offered 다시 검토 — which is a button that finds the
 * same problems again. So the panel was a place to be told about a problem
 * twice, and the work of acting on one was left where it started: find the
 * passage, decide what it should say, type it.
 *
 * What this asserts is that pressing 고치기 changes the document. Not that a
 * request was sent, not that a spinner ran — the section's text is different
 * afterwards and the version has moved, which is the only difference between a
 * button that fixes something and a button that says it will.
 */
import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

/** A section with a fault the linter reliably finds: a Chinese character. */
const FAULTY = '분산 시스템의 威胁 환경은 계속 넓어지고 있다. 대응이 필요하다.\n'

async function state(page: Page) {
  return page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const session = await login.json()
    const headers = {
      'content-type': 'application/json',
      Authorization: `Bearer ${session.accessToken ?? session.access_token}`,
    }
    const listed = await (
      await fetch('/api/artifacts?limit=50', { headers, credentials: 'include' })
    ).json()
    const items: { id: string; kind: string }[] = Array.isArray(listed) ? listed : (listed.items ?? [])
    const report = items.find((a) => a.kind === 'report')!
    const full = await (
      await fetch(`/api/artifacts/${report.id}`, { headers, credentials: 'include' })
    ).json()
    const data = full.data ?? full
    return {
      id: report.id,
      version: full.version as number,
      heading: data.sections[0].heading as string,
      content: data.sections[0].content as string,
      lint: (data.lint ?? []) as { where: string; message: string }[],
    }
  }, E2E_ADMIN)
}

test('고치기를 누르면 그 절이 실제로 바뀐다', async ({ page }) => {
  await signIn(page)

  // Seed a fault and the finding that names it, so the test does not depend on
  // whatever a real generation happened to leave behind.
  const before = await page.evaluate(
    async ([admin, body]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const session = await login.json()
      const headers = {
        'content-type': 'application/json',
        Authorization: `Bearer ${session.accessToken ?? session.access_token}`,
      }
      const listed = await (
        await fetch('/api/artifacts?limit=50', { headers, credentials: 'include' })
      ).json()
      const items: { id: string; kind: string }[] = Array.isArray(listed) ? listed : (listed.items ?? [])
      const report = items.find((a) => a.kind === 'report')!
      const full = await (
        await fetch(`/api/artifacts/${report.id}`, { headers, credentials: 'include' })
      ).json()
      const data = full.data ?? full
      data.sections[0].content = body
      data.sections[0].format = 'markdown'
      data.lint = [
        {
          severity: 'P1',
          rule: 'hanja',
          where: data.sections[0].heading,
          message: '한국어 문장에 중국어 한자가 섞였습니다 — "威胁".',
        },
      ]
      await fetch(`/api/artifacts/${report.id}`, {
        method: 'PATCH',
        headers,
        credentials: 'include',
        body: JSON.stringify({ data }),
      })
      return { id: report.id }
    },
    [E2E_ADMIN, FAULTY] as [typeof E2E_ADMIN, string],
  )
  expect(before.id).toBeTruthy()

  const was = await state(page)
  expect(was.content).toContain('威胁')

  await page.goto('/artifacts')
  await page.getByRole('button', { name: /보고/ }).first().click()
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: '검사 결과' }).click()
  const finding = page.getByText('威胁').first()
  await expect(finding).toBeVisible({ timeout: 10_000 })

  // The button appears on hover, which is deliberate — eight of them at rest
  // is a list of buttons rather than a list of findings.
  const row = page.locator('li', { hasText: '威胁' }).last()
  await row.hover()
  await row.getByRole('button', { name: '고치기' }).click()

  // The document changed. Not a request sent, not a spinner run.
  await expect
    .poll(async () => (await state(page)).content, { timeout: 120_000 })
    .not.toContain('威胁')
  const now = await state(page)
  expect(now.version).toBeGreaterThan(was.version)
})
