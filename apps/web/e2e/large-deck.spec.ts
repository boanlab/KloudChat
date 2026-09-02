import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

test('60장 덱을 열고 끝까지 탐색해 PPTX와 PDF로 내보낸다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const title = `대용량 덱 검증 ${Date.now()}`
  const id = await page.evaluate(async ([account, deckTitle]) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    const auth = await login.json()
    const response = await fetch('/api/artifacts', {
      method: 'POST',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` },
      body: JSON.stringify({
        kind: 'deck', title: deckTitle,
        data: {
          kind: 'deck', title: deckTitle,
          slides: Array.from({ length: 60 }, (_, index) => ({
            id: `large-${index + 1}`,
            layout: index === 0 ? 'title' : 'bullets',
            title: index === 0 ? deckTitle : `${index + 1}번 장`,
            body: index === 0 ? '60장 탐색·내보내기 성능 검증' : undefined,
            bullets: index === 0 ? undefined : [`${index + 1}번 장의 첫 번째 핵심 내용`, '저장과 내보내기에서 유지할 두 번째 내용'],
            accent: '#0f766e',
          })),
          design: { accent: '#0f766e', visualStyle: 'editorial' },
        },
      }),
    })
    if (!response.ok) throw new Error(`대용량 덱 생성 실패: ${response.status}`)
    return (await response.json()).id as string
  }, [E2E_ADMIN, title] as const)

  try {
    await page.goto('/artifacts')
    await page.getByRole('tab', { name: /^슬라이드/ }).click()
    const openedAt = Date.now()
    await page.getByRole('button', { name: `${title} 열기` }).click()
    const panel = page.getByRole('dialog')
    await expect(panel.getByText(title).first()).toBeVisible({ timeout: 10_000 })
    expect(Date.now() - openedAt).toBeLessThan(5_000)

    const thumbnails = panel.locator('button.aspect-video')
    await expect(thumbnails).toHaveCount(60)
    await thumbnails.nth(59).click()
    await expect(panel.getByText('60번 장').first()).toBeVisible()
    await panel.getByRole('tab', { name: '보기', exact: true }).click()
    // 「60장」 배지가 여기 있었다. 누를 수 없는 숫자였고, 바로 옆 목록이
    // 예순 장을 그려 놓고 다시 「60장」이라고 적는 자리였다 — 그래서 없앴다.
    // 남은 것은 목록을 여닫는 버튼이고, 세는 일은 그 버튼이 한다.
    await expect(panel.getByRole('button', { name: '장 목록' })).toHaveText(/60\/60|60장/)

    const exports = await page.evaluate(async ([account, artifactId]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: account.email, password: account.password }),
      })
      const auth = await login.json()
      const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
      const results = []
      for (const format of ['pptx', 'pdf']) {
        const started = performance.now()
        const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers })
        const bytes = new Uint8Array(await response.arrayBuffer())
        results.push({ format, status: response.status, size: bytes.length, signature: String.fromCharCode(...bytes.slice(0, 4)), elapsed: performance.now() - started })
      }
      return results
    }, [E2E_ADMIN, id] as const)
    for (const file of exports) {
      expect(file.status).toBe(200)
      expect(file.size).toBeGreaterThan(10_000)
      expect(file.signature).toBe(file.format === 'pdf' ? '%PDF' : 'PK\u0003\u0004')
      expect(file.elapsed).toBeLessThan(30_000)
    }
  } finally {
    await page.evaluate(async ([account, artifactId]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: account.email, password: account.password }),
      })
      const auth = await login.json()
      await fetch(`/api/artifacts/${artifactId}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` },
      })
    }, [E2E_ADMIN, id] as const)
  }
})

test('40절 장문 보고서를 조판하고 DOCX·PDF·HWPX로 내보낸다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const title = `장문 보고서 검증 ${Date.now()}`
  const id = await page.evaluate(async ([account, reportTitle]) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    const auth = await login.json()
    const response = await fetch('/api/artifacts', {
      method: 'POST',
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` },
      body: JSON.stringify({
        kind: 'report', title: reportTitle,
        data: {
          kind: 'report', title: reportTitle,
          sections: Array.from({ length: 40 }, (_, index) => ({
            id: `section-${index + 1}`,
            heading: `${index + 1}절 검토 결과`,
            content: `${index + 1}절의 첫 문단입니다. 장문 문서에서도 페이지와 내보낸 파일에 빠짐없이 남아야 합니다.\n\n- 검토 항목 A\n- 검토 항목 B`,
          })),
          sources: [],
          design: { accent: '#0f766e', visualStyle: 'editorial' },
        },
      }),
    })
    if (!response.ok) throw new Error(`장문 보고서 생성 실패: ${response.status}`)
    return (await response.json()).id as string
  }, [E2E_ADMIN, title] as const)

  try {
    await page.goto('/artifacts')
    await page.getByRole('tab', { name: /^보고서/ }).click()
    const openedAt = Date.now()
    await page.getByRole('button', { name: `${title} 열기` }).click()
    const panel = page.getByRole('dialog')
    await expect(panel.getByText(title).first()).toBeVisible({ timeout: 10_000 })
    expect(Date.now() - openedAt).toBeLessThan(5_000)
    await panel.getByRole('tab', { name: '홈', exact: true }).click()
    await panel.getByRole('button', { name: '페이지뷰' }).click()
    const preview = panel.getByLabel('실제 페이지 미리보기')
    await expect(preview).toHaveAttribute('data-page-count', /\d+/, { timeout: 30_000 })
    expect(Number(await preview.getAttribute('data-page-count'))).toBeGreaterThan(5)
    const lastSection = panel.getByText('40절의 첫 문단입니다.', { exact: false }).last()
    await lastSection.scrollIntoViewIfNeeded()
    await expect(lastSection).toBeVisible()

    const exports = await page.evaluate(async ([account, artifactId]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: account.email, password: account.password }),
      })
      const auth = await login.json()
      const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
      const results = []
      for (const format of ['docx', 'pdf', 'hwpx']) {
        const started = performance.now()
        const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers })
        const bytes = new Uint8Array(await response.arrayBuffer())
        results.push({ format, status: response.status, size: bytes.length, signature: String.fromCharCode(...bytes.slice(0, 4)), elapsed: performance.now() - started })
      }
      return results
    }, [E2E_ADMIN, id] as const)
    for (const file of exports) {
      expect(file.status).toBe(200)
      expect(file.size).toBeGreaterThan(1_000)
      expect(file.signature).toBe(file.format === 'pdf' ? '%PDF' : 'PK\u0003\u0004')
      expect(file.elapsed).toBeLessThan(30_000)
    }
  } finally {
    await page.evaluate(async ([account, artifactId]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: account.email, password: account.password }),
      })
      const auth = await login.json()
      await fetch(`/api/artifacts/${artifactId}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` },
      })
    }, [E2E_ADMIN, id] as const)
  }
})
