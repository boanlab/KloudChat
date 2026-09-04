import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Bulk selection and deletion on every list that has it. */
const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { ...init, headers: { ...(init?.headers || {}), Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('프로젝트를 여러 개 골라 한 번에 지운다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // Two rows to select.
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

  // Goes all the way to the database.
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

  // Gone from the server.
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

  // Brings its own row.
  await page.evaluate(
    async (fn) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'code',
          title: `선택막대 확인 ${Date.now()}`,
          data: { kind: 'code', content: 'print(1)', language: 'python' },
        }),
      }),
    AS_USER,
  )

  for (const [path, label] of [
    ['/artifacts', '결과물'],
    ['/skills', '스킬'],
    ['/agents', '에이전트'],
    ['/connectors', '커넥터'],
  ] as const) {
    await page.goto(path)
    const boxes = page.getByRole('checkbox', { name: /선택$/ })
    // A screen with no rows is logged as skipped.
    if ((await boxes.count()) === 0) {
      await page.waitForTimeout(2_000)
    }
    if ((await boxes.count()) === 0) {
      console.log(`${label}: 지울 수 있는 행이 없어 건너뜀`)
      continue
    }
    await boxes.first().check()
    await expect(page.getByText('1개 선택됨')).toBeVisible()
    await expect(page.getByRole('button', { name: '선택 삭제' })).toBeVisible()
    console.log(`${label}: 선택 막대 확인`)
    await page.getByRole('button', { name: '선택 해제' }).click()
    await expect(page.getByText('1개 선택됨')).toHaveCount(0)
  }
})

test('디자인도 여러 개 골라 한 번에 지운다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const names = [`묶음디자인 A ${Date.now()}`, `묶음디자인 B ${Date.now()}`]
  for (const name of names) {
    await page.evaluate(
      async ([fn, n]) =>
        await eval(fn as string)('/api/designs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: n, body: '', imageStyle: '' }),
        }),
      [AS_USER, name] as const,
    )
  }

  await page.goto('/designs')
  for (const name of names) {
    await page.getByRole('checkbox', { name: `${name} 선택` }).check()
  }
  await expect(page.getByText('2개 선택됨')).toBeVisible()
  await page.getByRole('button', { name: '선택 삭제' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click()

  for (const name of names) {
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  }
  const left = await page.evaluate(
    async ([fn, a, b]) => {
      const rows = (await eval(fn as string)('/api/designs')) as { name: string }[]
      return rows.filter((r) => r.name === a || r.name === b).length
    },
    [AS_USER, names[0], names[1]] as const,
  )
  expect(left).toBe(0)
  console.log('디자인 삭제 후 서버에 남은 것:', left)
})
