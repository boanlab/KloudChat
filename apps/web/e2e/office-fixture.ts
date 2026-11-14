import type { Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A chat with an office document (HWP, Word, PowerPoint) beside it, the API mocked: the document's file
 * per version, saves that move one version at a time (409 otherwise), and an agent turn whose
 * tools save a version of their own (`agentFile`) on the server.
 */

export const sessionId = '77777777777777777777777777777777'
export const artifactId = '88888888888888888888888888888888'
const at = '2026-11-11T00:00:00.000Z'

export type OfficeOptions = {
  kind: 'hwp' | 'docx' | 'pptx'
  file: Buffer
  /** The file the agent's turn leaves on the server. */
  agentFile: Buffer
  title: string
  conflict?: boolean
  holdTurn?: Promise<void>
  fromHome?: boolean
  /** A file attached to an earlier question in the chat. */
  attachment?: { id: string; name: string }
  /** The conversation's own kind (a report's or deck's screen); a chat when left out. */
  sessionKind?: 'chat' | 'report' | 'slides'
  /** A file in the file box (`/files`, where the page opens): its bytes are `boxFile`, else `file`. */
  box?: { id: string; name: string; boxFile?: Buffer; text?: string }
  /** The document was opened from the file box file (or the attachment) before: it is offered again. */
  copied?: boolean
  /** Hand saves wait for this before the server answers. */
  holdSave?: Promise<void>
  /** These fetches of the document's file (the first is 1) fail with a server error. */
  failLoads?: number[]
  /** The document has no conversation (made outside one); the page opens on the artifacts page. */
  orphan?: boolean
  /** The artifacts page opens instead of the chat. */
  artifactsPage?: boolean
  /** A share link to the document, read without an account: the page opens on it. */
  shared?: { token: string }
  /** The chat is opened by its address alone (a reload, a link): its document is named only by
   *  the conversation's own fetch, as the server finds a chat's newest office document. */
  reopen?: boolean
  /** The conversation (`POST /sessions`) and the document (`POST /artifacts/{kind}`) wait for this. */
  holdCreate?: Promise<void>
}

export async function officeFixture(page: Page, options: OfficeOptions) {
  const unexpected: string[] = []
  const problems: string[] = []
  const saves: { expected: string | null; size: number }[] = []
  /** The first bytes of each saved file: 「PK」 for a zip package. */
  const heads: string[] = []
  /** Each saved file as it was sent. */
  const files: Buffer[] = []
  const csp: string[] = []
  const sent: Record<string, unknown>[] = []
  const created: Record<string, unknown>[] = []
  /** Every request that would change a file-box file (none should). */
  const fileWrites: string[] = []
  /** Share links made (`POST /shares`). */
  const shares: Record<string, unknown>[] = []
  /** Documents given a conversation (`POST /artifacts/{id}/{kind}/session`). */
  const attached: Record<string, unknown>[] = []
  /** Every fetch of the document's file, by the editor or a preview. */
  const loads: string[] = []
  /** Each hand save as it reaches the server, before a held save is answered. */
  const received: string[] = []
  /** Each hand save's version summary. */
  const summaries: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (/Content Security Policy/i.test(message.text())) csp.push(message.text())
  })
  const { kind } = options
  const mime = {
    hwp: 'application/hwp+zip',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  }[kind]
  let artifact = {
    id: artifactId, title: options.title, kind, version: 1, partial: false,
    sessionId: options.orphan ? null as string | null : sessionId, projectId: null,
    createdAt: at, updatedAt: at,
    data: {
      format: kind === 'hwp' ? 'hwpx' : kind, size: options.file.length, text: options.title,
    },
  }
  let file: Buffer = options.file
  let messages: Record<string, unknown>[] = options.attachment
    ? [{ id: 'q0', role: 'user', content: '이 공문을 채워 주세요', createdAt: at,
        attachments: [{ id: options.attachment.id, name: options.attachment.name, size: 38000 }] }]
    : []
  const session = {
    id: sessionId, title: '연수 공문', kind: options.sessionKind ?? 'chat', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: options.reopen ? artifactId : null, pinned: false,
    messages,
    messageCount: messages.length, made: null, createdAt: at, updatedAt: at,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: 'Office', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      // from home: the report and slides surfaces too, where a file is picked
      brand: { name: 'KloudChat', logo: '' },
      enabledKinds: options.fromHome ? ['chat', 'report', 'slides'] : ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/base',
      models: [{
        id: 'fixture/base', label: 'Fixture', name: 'Fixture', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external',
        creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000,
      }],
      autoRouting: { enabled: false, available: false },
    })
    if (path === '/credits') {
      return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    }
    if (path === '/sessions' && method === 'POST') {
      // the conversation the home screen's document button starts, under the id the client chose
      const body = request.postDataJSON()
      if (options.holdCreate) await options.holdCreate
      Object.assign(session, { id: body.id, title: '새 작업', kind: body.kind ?? 'chat', messages: [], messageCount: 0 })
      return json(session)
    }
    if (path === `/artifacts/${kind}` && method === 'POST') {
      const body = request.postDataJSON()
      created.push(body)
      if (options.holdCreate) await options.holdCreate
      const named = options.box && body.fileId === options.box.id
        ? options.box.name.replace(/\.[^.]+$/, '') : options.title
      artifact = {
        ...artifact, sessionId: session.id, title: body.title ?? named,
        data: { ...artifact.data, ...(body.fileId ? { sourceFileId: body.fileId } : {}) },
      }
      return route.fulfill({ status: 201, json: artifact })
    }
    const { box } = options
    if (box && path.startsWith('/files')) {
      if (method !== 'GET') {
        fileWrites.push(`${method} ${path}`)
        return route.fulfill({ status: 405, json: { detail: 'not here' } })
      }
      const row = {
        id: box.id, name: box.name, size: (box.boxFile ?? options.file).length, mime, tokens: 10,
        projectId: null, sessionId: null, preview: '', error: null, origin: 'upload', createdAt: at,
      }
      if (path === '/files') return json([row])
      // the file box pages on the server
      if (path === '/files/page') {
        return json({ items: [row], total: 1, next: null, counts: { upload: 1, generated: 0 } })
      }
      if (path === `/files/${box.id}/content`) {
        return route.fulfill({ body: box.boxFile ?? options.file, contentType: 'application/octet-stream' })
      }
      if (path === `/files/${box.id}/text`) {
        return json({ text: box.text ?? '파일에서 읽은 글', truncated: false })
      }
      if (path === `/files/${box.id}/documents`) return json(options.copied ? [artifact] : [])
    }
    if (options.attachment && path === `/files/${options.attachment.id}/documents`) {
      return json(options.copied ? [artifact] : [])
    }
    if (options.shared && path === `/shared/${options.shared.token}`) {
      return json({
        kind: 'artifact', title: artifact.title, artifactKind: kind,
        data: { format: artifact.data.format, size: artifact.data.size, text: artifact.data.text },
        updatedAt: at,
      })
    }
    if (options.shared && path === `/shared/${options.shared.token}/document`) {
      loads.push('shared')
      return route.fulfill({ body: file, contentType: mime })
    }
    if (path === '/shares' && method === 'POST') {
      const body = request.postDataJSON()
      shares.push(body)
      return json({
        id: 'share-1', token: 'shared-token', artifactId: body.artifactId ?? null,
        sessionId: body.sessionId ?? null, scope: body.scope, views: 0, createdAt: at,
      })
    }
    if (path === `/artifacts/${artifactId}/${kind}/session` && method === 'POST') {
      const body = request.postDataJSON()
      attached.push(body)
      artifact = { ...artifact, sessionId: body.sessionId }
      return json(artifact)
    }
    if (path === '/sessions' && method === 'GET') {
      return json([options.reopen ? { ...session, artifactId: null } : session])
    }
    if (path === `/sessions/${session.id}` && method === 'GET') return json(session)
    if (path === `/sessions/${session.id}/messages` && method === 'GET') return json(messages)
    if (path === `/sessions/${session.id}/messages` && method === 'POST') {
      sent.push(request.postDataJSON())
      if (options.holdTurn) await options.holdTurn
      // what the server keeps of the turn, read back when the stream ends
      messages = [
        ...messages,
        { id: 'q1', role: 'user', content: String(sent.at(-1)?.content ?? ''), attachments: [],
          createdAt: at },
        { id: 'a1', role: 'assistant', content: '연수명·일시·장소·대상을 채웠습니다.',
          attachments: [], model: 'fixture/base', createdAt: at,
          steps: ['문서 확인', '문서 편집', '문서 확인', '문서 편집'].map((label, i) => (
            { id: `h${i + 1}_0`, type: 'tool', label, status: 'done' })) },
      ]
      Object.assign(session, { messages, messageCount: messages.length })
      // the tools saved their version on the server meanwhile
      artifact = { ...artifact, version: artifact.version + 1 }
      file = options.agentFile
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
        body: [
          { type: 'step', id: 'h1_0', label: '문서 확인', status: 'done' },
          { type: 'step', id: 'h2_0', label: '문서 편집', status: 'done', detail: '4건',
            artifactId, artifactVersion: artifact.version },
          { type: 'step', id: 'h3_0', label: '문서 확인', status: 'done' },
          { type: 'step', id: 'h4_0', label: '문서 편집', status: 'done', detail: '1건',
            artifactId, artifactVersion: artifact.version },
          { type: 'delta', text: '연수명·일시·장소·대상을 채웠습니다.' },
          { type: 'artifact', artifactId, deliberate: true },
          { type: 'usage', inputTokens: 10, outputTokens: 5, credits: 1 },
          { type: 'done' },
        ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      })
    }
    if (path === '/artifacts' && method === 'GET') return json([artifact])
    if (path === `/artifacts/${artifactId}` && method === 'GET') return json(artifact)
    if (path === `/artifacts/${artifactId}/versions`) return json([])
    if (path === `/artifacts/${artifactId}/${kind}` && method === 'GET') {
      loads.push(String(artifact.version))
      if (options.failLoads?.includes(loads.length)) {
        return route.fulfill({ status: 500, json: { detail: 'storage_unavailable' } })
      }
      return route.fulfill({
        body: file, contentType: mime,
        headers: { 'X-Artifact-Version': String(artifact.version) },
      })
    }
    if (path === `/artifacts/${artifactId}/${kind}` && method === 'PUT') {
      const expected = url.searchParams.get('expectedVersion')
      summaries.push(url.searchParams.get('summary') ?? '')
      const body = request.postDataBuffer()
      received.push(expected ?? '')
      if (options.holdSave) await options.holdSave
      saves.push({ expected, size: body?.length ?? 0 })
      heads.push(body?.subarray(0, 2).toString('latin1') ?? '')
      if (body) files.push(body)
      if (options.conflict || expected !== String(artifact.version)) {
        return route.fulfill({ status: 409, json: { detail: '이 문서는 다른 곳에서 이미 저장되었습니다.' } })
      }
      file = body ?? file
      artifact = { ...artifact, version: artifact.version + 1 }
      return json(artifact)
    }
    if (path === '/artifacts/counts') return json({ counts: { [kind]: 1 }, total: 1 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${session.id}/jobs`, '/projects', '/skills', '/memory',
      '/agents', '/tools', '/connectors', '/connectors/catalog', '/jobs', '/designs',
      '/design-templates', '/shares'].includes(path)) return json([])
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  const opened = options.shared ? `/share/${options.shared.token}`
    : options.box ? '/files' : options.fromHome ? '/'
      : options.orphan || options.artifactsPage ? '/artifacts'
        : options.attachment || options.reopen ? `/s/${sessionId}` : `/s/${sessionId}?artifact=${artifactId}`
  await page.goto(opened)
  return {
    unexpected, problems, saves, heads, files, sent, created, csp, fileWrites, shares, attached,
    loads, summaries, received,
  }
}
