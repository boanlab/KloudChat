import { expect, test } from '@playwright/test'
import {
  E2E_ADMIN, deckHeader, deckLayout, deckSelect, deckSelection, deckStage, deckTextSize, deckThumbs,
  deckToolbar, deckToolbarMenu, saveDeck, signIn,
} from './helpers'

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
  // No 편집 toggle: the toolbar is there from the start, the slide's steps in 더보기.
  await expect(deckToolbar(page)).toBeVisible({ timeout: 20_000 })
  await deckThumbs(page).first().click()
  await deckHeader(page).getByRole('button', { name: '더보기' }).click()
  await expect(page.getByRole('menuitem', { name: '이 장 다시 만들기' })).toBeVisible()
  await page.keyboard.press('Escape')

  // 테마 › 이 장의 강조색, then 레이아웃 › 두 단
  const design = await deckToolbarMenu(page, '테마')
  await design.getByLabel('이 장의 강조색').fill('#123456')
  await page.keyboard.press('Escape')
  await deckLayout(page, '두 단')

  const image = deckStage(page).locator('[data-slide-element="image"]')
  await deckSelect(page, 'image')
  await expect(image).toHaveAttribute('data-selected', 'true')
  await expect(deckSelection(page).getByLabel('그림 설명')).toBeVisible()
  // the selected picture's controls are in the 이미지 menu
  let pictures = await deckToolbarMenu(page, '이미지')
  await pictures.getByRole('button', { name: '그림 영역 채우기' }).click()
  pictures = await deckToolbarMenu(page, '이미지')
  await pictures.getByRole('button', { name: '그림 크기 크게' }).click()
  await page.keyboard.press('Escape')
  // undo and redo cover the picture's size like any other edit
  const undo = deckToolbar(page).getByRole('button', { name: '실행 취소' })
  await expect(undo).toBeEnabled()
  await undo.click()
  pictures = await deckToolbarMenu(page, '이미지')
  await expect(pictures.getByRole('button', { name: '그림 크기 보통' })).toHaveClass(/\bon\b/)
  await page.keyboard.press('Escape')
  // Ctrl+Y redoes it, as in the PowerPoint editor
  await page.keyboard.press('Control+y')
  pictures = await deckToolbarMenu(page, '이미지')
  await expect(pictures.getByRole('button', { name: '그림 크기 크게' })).toHaveClass(/\bon\b/)
  await pictures.getByRole('button', { name: '그림 오른쪽' }).click()
  await expect(pictures.getByRole('button', { name: '그림 오른쪽' })).toHaveClass(/\bon\b/)
  await pictures.getByRole('button', { name: '그림 왼쪽' }).click()
  await expect(pictures.getByRole('button', { name: '그림 왼쪽' })).toHaveClass(/\bon\b/)
  await page.keyboard.press('Escape')
  await deckSelect(page, 'image')
  await deckSelection(page).getByLabel('그림 설명').fill('수정한 그림 설명')
  await saveDeck(page)

  let stored = await storedSlide(page, id)
  expect(stored.accent).toBe('#123456')
  expect(stored.layout).toBe('two-column')
  expect(stored.textScale).toBeUndefined()
  expect(stored.image.caption).toBe('수정한 그림 설명')
  expect(stored.image.fit).toBe('cover')
  expect(stored.image.size).toBe('large')
  expect(stored.image.position).toBe('left')

  // Delete removes the selected picture and Ctrl+Z brings it back; 이미지 › 그림 제거 does the same
  await deckSelect(page, 'image')
  await page.keyboard.press('Delete')
  await expect(image).toHaveCount(0)
  await page.keyboard.press('Control+z')
  await expect(image).toHaveCount(1)
  const menu = await deckToolbarMenu(page, '이미지')
  await menu.getByRole('button', { name: '그림 제거' }).click()
  await expect(image).toHaveCount(0)
  await saveDeck(page)
  pictures = await deckToolbarMenu(page, '이미지')
  await expect(pictures.getByRole('button', { name: '그림 넣기' })).toBeVisible()
  stored = await storedSlide(page, id)
  expect(stored.image).toBeUndefined()

  // 이미지 › 그림 올리기 puts a local picture on the slide
  await pictures.getByLabel('로컬 그림 업로드').setInputFiles({
    name: '내그림.png',
    mimeType: 'image/png',
    buffer: Buffer.from(PNG.split(',')[1], 'base64'),
  })
  await expect(deckStage(page).locator('img[src^="data:image/png;base64,"]').first()).toBeVisible()
  await saveDeck(page)
  stored = await storedSlide(page, id)
  expect(stored.image.src).toContain('data:image/png;base64,')

  // 굵게 and a size on part of a line keep the rest of the line as it was
  const line = deckStage(page).locator('[contenteditable="true"]').filter({ hasText: '선택한 부분만 바뀌어야 합니다' })
  await line.evaluate((element) => {
    const text = element.firstChild?.firstChild ?? element.firstChild
    if (!text) throw new Error('제목 텍스트를 찾지 못했습니다.')
    const host = element as HTMLElement
    // the box has the caret, as after a click, so the toolbar formats it
    host.focus()
    const range = document.createRange()
    range.setStart(text, 4)
    range.setEnd(text, 7)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  })
  const bold = deckToolbar(page).getByRole('button', { name: '굵게' })
  await expect(bold).toBeEnabled()
  await bold.click()
  await expect(bold).toHaveAttribute('aria-pressed', 'true')
  await deckTextSize(page, 140)
  await saveDeck(page)
  stored = await storedSlide(page, id)
  expect(stored.bullets[0]).toBe('선택한 부분만 바뀌어야 합니다')
  expect(stored.richText['bullets.0']).toMatch(/^선택한 /)
  expect(stored.richText['bullets.0']).toMatch(/ 바뀌어야 합니다$/)
  expect(stored.richText['bullets.0']).toMatch(/<(b|strong)>/)
  expect(stored.richText['bullets.0']).toContain('font-size:1.4em')
  const visibleFormatting = await line.evaluate((element) => {
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
})
