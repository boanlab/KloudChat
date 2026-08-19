import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A picture made on the image surface, put inside a written document.
 *
 * The writing model can neither draw one nor point at one — `sanitise` drops
 * every address that is not already inside the file — so this is the path that
 * exists instead: a person picks a picture they already have, and the server
 * inlines its bytes. Nothing here costs a model call, so both the artifact and
 * the picture are seeded through the API and only the joining is driven
 * through the screen.
 */

/** 240×160, one flat colour. Small enough to inline in a test file. */
const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAPAAAACgCAIAAAD5ZfRxAAAAcElEQVR42u3QMQEAAAgDINc/9Ezg' +
  'JIQmsjWnhAQCggABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAAB' +
  'AgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgIA/AzWtAAF2LhZlAAAAAElFTkSuQmCC'

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

  // The blob first: an image artifact is a row that points at a stored file,
  // and the endpoint reads that file rather than the artifact.
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

  // Which block: the picture belongs to a place in the document, and the
  // preview is sandboxed, so the choice is made from the plan it was written
  // from — the same list the rewrite uses.
  await page.getByRole('button', { name: '그림 넣기' }).click()
  await page.getByRole('menuitem', { name: '현황' }).click()

  // Exact: the gallery behind the dialog carries "{name} 열기" and
  // "{name} 삭제" buttons for the same picture.
  await page.getByRole('button', { name: pictureTitle, exact: true }).click()
  await page.getByLabel('설명').fill('그림 1. 시험용')
  await page.getByRole('button', { name: '넣기', exact: true }).click()

  // The version is the visible half of "this edited the document", and it is
  // what makes the change undoable. Read off the open dialog, which is also
  // the assertion that the panel is looking at the document the server now
  // holds rather than the copy it opened with.
  await expect(page.getByRole('dialog').getByText('HTML · v2')).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: 'test-results/shots/14-block-image.png' })

  const stored = await page.evaluate(
    async ([fn, id]) => await eval(fn)(id),
    [READ, pageId],
  )
  const data = (stored as { data: { blocks: { html: string }[]; content: string } }).data
  // Inside the file, not a link to it: the artifact is downloaded and shared,
  // and a reader opening it must not fetch anything.
  expect(data.blocks[1].html).toContain('data:image/png;base64,')
  expect(data.content).toContain('<figcaption>그림 1. 시험용</figcaption>')
  expect(data.content).not.toContain('/api/files/')
})
