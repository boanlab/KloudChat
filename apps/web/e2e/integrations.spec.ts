import { expect, test, type Page, type Route } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * Google Drive and Slack connected per person: 설정 › 연결 (the OAuth round trip mocked as a
 * redirect straight back), the card a Drive save waits on, and Drive files picked into a
 * project (Google Picker stubbed in the page). Every API response is a fixture; no request
 * reaches Google.
 */

const now = '2026-11-15T03:00:00.000Z'
const sessionId = 'drive-save'
const projectId = 'proj-drive'
const actionId = 'act-1'
const model = 'strict-local/qwen3.8-flash'
const shots = process.env.INTEGRATIONS_SCREENSHOT_DIR

type State = {
  notify: Record<string, unknown>
  slackAction: Record<string, unknown>
  connected: boolean
  posts: { method: string; path: string; body: unknown }[]
  unexpected: string[]
  action: Record<string, unknown>
  projectFiles: Record<string, unknown>[]
}

const admin = (on: boolean) => ({ ...user, role: on ? 'admin' : 'user' })
let asAdmin = false

const user = {
  id: 'fixture-user', email: 'member@gmail.com', name: '구성원', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: now,
  preferences: { autoMemory: false, showUsage: true, streamResponses: true },
}

function fileRow(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id, name, size: 2048, mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    tokens: 420, projectId, sessionId: null, origin: 'upload', preview: '11월 운영위원회 회의록. 예산 3건 의결.',
    error: null, indexed: true, createdAt: now, ...extra,
  }
}

async function mock(page: Page, { slackRow = false }: { slackRow?: boolean } = {}): Promise<State> {
  const state: State = {
    notify: { on: false, channel: '' },
    slackAction: {
      id: 'act-slack', provider: 'slack', kind: 'slack.post_message', status: 'pending',
      payload: { channel: 'C111111', channelName: '운영위', text: '회의록을 올렸습니다. 확인 부탁드립니다.' },
      editable: ['channel', 'channelName', 'text'], result: null, error: '', createdAt: now, decidedAt: null,
    },
    connected: false, posts: [], unexpected: [], projectFiles: [],
    action: {
      id: actionId, provider: 'google', kind: 'drive.export', status: 'pending',
      payload: { artifactId: 'art-1', title: '11월 운영위 결과 보고', format: 'docx', formats: ['docx', 'pdf', 'md'] },
      editable: ['format'], result: null, error: '', createdAt: now, decidedAt: null,
    },
  }
  page.on('pageerror', (error) => state.unexpected.push(`pageerror: ${error.message}`))
  const question = { id: 'q1', role: 'user', content: '이 보고서를 내 드라이브에 저장해 줘', attachments: [], createdAt: now }
  const answer = {
    id: 'a1', role: 'assistant', model, createdAt: now, attachments: [],
    content: '「11월 운영위 결과 보고」를 .docx로 드라이브에 올리는 확인 카드를 띄웠습니다. 확인을 누르면 저장됩니다.',
    steps: [{ id: 'h0_0', type: 'tool', label: '드라이브 저장 제안', status: 'done', detail: '확인을 기다립니다',
      confirm: { actionId, kind: 'drive.export', provider: 'google' } }],
    usage: { inputTokens: 40, outputTokens: 30, credits: 0 },
  }
  const session = {
    id: sessionId, title: '결과 보고 저장', kind: 'chat', model, routingMode: 'manual', projectId: null,
    agentId: null, artifactId: null, pinned: false, messages: [question, answer], messageCount: 2,
    createdAt: now, updatedAt: now,
  }
  const project = () => ({
    id: projectId, name: '운영위', description: '', emoji: '📁', instructions: '', skillIds: [],
    designSystemId: null, renderTemplates: {}, files: state.projectFiles, sessionIds: [],
    createdAt: now, updatedAt: now,
  })
  await page.addInitScript(() => {
    // Google Picker stand-in: the dialog "picks" one file at once; no script is loaded.
    const w = window as unknown as Record<string, unknown>
    class Builder {
      private cb: (d: unknown) => void = () => {}
      setAppId() { return this }
      setOAuthToken() { return this }
      setDeveloperKey() { return this }
      setOrigin() { return this }
      setLocale() { return this }
      addView() { return this }
      enableFeature() { return this }
      setCallback(cb: (d: unknown) => void) { this.cb = cb; return this }
      build() { return { setVisible: () => setTimeout(() => this.cb({ action: 'picked', docs: [{ id: '1AbCdEfGhIjKlMnOp' }] }), 50) } }
    }
    class View { setIncludeFolders() {} setSelectFolderEnabled() {} }
    w.google = { picker: { PickerBuilder: Builder, DocsView: View, ViewId: { DOCS: 'all' }, Feature: { MULTISELECT_ENABLED: 'multi' }, Action: { PICKED: 'picked', CANCEL: 'cancel' } } }
  })
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (method !== 'GET' && path.startsWith('/integrations')) state.posts.push({ method, path, body: request.postDataJSON?.() ?? null })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user: admin(asAdmin) })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: true, strictLocalWebSearch: 'ask' },
      passwordResetEnabled: false, dictationEnabled: false,
      integrations: { drivePicker: true, slack: false },
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: model,
      models: [{ id: model, label: 'qwen3.8-flash (strict-local)', name: 'qwen3.8-flash', vendor: 'Self-hosted',
        provider: 'local', kinds: ['chat'], modality: 'chat', dataBoundary: 'self_hosted', strictLocal: true,
        privacyOnly: false, creditCost: 0, inputCreditCost: 0, supportsTools: true, supportsVision: false, contextWindow: 64000 }],
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    // 설정 › 연결
    if (path === '/integrations' && method === 'GET') return json([
      { provider: 'google', features: ['drive', 'drive_export'],
        connection: state.connected ? { status: 'connected', label: 'member@gmail.com', features: ['drive', 'drive_export'],
          connectedAt: now, lastUsedAt: null, prefs: {} } : null },
      ...(slackRow ? [{ provider: 'slack', features: ['read', 'post', 'notify', 'bot'],
        connection: { status: 'connected', label: '단국 · @jn', features: ['read', 'post', 'notify'],
          connectedAt: now, lastUsedAt: now, prefs: { notify: state.notify } } }] : []),
    ])
    if (path === '/integrations/slack/channels') return json([
      { id: 'C111111', name: '운영위', private: false, shared: false },
      { id: 'G222222', name: '예산-비공개', private: true, shared: false },
    ])
    if (path === '/integrations/slack/notify' && method === 'PUT') {
      state.notify = { ...(request.postDataJSON() as object), name: '' }
      return json({ notify: state.notify })
    }
    if (path === '/integrations/actions/act-slack' && method === 'GET') return json(state.slackAction)
    if (path === '/integrations/actions/act-slack/confirm' && method === 'POST') {
      const edits = (request.postDataJSON() as { edits?: Record<string, unknown> | null }).edits ?? {}
      state.slackAction = { ...state.slackAction, status: 'done', payload: { ...(state.slackAction.payload as object), ...edits },
        result: { id: '1.2', url: 'https://dankook.slack.com/archives/G222222/p12', label: '#예산-비공개' } }
      return json(state.slackAction)
    }
    if (path.startsWith('/admin/') && method !== 'GET') state.posts.push({ method, path, body: request.postDataJSON() })
    if (path === '/integrations/google/connect' && method === 'POST') {
      // the provider's consent screen, mocked: it sends the browser straight back
      state.connected = true
      return json({ url: '/settings/connections?connected=google' })
    }
    if (path === '/admin/integrations' && method === 'GET') return json({ baseUrl: 'https://chat.example.ac.kr', providers: [
      { provider: 'google', enabled: true, clientId: '1234-abc.apps.googleusercontent.com', clientSecretSet: true,
        clientSecretPreview: '…x9Qz', features: ['drive', 'drive_export'], allFeatures: ['drive', 'drive_export'],
        redirectUri: 'https://chat.example.ac.kr/api/integrations/google/callback',
        scopes: ['openid', 'email', 'https://www.googleapis.com/auth/drive.file'], usable: true, connectedPeople: 2,
        pickerKey: 'AIza-browser', appId: '123456789' },
      { provider: 'slack', enabled: false, clientId: '', clientSecretSet: false, clientSecretPreview: '', features: ['read', 'post', 'notify', 'bot'],
        allFeatures: ['read', 'post', 'notify', 'bot'], redirectUri: 'https://chat.example.ac.kr/api/integrations/slack/callback',
        scopes: [], usable: false, connectedPeople: 0, signingSecretSet: false, installScopes: ['chat:write'],
        eventsUrl: 'https://chat.example.ac.kr/api/integrations/slack/events', workspaces: [] },
    ] })
    if (path === '/admin/integrations/google' && method === 'PUT') return json({
      provider: 'google', enabled: false, clientId: '1234-abc.apps.googleusercontent.com', clientSecretSet: true,
      clientSecretPreview: '…x9Qz', features: ['drive', 'drive_export'], allFeatures: ['drive', 'drive_export'],
      redirectUri: 'https://chat.example.ac.kr/api/integrations/google/callback', scopes: [], usable: false,
      connectedPeople: 0, pickerKey: 'AIza-browser', appId: '123456789' })
    if (path === '/integrations/google' && method === 'DELETE') {
      state.connected = false
      return route.fulfill({ status: 204 })
    }
    // the write card
    if (path === `/integrations/actions/${actionId}` && method === 'GET') return json(state.action)
    if (path === `/integrations/actions/${actionId}/confirm` && method === 'POST') {
      const edits = (request.postDataJSON() as { edits?: { format?: string } | null }).edits ?? {}
      state.action = { ...state.action, status: 'done', decidedAt: now,
        payload: { ...(state.action.payload as object), ...edits },
        result: { id: 'newfile', url: 'https://drive.google.com/file/d/newfile/view', label: '11월 운영위 결과 보고.pdf' } }
      return json(state.action)
    }
    // Drive into a project
    if (path === '/integrations/google/picker') return json({
      accessToken: 'ya29.fixture', apiKey: 'browser-key', appId: '123456789', expiresAt: now, origin: 'http://127.0.0.1' })
    if (path === '/integrations/google/drive/import' && method === 'POST') {
      const row = fileRow('drive-file-1', '회의록 11월.docx', { sourceUrl: 'https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp' })
      state.projectFiles = [row]
      return json({ files: [row], refused: [] })
    }
    if (path === '/integrations/google/drive/files/drive-file-1/sync' && method === 'POST')
      return json({ file: state.projectFiles[0], changed: false })
    if (path === '/sessions' && method === 'GET') {
      const { messages: _m, ...summary } = session
      return json([summary])
    }
    if (path === '/sessions/slack-post' && method === 'GET') return json({ ...session, id: 'slack-post', title: 'Slack 공지',
      messages: [{ ...question, content: '운영위 채널에 회의록 올렸다고 슬랙으로 알려 줘' }, { ...answer, content: '운영위 채널에 보낼 메시지를 카드에 띄웠습니다.',
        steps: [{ id: 'h0_0', type: 'tool', label: 'Slack 메시지 제안', status: 'done', detail: '확인을 기다립니다',
          confirm: { actionId: 'act-slack', kind: 'slack.post_message', provider: 'slack' } }] }] })
    if (path === '/sessions/slack-post/messages' && method === 'GET') return json([])
    if (path === `/sessions/${sessionId}` && method === 'GET') return json(session)
    if (path === `/sessions/${sessionId}/messages` && method === 'GET') return json(session.messages)
    if (path === '/projects' && method === 'GET') return json([project()])
    if (path === `/projects/${projectId}` && method === 'GET') return json(project())
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') {
      // the rest of a page's reads (lists, guides, notes): empty
      return json(path.endsWith('/guide') || path.endsWith('/usage') ? {} : [])
    }
    state.unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return state
}

async function shot(page: Page, name: string) {
  if (!shots) return
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: `${shots}/${name}.png`, animations: 'disabled', fullPage: false })
}

const phoneName = (page: Page) => ((page.viewportSize()?.width ?? 1440) < 600 ? 'phone' : 'desktop')

test('설정 › 연결에서 내 Google 계정을 연결하고 해제한다 @phone', async ({ page }) => {
  const state = await mock(page)
  await page.goto('/settings/connections')
  const card = page.getByTestId('connection-google')
  await expect(card.getByText('내 드라이브에서 고른 파일만 읽고, 만든 문서를 저장합니다.')).toBeVisible()
  await expect(card.getByText('고른 파일을 읽고 자료로 가져옵니다.')).toBeVisible()
  await shot(page, `connections-before-${phoneName(page)}`)

  await card.getByRole('button', { name: '연결', exact: true }).click()
  await expect(page).toHaveURL(/\/settings\/connections$/)
  await expect(page.getByRole('status').getByText('Google 드라이브에 연결했습니다.')).toBeVisible()
  await expect(card.getByText('연결됨')).toBeVisible()
  await expect(card.getByText('member@gmail.com')).toBeVisible()
  await shot(page, `connections-connected-${phoneName(page)}`)

  await card.getByRole('button', { name: '연결 해제' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('권한을 되돌리고 저장한 토큰을 지웁니다. 가져온 파일은 남습니다.')).toBeVisible()
  await dialog.getByRole('button', { name: '연결 해제' }).click()
  await expect(card.getByRole('button', { name: '연결', exact: true })).toBeVisible()
  expect(state.posts.map((p) => `${p.method} ${p.path}`)).toEqual([
    'POST /integrations/google/connect', 'DELETE /integrations/google'])
  expect(state.unexpected).toEqual([])
})

test('드라이브 저장은 카드에서 확인해야 보내고, 고른 형식으로 저장한다 @phone', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  const card = page.getByTestId('action-card')
  await expect(card.getByText('내 Google 드라이브에 저장할까요?')).toBeVisible()
  await expect(card.getByText('11월 운영위 결과 보고')).toBeVisible()
  // nothing sent while it waits
  expect(state.posts).toEqual([])
  // the default is the format the tool call named (the answer says .docx)
  await expect(card.getByLabel('파일 형식')).toHaveValue('docx')
  await card.getByLabel('파일 형식').selectOption('pdf')
  await shot(page, `drive-card-${phoneName(page)}`)
  await card.getByRole('button', { name: '드라이브에 저장' }).click()
  await expect(card.getByText('드라이브에 저장했습니다.')).toBeVisible()
  await expect(card.getByRole('link', { name: '열기' })).toHaveAttribute('href', 'https://drive.google.com/file/d/newfile/view')
  expect(state.posts).toEqual([{ method: 'POST', path: `/integrations/actions/${actionId}/confirm`, body: { edits: { format: 'pdf' } } }])
  // a reload shows the card as done, not asking again
  await page.reload()
  await expect(page.getByTestId('action-card').getByText('드라이브에 저장했습니다.')).toBeVisible()
  await expect(page.getByTestId('action-card').getByRole('button', { name: '드라이브에 저장' })).toHaveCount(0)
  await shot(page, `drive-card-done-${phoneName(page)}`)
  expect(state.unexpected).toEqual([])
})

test('프로젝트 지식에 드라이브에서 고른 파일을 가져오고 다시 가져온다 @phone', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: /^지식/ }).click()
  await page.getByRole('button', { name: '드라이브', exact: true }).click()
  const row = page.locator('[data-knowledge="drive-file-1"]')
  await expect(row).toContainText('회의록 11월.docx')
  await expect(row).toContainText('Google 드라이브')
  expect(state.posts[0]).toEqual({ method: 'POST', path: '/integrations/google/drive/import',
    body: { fileIds: ['1AbCdEfGhIjKlMnOp'], projectId, sessionId: null } })
  await shot(page, `drive-project-${phoneName(page)}`)
  await row.getByRole('button', { name: '회의록 11월.docx 드라이브에서 다시 가져오기' }).click()
  await expect(page.getByText('드라이브에서 바뀐 내용이 없습니다.')).toBeVisible()
  expect(state.unexpected).toEqual([])
})

test('관리자 연동 화면은 비밀값을 보이지 않고, 끄기 전에 모두의 연결이 끊긴다고 묻는다 @phone', async ({ page }) => {
  asAdmin = true
  try {
    const state = await mock(page)
    await page.goto('/admin/system/integrations')
    const google = page.getByTestId('integration-admin-google')
    await expect(google.getByText('설치 하나에 OAuth 클라이언트 하나를 씁니다. 사람마다 자기 Google 계정을 연결합니다.')).toBeVisible()
    await expect(google.getByText('저장됨 …x9Qz · 비워 두면 그대로 둡니다')).toBeVisible()
    await expect(google.getByRole('textbox', { name: '리디렉션 URI' })).toHaveValue('https://chat.example.ac.kr/api/integrations/google/callback')
    await shot(page, `admin-integrations-${phoneName(page)}`)
    await google.getByRole('switch', { name: 'Google 드라이브 켜기' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('모든 사람의 연결을 끊고 권한을 되돌립니다.')).toBeVisible()
    await dialog.getByRole('button', { name: '끄기' }).click()
    await expect(google.getByText('꺼짐')).toBeVisible()
    expect(state.posts).toEqual([{ method: 'PUT', path: '/admin/integrations/google', body: { enabled: false } }])
    expect(state.unexpected).toEqual([])
  } finally {
    asAdmin = false
  }
})

test('Slack 메시지는 카드에서 채널과 내용을 고친 뒤 보낸다 @phone', async ({ page }) => {
  const state = await mock(page, { slackRow: true })
  await page.goto('/s/slack-post')
  const card = page.getByTestId('action-card')
  await expect(card.getByText('Slack에 이 메시지를 보낼까요?')).toBeVisible()
  await expect(card.getByLabel('보낼 내용')).toHaveValue('회의록을 올렸습니다. 확인 부탁드립니다.')
  expect(state.posts).toEqual([])
  await card.getByLabel('보낼 채널').selectOption('G222222')
  await card.getByLabel('보낼 내용').fill('회의록을 올렸습니다.')
  await shot(page, `slack-card-${phoneName(page)}`)
  await card.getByRole('button', { name: 'Slack에 보내기' }).click()
  await expect(card.getByText('보냈습니다.')).toBeVisible()
  expect(state.posts).toEqual([{ method: 'POST', path: '/integrations/actions/act-slack/confirm',
    body: { edits: { channel: 'G222222', channelName: '예산-비공개', text: '회의록을 올렸습니다.' } } }])
  expect(state.unexpected).toEqual([])
})

test('Slack 알림을 켜고 받을 곳을 고른다 @phone', async ({ page }) => {
  const state = await mock(page, { slackRow: true })
  await page.goto('/settings/connections')
  const box = page.getByTestId('slack-notify')
  await box.getByRole('switch', { name: '긴 작업이 끝나면 Slack으로 알림' }).click()
  await box.getByLabel('알림 받을 곳').selectOption('C111111')
  await expect.poll(() => state.posts.length).toBe(2)
  expect(state.posts.map((p) => p.body)).toEqual([{ on: true, channel: '' }, { on: true, channel: 'C111111' }])
  await shot(page, `slack-connection-${phoneName(page)}`)
  expect(state.unexpected).toEqual([])
})
