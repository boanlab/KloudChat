import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

async function storedSlide(page: import('@playwright/test').Page, id: string) {
  return page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })).json()
    return full.data.slides[0]
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
}

test('장별 강조색과 그림 설명을 수정하고 그림을 제거할 수 있다', async ({ page }) => {
  await signIn(page)
  const id = await page.evaluate(async ([admin, png]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const listed = await (await fetch('/api/artifacts?kind=deck', { headers })).json()
    const first = (Array.isArray(listed) ? listed : listed.items)[0]
    const full = await (await fetch(`/api/artifacts/${first.id}`, { headers })).json()
    const data = full.data ?? full
    data.slides[0] = { ...data.slides[0], layout: 'bullets', title: '디자인 확인', bullets: ['선택한 부분만 바뀌어야 합니다'], richText: undefined, textScale: undefined, chart: undefined, rows: undefined, image: { src: png, caption: '이전 설명' } }
    await fetch(`/api/artifacts/${first.id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    return first.id as string
  }, [E2E_ADMIN, PNG] as [typeof E2E_ADMIN, string])

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByLabel('슬라이드 편집 도구')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '이 장 다시 만들기' })).toBeVisible()
  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await expect(page.getByLabel('슬라이드 편집 도구')).toBeVisible()
  await page.getByLabel('도구막대 강조색').fill('#123456')
  await page.getByLabel('도구막대 레이아웃').selectOption('two-column')
  await page.locator('[data-slide-element="image"]').click()
  await expect(page.getByText('그림 선택됨')).toBeVisible()
  await expect(page.locator('[data-slide-element="image"]')).toHaveAttribute('data-selected', 'true')
  await page.getByRole('button', { name: '그림 영역 채우기' }).click()
  await page.getByLabel('그림 크기').selectOption('large')
  await expect(page.getByRole('button', { name: '슬라이드 편집 실행 취소' })).toBeEnabled()
  await page.getByRole('button', { name: '슬라이드 편집 실행 취소' }).click()
  await expect(page.getByLabel('그림 크기')).toHaveValue('medium')
  await page.getByRole('button', { name: '슬라이드 편집 다시 실행' }).click()
  await expect(page.getByLabel('그림 크기')).toHaveValue('large')
  await page.getByRole('button', { name: '그림 왼쪽' }).click()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('button', { name: '그림 오른쪽' })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('button', { name: '그림 왼쪽' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('그림 설명').fill('수정한 그림 설명')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByLabel('그림 설명')).toBeHidden({ timeout: 20_000 })

  let stored = await storedSlide(page, id)
  expect(stored.accent).toBe('#123456')
  expect(stored.layout).toBe('two-column')
  expect(stored.textScale).toBeUndefined()
  expect(stored.image.caption).toBe('수정한 그림 설명')
  expect(stored.image.fit).toBe('cover')
  expect(stored.image.size).toBe('large')
  expect(stored.image.position).toBe('left')

  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await page.locator('[data-slide-element="image"]').click()
  await page.keyboard.press('Delete')
  await expect(page.locator('[data-slide-element="image"]')).toHaveCount(0)
  await page.getByRole('button', { name: '슬라이드 편집 실행 취소' }).click()
  await page.locator('[data-slide-element="image"]').click()
  await page.keyboard.press('Delete')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await page.getByRole('tab', { name: '삽입' }).click()
  await expect(page.getByRole('button', { name: '그림 넣기' })).toBeVisible({ timeout: 20_000 })
  stored = await storedSlide(page, id)
  expect(stored.image).toBeUndefined()

  await page.getByRole('tab', { name: '홈' }).click()
  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await page.getByLabel('로컬 그림 업로드').setInputFiles({
    name: '내그림.png',
    mimeType: 'image/png',
    buffer: Buffer.from(PNG.split(',')[1], 'base64'),
  })
  await expect(page.locator('img[src^="data:image/png;base64,"]').first()).toBeVisible()
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByRole('tab', { name: '편집', exact: true })).toBeVisible({ timeout: 20_000 })
  stored = await storedSlide(page, id)
  expect(stored.image.src).toContain('data:image/png;base64,')

  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await page.locator('[contenteditable="true"]').filter({ hasText: '선택한 부분만 바뀌어야 합니다' }).evaluate((element) => {
    const text = element.firstChild?.firstChild ?? element.firstChild
    if (!text) throw new Error('제목 텍스트를 찾지 못했습니다.')
    const range = document.createRange()
    range.setStart(text, 4)
    range.setEnd(text, 7)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  await page.getByRole('button', { name: '선택한 글자 굵게' }).click()
  await expect(page.getByRole('button', { name: '선택한 글자 굵게' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('선택한 글자 크기').selectOption('140')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByRole('tab', { name: '편집', exact: true })).toBeVisible({ timeout: 20_000 })
  stored = await storedSlide(page, id)
  expect(stored.bullets[0]).toBe('선택한 부분만 바뀌어야 합니다')
  expect(stored.richText['bullets.0']).toMatch(/^선택한 /)
  expect(stored.richText['bullets.0']).toMatch(/ 바뀌어야 합니다$/)
  expect(stored.richText['bullets.0']).toMatch(/<(b|strong)>/)
  expect(stored.richText['bullets.0']).toContain('font-size:1.4em')
  await page.getByRole('tab', { name: '편집', exact: true }).click()
  const visibleFormatting = await page.locator('[contenteditable="true"]').filter({ hasText: '선택한 부분만 바뀌어야 합니다' }).evaluate((element) => {
    const selected = element.querySelector('[style*="font-size:1.4em"]') as HTMLElement | null
    if (!selected) return null
    return {
      html: element.innerHTML,
      selectedSize: Number.parseFloat(getComputedStyle(selected).fontSize),
      normalSize: Number.parseFloat(getComputedStyle(element).fontSize),
      selectedWeight: Number.parseInt(getComputedStyle(selected).fontWeight, 10),
    }
  })
  expect(visibleFormatting).not.toBeNull()
  expect(visibleFormatting!.html).toMatch(/^<span>선택한 /)
  expect(visibleFormatting!.html).toMatch(/ 바뀌어야 합니다<\/span>$/)
  expect(visibleFormatting!.selectedSize).toBeGreaterThan(visibleFormatting!.normalSize)
  expect(visibleFormatting!.selectedWeight).toBeGreaterThanOrEqual(600)
  await page.getByRole('button', { name: '편집 취소', exact: true }).click()
})
