import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { ...init, headers: { ...(init?.headers || {}), Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

/** Tab counts come from a workspace-wide query, so deleting rows must refresh them. */
test('결과물을 지우면 탭의 숫자도 같이 줄어든다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // Two charts, so the 차트 tab has a number that must move.
  for (const n of [1, 2]) {
    await page.evaluate(
      async ([fn, i]) =>
        await eval(fn as string)('/api/artifacts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: 'chart',
            title: `숫자확인 ${i} ${Date.now()}`,
            data: {
              chartType: 'bar',
              caption: '',
              xLabel: '',
              yLabel: '',
              series: [{ name: 's', color: '#5b53e8', points: [{ x: 'a', y: 1 }] }],
              table: { columns: ['x', 'y'], rows: [['a', 1]] },
              sourceFile: '',
            },
          }),
        }),
      [AS_USER, n] as const,
    )
  }

  await page.goto('/artifacts')
  const chartTab = page.getByRole('tab', { name: /차트/ })
  await expect(chartTab).toBeVisible({ timeout: 30_000 })
  // The counts are a second request.
  await expect
    .poll(async () => Number((await chartTab.textContent())?.replace(/\D/g, '') || 0), {
      timeout: 20_000,
      message: '차트 개수가 오지 않았습니다',
    })
    .toBeGreaterThanOrEqual(2)
  const before = Number((await chartTab.textContent())?.replace(/\D/g, '') || 0)
  console.log('삭제 전 차트 탭:', before)

  await chartTab.click()
  const boxes = page.getByRole('checkbox', { name: /숫자확인/ })
  await expect(boxes.first()).toBeVisible({ timeout: 20_000 })
  await boxes.nth(0).check()
  await boxes.nth(1).check()
  await page.getByRole('button', { name: '선택 삭제' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click()

  await expect
    .poll(async () => Number((await chartTab.textContent())?.replace(/\D/g, '') || 0), {
      timeout: 20_000,
      message: '탭 숫자가 그대로입니다',
    })
    .toBe(before - 2)
  console.log('삭제 후 차트 탭:', before - 2)
})
