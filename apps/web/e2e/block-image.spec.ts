import { readFile } from 'node:fs/promises'
import { crc32 as zlibCrc32, deflateSync } from 'node:zlib'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Inserting an existing image artifact into a document block; the server inlines its bytes.
 *  Both artifacts are seeded through the API. */

/** A valid PNG, built rather than pasted. */
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

  // An image artifact points at a stored file; the endpoint reads the file.
  const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0))
  const form = new FormData()
  form.append('file', new Blob([bytes], { type: 'image/png' }), 'e2e-picture.png')
  const upload = await fetch('/api/files', { method: 'POST', headers: auth, body: form })
  if (!upload.ok) return null
  const file = await upload.json()

  const stamp = Date.now()
  const picture = await json('/api/artifacts', {
    kind: 'image',
    title: '넣을 그림 ' + stamp,
    data: {
      kind: 'image',
      jobId: null,
      prompt: '단색 사각형',
      aspect: '3:2',
      actualAspect: '3:2',
      width: 240,
      height: 160,
      style: '미니멀',
      seed: 0,
      model: 'e2e',
      src: '/api/files/' + file.id + '/content',
    },
  })
  const page = await json('/api/artifacts', {
    kind: 'html',
    title: '그림 넣을 문서 ' + stamp,
    data: {
      kind: 'html',
      templateId: 'deck-editorial',
      language: 'html',
      content: '<html><body></body></html>',
      blocks: [
        { title: '표지', layout: 'cover', html: '<p class="lead">한 줄 소개</p>' },
        { title: '현황', layout: 'bullets', html: '<ul><li>보유 42대</li></ul>' },
      ],
      lint: [],
    },
  })
  return { stamp, pictureTitle: picture && picture.title, pageId: page && page.id, pageTitle: page && page.title }
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

test('이미지 화면에서 만든 그림을 문서 한 자리에 넣는다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  const seeded = await page.evaluate(
    async ([fn, png]) => await eval(fn)(png),
    [SETUP, PNG_BASE64],
  )
  expect(seeded, '문서와 그림을 만들지 못했습니다').not.toBeNull()
  const { pictureTitle, pageId, pageTitle } = seeded as {
    pictureTitle: string
    pageId: string
    pageTitle: string
  }

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${pageTitle} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  // The block is chosen from the plan, since the preview is sandboxed.
  await page.getByRole('button', { name: '그림 넣기' }).click()
  await page.getByRole('menuitem', { name: '현황' }).click()

  // Exact: the gallery behind the dialog has "{name} 열기" and "{name} 삭제" buttons.
  await page.getByRole('button', { name: pictureTitle, exact: true }).click()
  await page.getByLabel('설명').fill('그림 1. 시험용')
  await page.getByRole('button', { name: '넣기', exact: true }).click()

  // The version bump, read off the open dialog, shows the panel holds the server's copy.
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('HTML · v2')).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: 'test-results/shots/14-block-image.png' })

  const stored = await page.evaluate(
    async ([fn, id]) => await eval(fn)(id),
    [READ, pageId],
  )
  const data = (stored as { data: { blocks: { html: string }[]; content: string } }).data
  // Inlined, not linked: a downloaded artifact must not fetch anything.
  expect(data.blocks[1].html).toContain('data:image/png;base64,')
  expect(data.content).toContain('<figcaption>그림 1. 시험용</figcaption>')
  expect(data.content).not.toContain('/api/files/')

  // Zip entry names are stored uncompressed, so the media part is readable from the bytes.
  const saved = page.waitForEvent('download', { timeout: 60_000 })
  await dialog.getByRole('button', { name: '내보내기' }).click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const file = await saved
  const bytes = await readFile(await file.path())
  expect(file.suggestedFilename()).toMatch(/\.pptx$/)
  expect(bytes.toString('latin1')).toContain('ppt/media/image1.png')
})

/** With no pictures the control still shows and says why. The listing is filtered in flight:
 *  the account is shared with every other suite. */
test('그림이 없어도 넣을 자리는 보이고, 왜 없는지 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const seeded = await page.evaluate(
    async ([fn, png]) => await eval(fn)(png),
    [SETUP, PNG_BASE64],
  )
  expect(seeded, '문서를 만들지 못했습니다').not.toBeNull()
  const { pageTitle } = seeded as { pageTitle: string }

  // Image rows removed from the listing; the document is fetched by id on another path.
  await page.route('**/api/artifacts?*', async (route) => {
    const response = await route.fetch()
    const rows = await response.json()
    await route.fulfill({
      response,
      json: Array.isArray(rows) ? rows.filter((row) => row.kind !== 'image') : rows,
    })
  })

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${pageTitle} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  await page.getByRole('button', { name: '그림 넣기' }).click()
  await page.getByRole('menuitem', { name: /표지/ }).click()

  await expect(page.getByText(/그림이 없습니다|그림을 만들 수 없습니다/)).toBeVisible({
    timeout: 10_000,
  })
})
