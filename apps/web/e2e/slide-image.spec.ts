import { readFile } from 'node:fs/promises'
import { crc32 as zlibCrc32, deflateSync } from 'node:zlib'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The same picture path on the deck track that was never HTML.
 *
 * A JSON deck's slides are rows in a JSONB column, so the picture is stored as
 * the `data:` URI itself and the preview, the `.pptx` and the `.pdf` all read
 * it from there. Seeded through the API — nothing here costs a model call —
 * and joined through the screen.
 */

/**
 * A real PNG, built here.
 *
 * Pasted base64 is unreadable in a diff and easy to truncate — the first
 * version of this file carried a broken one, which the server refused only at
 * export time, hours later.
 */
function png(width = 240, height = 160): string {
  const raw = Buffer.concat(
    Array.from({ length: height }, (_, y) =>
      Buffer.concat([
        Buffer.from([0]),
        Buffer.from(
          Array.from({ length: width * 3 }, (_, i) => (i % 3 === 0 ? 40 + y : i % 3 === 1 ? 90 : 200)),
        ),
      ]),
    ),
  )
  const chunk = (kind: string, data: Buffer) => {
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(kind, 'latin1'), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(zlibCrc32(body) >>> 0)
    return Buffer.concat([length, body, crc])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]).toString('base64')
}

const PNG_BASE64 = png()

const SETUP = `async (png) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const auth = { Authorization: 'Bearer ' + accessToken }
  const json = async (path, payload) => {
    const r = await fetch(path, {
      method: 'POST',
      headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return r.ok ? await r.json() : null
  }

  const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0))
  const form = new FormData()
  form.append('file', new Blob([bytes], { type: 'image/png' }), 'e2e-slide-picture.png')
  const upload = await fetch('/api/files', { method: 'POST', headers: auth, body: form })
  if (!upload.ok) return null
  const file = await upload.json()

  const stamp = Date.now()
  const picture = await json('/api/artifacts', {
    kind: 'image',
    title: '슬라이드용 그림 ' + stamp,
    data: {
      kind: 'image', jobId: null, prompt: '단색 사각형', aspect: '3:2', actualAspect: '3:2',
      width: 240, height: 160, style: '미니멀', seed: 0, model: 'e2e',
      src: '/api/files/' + file.id + '/content',
    },
  })
  const deck = await json('/api/artifacts', {
    kind: 'deck',
    title: '그림 넣을 덱 ' + stamp,
    data: {
      kind: 'deck',
      theme: '청록',
      slides: [
        { id: 'sl0', layout: 'title', title: '표지', body: '한 줄', accent: '#0f766e' },
        { id: 'sl1', layout: 'bullets', title: '현황', bullets: ['보유 42대'], accent: '#0f766e' },
      ],
    },
  })
  return { pictureTitle: picture && picture.title, deckId: deck && deck.id, deckTitle: deck && deck.title }
}`

const READ = `async (id) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch('/api/artifacts/' + id, { headers: { Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('JSON 덱의 한 장에 그림을 넣고, 파일로 받으면 그림이 들어 있다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  const seeded = await page.evaluate(
    async ([fn, png]) => await eval(fn)(png),
    [SETUP, PNG_BASE64],
  )
  expect(seeded, '덱과 그림을 만들지 못했습니다').not.toBeNull()
  const { pictureTitle, deckId, deckTitle } = seeded as {
    pictureTitle: string
    deckId: string
    deckTitle: string
  }

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${deckTitle} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  const dialog = page.getByRole('dialog')
  // The second slide: the picture belongs to the one being shown.
  await dialog.getByRole('button', { name: '다음 장' }).click()
  await dialog.getByRole('button', { name: '그림 넣기' }).click()
  await page.getByRole('button', { name: pictureTitle, exact: true }).click()
  await page.getByLabel('설명').fill('그림 1. 슬라이드')
  await page.getByRole('button', { name: '넣기', exact: true }).click()

  // Drawn in the preview, at the same place the exporters put it. Two of them:
  // the stage and that slide's thumbnail in the list beside it.
  const drawn = dialog.locator('img[src^="data:image/png;base64,"]')
  await expect(drawn.first()).toBeVisible({ timeout: 30_000 })
  expect(await drawn.count()).toBeGreaterThanOrEqual(1)
  await page.screenshot({ path: 'test-results/shots/17-slide-image.png' })

  const stored = await page.evaluate(async ([fn, id]) => await eval(fn)(id), [READ, deckId])
  const slides = (stored as { data: { slides: { image?: { src: string; caption: string } }[] } })
    .data.slides
  expect(slides[1].image?.src).toContain('data:image/png;base64,')
  expect(slides[1].image?.caption).toBe('그림 1. 슬라이드')
  expect(slides[0].image).toBeUndefined()

  // ── and it leaves in the file ───────────────────────────────────────
  const saved = page.waitForEvent('download', { timeout: 60_000 })
  await dialog.getByRole('button', { name: '내보내기' }).click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const file = await saved
  const bytes = await readFile(await file.path())
  expect(file.suggestedFilename()).toMatch(/\.pptx$/)
  expect(bytes.toString('latin1')).toContain('ppt/media/image1.png')
})
