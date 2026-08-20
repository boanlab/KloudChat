import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Selecting several rows and removing them together, on every list that has one.
 *
 * Stubbed rather than seeded: what is under test is the client's half — that a
 * checkbox reaches the bulk endpoint with the right ids, and that the rows
 * leave the screen. Creating six kinds of real row to delete would make this a
 * test of the fixtures.
 */
const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { ...init, headers: { ...(init?.headers || {}), Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

async function capture(page: Page, path: string) {
  const sent: string[][] = []
  await page.route(`**${path}`, async (route) => {
    sent.push((route.request().postDataJSON() as { ids: string[] }).ids)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 2 }) })
  })
  return sent
}

test('프로젝트를 여러 개 골라 한 번에 지운다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // Two of our own, so the selection has something to hold.
  const names = [`묶음삭제 A ${Date.now()}`, `묶음삭제 B ${Date.now()}`]
  for (const name of names) {
    await page.evaluate(
      async ([fn, n]) =>
        await eval(fn as string)('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: n, description: '', emoji: '🧪', instructions: '' }),
        }),
      [AS_USER, name] as const,
    )
  }

  // Not stubbed: this one goes all the way to the database, so the route and
  // the screen are held to the same claim.
  const sent: string[][] = []
  page.on('request', (r) => {
    if (r.url().endsWith('/api/projects/delete') && r.method() === 'POST') {
      sent.push((r.postDataJSON() as { ids: string[] }).ids)
    }
  })
  await page.goto('/projects')
  for (const name of names) {
    await page.getByRole('checkbox', { name: `${name} 선택` }).check()
  }
  await expect(page.getByText('2개 선택됨')).toBeVisible()

  await page.getByRole('button', { name: '선택 삭제' }).click()
  await expect(page.getByText('2개를 삭제할까요?')).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click()

  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0]).toHaveLength(2)
  for (const name of names) {
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  }

  // And gone from the server, not just from this tab.
  const left = await page.evaluate(
    async ([fn, a, b]) => {
      const rows = (await eval(fn as string)('/api/projects')) as { name: string }[]
      return rows.filter((r) => r.name === a || r.name === b).length
    },
    [AS_USER, names[0], names[1]] as const,
  )
  expect(left).toBe(0)
  console.log('보낸 ids:', sent[0].length, '개 · 서버에 남은 것:', left)
})

test('결과물·스킬·에이전트·커넥터 화면에도 같은 선택 막대가 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  for (const [path, label] of [
    ['/artifacts', '결과물'],
    ['/skills', '스킬'],
    ['/agents', '에이전트'],
    ['/connectors', '커넥터'],
  ] as const) {
    await page.goto(path)
    const boxes = page.getByRole('checkbox', { name: /선택$/ })
    await expect(boxes.first()).toBeVisible({ timeout: 30_000 })
    await boxes.first().check()
    await expect(page.getByText('1개 선택됨')).toBeVisible()
    await expect(page.getByRole('button', { name: '선택 삭제' })).toBeVisible()
    console.log(`${label}: 선택 막대 확인`)
    await page.getByRole('button', { name: '선택 해제' }).click()
    await expect(page.getByText('1개 선택됨')).toHaveCount(0)
  }
})
