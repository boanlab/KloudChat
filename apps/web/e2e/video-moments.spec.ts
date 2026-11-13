import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { at, chatId, composerFixture } from './composer-fixture'

/**
 * Videos against fixture data: a video goes up in pieces with its progress shown, its chip
 * follows the server reading what is said and shown, and an answer's time marks open the player
 * at that moment.
 */

const videoId = 'v0000000000000000000000000000001'
/** A 13-minute 64×36 clip, a frame a second (made with ffmpeg; a few KB). */
const CLIP = readFileSync(new URL('./fixtures/thirteen-minutes.webm', import.meta.url))
const PIECE = 64 * 1024

function videoRow(extra: Record<string, unknown>) {
  return {
    id: videoId, name: '강의.mp4', size: 200_000, mime: 'video/mp4', tokens: 0, projectId: null,
    sessionId: chatId, preview: '', error: null, indexed: false, createdAt: at,
    mediaState: 'queued', mediaStage: null, mediaDone: 0, mediaTotal: 0, mediaDuration: 0, ...extra,
  }
}

const SIGNED = 'e=9999999999&s=fixture'

function mediaInfo(extra: Record<string, unknown> = {}) {
  return {
    name: '강의.mp4', state: 'done', stage: null, done: 4, total: 4, error: null, duration: 780,
    width: 64, height: 36, hasVideo: true, hasAudio: false, language: 'ko', notes: [], timings: {},
    frames: [{ n: 0, t: 0 }, { n: 1, t: 700 }],
    segments: [
      { start: 0, end: 100, said: '오늘은 도파민 이야기입니다', shown: '슬라이드 — 제목', frame: 0 },
      { start: 100, end: 700, said: '보상 회로가 바뀝니다', shown: '', frame: null },
      { start: 700, end: 780, said: '중독 환자가 크게 늘었습니다', shown: '도표 — 막대그래프', frame: 1 },
    ],
    streamUrl: `/api/files/${videoId}/stream?${SIGNED}`,
    frameUrl: `/api/files/${videoId}/frames/{n}.jpg?${SIGNED}`,
    ...extra,
  }
}

const TINY_JPEG = Buffer.from(
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==',
  'base64',
)

test('a video goes up in pieces, then its chip follows the reading', { tag: '@phone' }, async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.route('**/api/auth/config', (route) => route.fulfill({ json: {
    brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'], privacy: { externalDataGuard: false },
    passwordResetEnabled: false, dictationEnabled: false,
    upload: { maxMb: 1, maxMediaMb: 500, mediaReading: true, maxMediaMinutes: 240 },
  } }))
  const offsets: number[] = []
  let size = 0
  const answers = [
    videoRow({ mediaState: 'running', mediaStage: 'reading', mediaDone: 1, mediaTotal: 3 }),
    videoRow({ mediaState: 'done', mediaDone: 3, mediaTotal: 3, mediaDuration: 750, tokens: 900 }),
  ]
  let polls = 0
  await page.route('**/api/uploads**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === 'POST' && url.pathname === '/api/uploads') {
      size = Number(request.postDataJSON().size)
      expect(request.postDataJSON().sessionId).toBe(chatId)
      return route.fulfill({ status: 201, json: { id: 'u1', size, received: 0, pieceBytes: PIECE } })
    }
    if (request.method() === 'PUT') {
      const offset = Number(url.searchParams.get('offset'))
      offsets.push(offset)
      // slow enough to see the progress move
      await new Promise((resolve) => setTimeout(resolve, 150))
      return route.fulfill({ json: { id: 'u1', size, received: offset + (request.postDataBuffer()?.length ?? 0), pieceBytes: PIECE } })
    }
    if (request.method() === 'POST' && url.pathname === '/api/uploads/u1/finish') {
      return route.fulfill({ status: 201, json: videoRow({}) })
    }
    return route.fallback()
  })
  await page.route(`**/api/files/${videoId}`, (route) => {
    const row = answers[Math.min(polls, answers.length - 1)]
    polls += 1
    return route.fulfill({ json: row })
  })
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  // over the ordinary 1 MB limit: a video has its own
  const bytes = Buffer.alloc(PIECE * 20 + 1000, 7)
  await page.getByLabel('파일 선택').setInputFiles({ name: '강의.mp4', mimeType: 'video/mp4', buffer: bytes })
  const progress = page.getByTestId('upload-progress')
  await expect(progress).toContainText(/강의\.mp4 올리는 중 \d+%/)
  const chip = page.locator('[data-media]').filter({ hasText: '강의.mp4' })
  await expect(chip).toContainText('영상 읽기 대기 중', { timeout: 15_000 })
  await expect(chip).toContainText('영상의 말과 화면 옮기는 중 1/3', { timeout: 15_000 })
  await expect(chip).toContainText('영상 읽음 · 12:30', { timeout: 15_000 })
  // every piece once, in order, none past the end
  expect(offsets).toEqual(Array.from({ length: 21 }, (_, i) => i * PIECE))
  expect(unexpected).toEqual([])
})

test('an answer\'s time marks open the player at that moment', async ({ page }, info) => {
  const reply = [
    '중독 환자가 크게 늘었습니다 [[1]](/files?open=' + videoId + '&t=750).',
    '앞부분에서 보상 회로를 설명합니다 [00:01:40].',
    '',
    '### 출처',
    `- [1] [강의.mp4 · 00:12:30](/files?open=${videoId}&t=750)`,
  ].join('\n')
  const { unexpected } = await composerFixture(page, info, { reply })
  await page.route(`**/api/files/${videoId}/media`, (route) => route.fulfill({ json: mediaInfo() }))
  await page.route(`**/api/files/${videoId}/frames/**`, (route) =>
    route.fulfill({ contentType: 'image/jpeg', body: TINY_JPEG }))
  await page.route(`**/api/files/${videoId}/stream**`, (route) =>
    route.fulfill({ contentType: 'video/webm', body: CLIP }))
  await page.goto(`/s/${chatId}`)
  const chips = page.getByTestId('time-chip')
  await expect(chips.first()).toBeVisible()
  // the cited passage, the bare mark (linked to the one video the answer cites), the source line
  await expect(chips).toHaveCount(3)
  await expect(chips.nth(0)).toContainText('12:30')
  await expect(chips.nth(1)).toContainText('1:40')
  await expect(chips.nth(0).locator('img')).toBeVisible()
  await chips.nth(1).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('강의.mp4')
  const video = dialog.getByTestId('media-element')
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => Math.round(el.currentTime)), { timeout: 15_000 }).toBe(100)
  // the outline marks where the player is, and a click on a stretch plays from there
  await expect(dialog.locator('[data-start="100"]')).toHaveAttribute('aria-current', 'true')
  await dialog.locator('[data-start="700"]').click()
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => Math.round(el.currentTime)), { timeout: 15_000 }).toBeGreaterThanOrEqual(700)
  expect(unexpected).toEqual([])
})
