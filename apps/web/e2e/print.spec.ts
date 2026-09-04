import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init || {}),
    headers: { ...((init || {}).headers || {}), Authorization: 'Bearer ' + accessToken },
  })
  if (!r.ok || r.status === 204) return null
  return r.json()
}`

/** Printing a report puts only the report on paper. */
test('인쇄하면 보고서만 종이에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // Seeded, so the content is known.
  const title = `인쇄 확인 보고서 ${Date.now().toString(36)}`
  const artifact = await page.evaluate(
    async ([fn, body]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    [
      AS_USER,
      {
        kind: 'report',
        title,
        data: {
          kind: 'report',
          citationStyle: 'APA',
          sources: [],
          wordCount: 0,
          sections: [
            {
              id: 'p1',
              heading: '배경',
              level: 1,
              status: 'done',
              content:
                '전이학습은 큰 말뭉치에서 익힌 표현을 다른 과제로 옮겨 쓴다. ' +
                '소량 데이터에서 특히 유리한 이유는 표현 학습의 비용을 이미 치렀기 때문이다.',
            },
            {
              id: 'p2',
              heading: '한계',
              level: 1,
              status: 'done',
              content:
                '원 과제와 목표 과제의 도메인이 멀어질수록 이득은 빠르게 줄어든다. ' +
                '의료 영상처럼 분포가 크게 다른 자료에서는 미세조정 비용이 오히려 커진다.',
            },
          ],
        },
      },
    ] as const,
  )

  // The print tree is mounted by the report panel.
  await page.goto('/artifacts')
  const card = page
    .locator('div')
    .filter({ has: page.getByText(title, { exact: true }) })
    .filter({ has: page.locator('button.aspect-video') })
    .last()
  await card.locator('button.aspect-video').first().click()
  const doc = page.locator('[data-print-doc]')
  await expect(doc).toHaveCount(1, { timeout: 20_000 })
  // Hidden on screen.
  await expect(doc).toBeHidden()

  await page.emulateMedia({ media: 'print' })
  await expect(page.locator('#root')).toBeHidden()
  await expect(doc).toBeVisible()

  // Every heading and the prose under them.
  const printed = (await doc.innerText()).trim()
  expect(printed, '제목이 인쇄본에 없다').toContain(title)
  for (const heading of ['배경', '한계']) {
    expect(printed, `"${heading}" 절이 인쇄본에 없다`).toContain(heading)
  }
  expect(printed, '본문이 인쇄본에 없다').toContain('도메인이 멀어질수록')

  // No controls.
  await expect(doc.getByRole('button')).toHaveCount(0)
  for (const label of ['이 절만 다시 쓰기', '목차', '내보내기', '수정']) {
    expect(printed, `인쇄본에 "${label}" 가 남아 있다`).not.toContain(label)
  }

  await page.evaluate(
    async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`, { method: 'DELETE' }),
    [AS_USER, artifact.id] as const,
  )
})
