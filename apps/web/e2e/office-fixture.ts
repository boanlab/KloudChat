import type { Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A chat with an office document (HWP, Word) beside it, the API mocked: the document's file
 * per version, saves that move one version at a time (409 otherwise), and an agent turn whose
 * tools save a version of their own (`agentFile`) on the server.
 */

export const sessionId = '77777777777777777777777777777777'
export const artifactId = '88888888888888888888888888888888'
const at = '2026-11-11T00:00:00.000Z'

export type OfficeOptions = {
  kind: 'hwp' | 'docx'
  file: Buffer
  /** The file the agent's turn leaves on the server. */
  agentFile: Buffer
  title: string
  conflict?: boolean
  holdTurn?: Promise<void>
  fromHome?: boolean
  /** A file attached to an earlier question in the chat. */
  attachment?: { id: string; name: string }
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
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (/Content Security Policy/i.test(message.text())) csp.push(message.text())
  })
  const { kind } = options
  const mime = kind === 'hwp'
    ? 'application/hwp+zip'
    : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  let artifact = {
    id: artifactId, title: options.title, kind, version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: {
      format: kind === 'hwp' ? 'hwpx' : 'docx', size: options.file.length, text: options.title,
    },
  }
  let file: Buffer = options.file
  let messages: Record<string, unknown>[] = options.attachment
    ? [{ id: 'q0', role: 'user', content: '이 공문을 채워 주세요', createdAt: at,
        attachments: [{ id: options.attachment.id, name: options.attachment.name, size: 38000 }] }]
    : []
  const session = {
    id: sessionId, title: '연수 공문', kind: 'chat', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false,
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
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
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
      Object.assign(session, { id: body.id, title: '새 작업' })
      return json(session)
    }
    if (path === `/artifacts/${kind}` && method === 'POST') {
      const body = request.postDataJSON()
      created.push(body)
      artifact = { ...artifact, sessionId: session.id, title: body.title ?? options.title }
      return route.fulfill({ status: 201, json: artifact })
    }
    if (path === '/sessions' && method === 'GET') return json([session])
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
      return route.fulfill({
        body: file, contentType: mime,
        headers: { 'X-Artifact-Version': String(artifact.version) },
      })
    }
    if (path === `/artifacts/${artifactId}/${kind}` && method === 'PUT') {
      const expected = url.searchParams.get('expectedVersion')
      const body = request.postDataBuffer()
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
  const opened = options.fromHome ? '/'
    : options.attachment ? `/s/${sessionId}` : `/s/${sessionId}?artifact=${artifactId}`
  await page.goto(opened)
  return { unexpected, problems, saves, heads, files, sent, created, csp }
}
