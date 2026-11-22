import { deflateSync } from 'node:zlib'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

test.use({ serviceWorkers: 'block' })

/** 「이미지 고치기」 against a mocked API: the picture is the edit source, the result shows 전후 비교
 *  and versions, a draw-only model turns the action off, a strict-local conversation keeps it inside. */

const at = '2026-11-15T00:00:00.000Z'
const later = '2026-11-15T00:05:00.000Z'
const imageSession = '91919191919191919191919191919191'
const strictSession = '92929292929292929292929292929292'
const source = '93939393939393939393939393939393'
const edited = '94949494949494949494949494949494'
const made = '95959595959595959595959595959595'
const SHOTS = process.env.SHOTS_DIR ?? ''

/** A square PNG: `ground` with a white disc, so a before and an after are told apart. */
function picture(ground: [number, number, number], size = 256): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc = (bytes: Buffer) => {
    let c = 0xffffffff
    for (const b of bytes) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type: string, data: Buffer) => {
    const head = Buffer.alloc(4)
    head.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const tail = Buffer.alloc(4)
    tail.writeUInt32BE(crc(body))
    return Buffer.concat([head, body, tail])
  }
  const rows: Buffer[] = []
  const middle = size / 2
  for (let y = 0; y < size; y++) {
    const row = Buffer.alloc(1 + size * 3)
    for (let x = 0; x < size; x++) {
      const inside = (x - middle) ** 2 + (y - middle) ** 2 < (size * 0.2) ** 2
      const [r, g, b] = inside ? [255, 255, 255] : ground
      row.set([r, g, b], 1 + x * 3)
    }
    rows.push(row)
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header.set([8, 2, 0, 0, 0], 8)
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(Buffer.concat(rows))),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

const RED = picture([214, 40, 40])
const BLUE = picture([30, 90, 220])
const NIGHT = picture([20, 24, 60])

const model = (id: string, name: string, kinds: string[], modality: string, extra: Record<string, unknown> = {}) => ({
  id, label: `Fixture · ${name}`, name, vendor: 'Fixture', provider: 'fixture', kinds, modality,
  dataBoundary: 'external', strictLocal: false, privacyOnly: false, creditCost: 1, inputCreditCost: 1,
  creditPerImage: 40, aspects: ['1:1'], supportsTools: false, contextWindow: 64000, description: '', ...extra,
})
const MODELS = [
  model('fixture/chat', 'Chat', ['chat', 'report', 'slides'], 'chat'),
  model('fixture/strict-chat', 'Strict Chat', ['chat'], 'chat', { dataBoundary: 'self_hosted', strictLocal: true }),
  model('fixture/edit', 'Edit Image', ['image'], 'image', { editsImages: true }),
  model('fixture/draw', 'Draw Only', ['image'], 'image', { editsImages: false }),
]

function message(id: string, role: 'user' | 'assistant', content: string, extra: Record<string, unknown> = {}) {
  return { id, role, content, steps: null, attachments: null, variants: null, superseded: null,
    usage: role === 'assistant' ? { inputTokens: 0, outputTokens: 0, credits: 40 } : null,
    model: role === 'assistant' ? 'fixture/edit' : null, routing: null, startedFrom: null, rating: null,
    artifactIds: null, failure: null, createdAt: role === 'user' ? at : later, ...extra }
}

function image(id: string, file: string, prompt: string, extra: Record<string, unknown> = {}) {
  return { id, kind: 'image', title: prompt, sessionId: imageSession, projectId: null, version: 1,
    partial: false, createdAt: at, updatedAt: later,
    data: { kind: 'image', jobId: null, prompt, aspect: '1:1', actualAspect: '1:1', width: 256, height: 256,
      style: '', labels: '', seed: 0, model: 'fixture/edit', src: `/api/files/${file}/content`, ...extra } }
}

type Seen = { images: Record<string, unknown>[]; patches: Record<string, unknown>[] }

async function fixture(page: Page, testInfo: TestInfo, { imageModel = 'fixture/edit' } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const seen: Seen = { images: [], patches: [] }
  const user = {
    id: 'fixture-user', name: '이미지 고치기', email: 'fixture@example.test', role: 'user', status: 'active',
    monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
    preferences: { autoMemory: false, showUsage: false, streamResponses: true },
  }
  const artifacts: Record<string, ReturnType<typeof image>> = {
    [source]: image(source, 'f-red', '빨간 바탕에 흰 원'),
    [edited]: image(edited, 'f-blue', '배경만 파랗게', {
      editOf: { artifactId: source, fileId: 'f-red', src: '/api/files/f-red/content', name: '빨간 바탕에 흰 원.png' },
      lineage: source, editVersion: 2,
    }),
  }
  const files: Record<string, Buffer> = { 'f-red': RED, 'f-blue': BLUE, 'f-night': NIGHT }
  const sessions = [
    {
      id: imageSession, title: '빨간 바탕에 흰 원', kind: 'image', model: imageModel, routingMode: 'manual',
      projectId: null, agentId: null, artifactId: edited, pinned: false, made: null, createdAt: at, updatedAt: later,
      messages: [
        message('m1', 'user', '빨간 바탕에 흰 원'),
        message('m2', 'assistant', '', { artifactIds: [source] }),
        message('m3', 'user', '배경만 파랗게'),
        message('m4', 'assistant', '', { artifactIds: [edited] }),
      ],
      messageCount: 4,
    },
    {
      id: strictSession, title: '내부 그림', kind: 'chat', model: 'fixture/strict-chat', routingMode: 'manual',
      projectId: null, agentId: null, artifactId: null, pinned: false, made: null, createdAt: at, updatedAt: later,
      messages: [
        message('s1', 'user', '그림 보여 줘', { attachments: [{ id: 'f-red', name: '사내 도면.png', size: 2048, type: 'image/png' }] }),
        message('s2', 'assistant', '', { artifactIds: [source] }),
      ],
      messageCount: 2,
    },
  ]
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600, user } })
    }
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'image'],
      privacy: { externalDataGuard: false, allowUserRawExternal: false, internalDataStrictLocal: false },
      passwordResetEnabled: false, dictationEnabled: false,
    } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: {
      models: MODELS, defaultChatModel: 'fixture/chat',
      defaults: { chat: 'fixture/chat', report: 'fixture/chat', image: imageModel },
      defaultModelByKind: { report: 'fixture/chat', image: imageModel },
      litellmAvailable: true, autoRouting: { enabled: false, available: false },
    } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 40, creditsRemaining: 960 } })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { image: 2 }, total: 2 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: sessions })
    const one = sessions.find((row) => path === `/sessions/${row.id}`)
    if (one && method === 'GET') return route.fulfill({ json: one })
    if (one && method === 'PATCH') {
      const patch = request.postDataJSON() as Record<string, unknown>
      seen.patches.push(patch)
      Object.assign(one, patch)
      return route.fulfill({ json: one })
    }
    const artifact = path.match(/^\/artifacts\/([0-9a-f]{32})$/)
    if (method === 'GET' && artifact && artifacts[artifact[1]]) return route.fulfill({ json: artifacts[artifact[1]] })
    const file = path.match(/^\/files\/([a-z-]+)\/content$/)
    if (method === 'GET' && file && files[file[1]]) {
      return route.fulfill({ status: 200, body: files[file[1]], headers: { 'content-type': 'image/png' } })
    }
    if (method === 'GET' && path === '/files/page') {
      const row = (id: string, name: string, origin: string, sessionId: string | null) => ({
        id, name, size: 2048, mime: 'image/png', tokens: 0, projectId: null, sessionId, origin, preview: '',
        error: null, createdAt: at,
      })
      const items = url.searchParams.get('origin') === 'generated'
        ? [row('f-blue', '배경만 파랗게.png', 'generated', imageSession)]
        : [row('f-red', '사진.png', 'upload', null)]
      return route.fulfill({ json: { items, total: items.length, next: null, counts: { upload: 1, generated: 1, working: 0 } } })
    }
    if (method === 'POST' && path === `/sessions/${imageSession}/images`) {
      const payload = request.postDataJSON() as Record<string, unknown>
      seen.images.push(payload)
      const row = image(made, 'f-night', String(payload.prompt), {
        editOf: { artifactId: payload.sourceArtifactId, fileId: 'f-blue', src: '/api/files/f-blue/content', name: '배경만 파랗게.png' },
        lineage: source, editVersion: 3, composedPrompt: `Edit the attached image: ${payload.prompt}`,
      })
      artifacts[made] = row
      const session = sessions[0]
      session.messages.push(message('m5', 'user', String(payload.prompt)), message('m6', 'assistant', '', { artifactIds: [made] }))
      session.messageCount = session.messages.length
      session.artifactId = made
      return route.fulfill({ json: [row] })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.abort('blockedbyclient')
  })
  return seen
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test('이미지 고치기는 그림을 입력창에 싣고, 보낸 지시와 함께 원본을 보낸다', async ({ page }, testInfo) => {
  const seen = await fixture(page, testInfo)
  await page.goto(`/s/${imageSession}`)
  const edit = page.getByTestId('image-edit').last()
  await expect(edit).toBeVisible()
  await expect(edit).not.toHaveAttribute('aria-disabled', 'true')
  await shot(page, 'desktop-1-result')
  await edit.click()
  const chip = page.getByTestId('image-edit-chip')
  await expect(chip).toContainText('고칠 이미지')
  await expect(chip).toContainText('배경만 파랗게')
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toBeFocused()
  await box.fill('배경을 밤하늘로 바꿔 줘')
  await shot(page, 'desktop-2-composer-chip')
  await box.press('Enter')
  await expect.poll(() => seen.images.length).toBe(1)
  expect(seen.images[0]).toMatchObject({ prompt: '배경을 밤하늘로 바꿔 줘', model: 'fixture/edit', sourceArtifactId: edited })
  expect(seen.images[0].sourceFileId).toBeUndefined()
  await expect(chip).toHaveCount(0)

  // the result: 전후 비교 over one frame, and the line's versions with this one marked
  const versions = page.getByTestId('image-versions').last()
  await expect(versions).toContainText('원본')
  await expect(versions).toContainText('v2')
  await expect(versions).toContainText('v3')
  await expect(versions.locator('[aria-current="true"]')).toContainText('v3')
  await page.getByTestId('image-compare-toggle').last().click()
  const compare = page.getByTestId('image-compare')
  await expect(compare).toBeVisible()
  await expect(compare.getByAltText('고치기 전')).toHaveAttribute('src', /f-blue/)
  await expect(compare.getByAltText('고친 뒤')).toHaveAttribute('src', /f-night/)
  const slider = compare.getByRole('slider', { name: '전후 비교' })
  await slider.fill('30')
  await expect(compare.getByAltText('고치기 전')).toHaveAttribute('style', /inset\(0px 70%|inset\(0 70%/)
  await compare.scrollIntoViewIfNeeded()
  await shot(page, 'desktop-3-before-after')
})

test('그리기만 하는 모델이면 고치기가 꺼지고 이유를 말하며, 고칠 수 있는 모델로 바꿀 수 있다', async ({ page }, testInfo) => {
  const seen = await fixture(page, testInfo, { imageModel: 'fixture/draw' })
  await page.goto(`/s/${imageSession}`)
  const edit = page.getByTestId('image-edit').last()
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  const tip = page.getByTestId('image-edit-tip').last()
  await expect(tip).toHaveText('이 모델(Draw Only)은 그리기만 합니다.')
  await expect(edit).toHaveAttribute('aria-describedby', (await tip.getAttribute('id')) ?? '')
  // the reason shows on keyboard focus, not only on hover
  await expect(tip).toBeHidden()
  await edit.focus()
  await expect(tip).toBeVisible()
  // a tap says it too, and nothing is put in the composer (`aria-disabled` takes the tap; Playwright
  // counts it as not enabled)
  await edit.click({ force: true })
  await expect(page.getByTestId('image-edit-reason')).toHaveText('이 모델(Draw Only)은 그리기만 합니다.')
  await expect(page.getByTestId('image-edit-chip')).toHaveCount(0)
  await shot(page, 'desktop-4-disabled')
  // the picker marks the models that edit
  await page.getByTestId('composer-model-slot').getByRole('button').first().click()
  await expect(page.getByTestId('model-edits-images')).toHaveCount(1)
  // the menu fades in
  if (SHOTS) await page.waitForTimeout(400)
  await shot(page, 'desktop-5-picker')
  await page.keyboard.press('Escape')
  const switchTo = page.getByTestId('image-edit-switch').last()
  await expect(switchTo).toHaveAttribute('title', 'Fixture · Edit Image')
  await switchTo.click()
  await expect.poll(() => seen.patches.map((p) => p.model)).toContain('fixture/edit')
  await expect(page.getByTestId('image-edit').last()).not.toHaveAttribute('aria-disabled', 'true')
  expect(seen.images).toHaveLength(0)
})

test('strict-local 대화의 그림은 외부 모델로 고치지 않는다', async ({ page }, testInfo) => {
  await fixture(page, testInfo)
  await page.goto(`/s/${strictSession}`)
  const edit = page.getByTestId('image-edit').last()
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByTestId('image-edit-tip').last()).toHaveText('strict-local 대화라 외부 모델로 고칠 수 없습니다.')
  // no switch: changing the model would not let the picture out
  await expect(page.getByTestId('image-edit-switch')).toHaveCount(0)
  // an attached picture has the action too, off for the same reason
  const attached = page.getByRole('button', { name: '사내 도면.png 고치기' })
  await expect(attached).toHaveAttribute('aria-disabled', 'true')
  await attached.click({ force: true })
  await expect(page.getByTestId('toast')).toContainText('strict-local 대화라 외부 모델로 고칠 수 없습니다.')
})

test('파일함의 그림도 고칠 수 있다: 만든 그림은 그 대화에서, 올린 그림은 새 이미지 대화에서', async ({ page }, testInfo) => {
  await fixture(page, testInfo)
  await page.goto('/files')
  const uploaded = page.getByRole('button', { name: '사진.png 고치기' })
  await expect(uploaded).toBeVisible()
  await uploaded.click()
  await expect(page).toHaveURL(/\/s\/[^/]+$/)
  expect(page.url()).not.toContain(imageSession)
  await expect(page.getByTestId('image-edit-chip')).toContainText('사진.png')

  await page.goto('/files')
  await page.getByRole('tab', { name: /만든 파일/ }).click()
  const madeHere = page.getByRole('button', { name: '배경만 파랗게.png 고치기' })
  await expect(madeHere).toBeVisible()
  await madeHere.click()
  await expect(page).toHaveURL(new RegExp(`/s/${imageSession}$`))
  await expect(page.getByTestId('image-edit-chip')).toContainText('배경만 파랗게.png')
})

test('휴대폰에서도 고치기와 전후 비교를 쓴다 @phone', async ({ page }, testInfo) => {
  test.skip((testInfo.project.use.viewport?.width ?? 1440) > 500, 'phone width')
  await fixture(page, testInfo)
  await page.goto(`/s/${imageSession}`)
  const edit = page.getByTestId('image-edit').last()
  await expect(edit).toBeVisible()
  await shot(page, 'phone-1-result')
  await page.getByTestId('image-compare-toggle').last().click()
  const compare = page.getByTestId('image-compare')
  await expect(compare).toBeVisible()
  const frame = await compare.boundingBox()
  expect(frame!.width).toBeLessThanOrEqual(390)
  await compare.scrollIntoViewIfNeeded()
  await shot(page, 'phone-2-before-after')
  await edit.click()
  await expect(page.getByTestId('image-edit-chip')).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('배경을 밤하늘로 바꿔 줘')
  await shot(page, 'phone-3-composer-chip')
})

test('휴대폰에서 꺼진 고치기는 눌렀을 때 이유를 말한다 @phone', async ({ page }, testInfo) => {
  test.skip((testInfo.project.use.viewport?.width ?? 1440) > 500, 'phone width')
  await fixture(page, testInfo, { imageModel: 'fixture/draw' })
  await page.goto(`/s/${imageSession}`)
  const edit = page.getByTestId('image-edit').last()
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  await edit.click({ force: true })
  await expect(page.getByTestId('image-edit-reason')).toHaveText('이 모델(Draw Only)은 그리기만 합니다.')
  await shot(page, 'phone-4-disabled')
})
