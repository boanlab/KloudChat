import { expect, test, type Page, type TestInfo } from '@playwright/test'

test.use({ serviceWorkers: 'block' })

const sourceId = '11111111111111111111111111111111'
const otherId = '22222222222222222222222222222222'
const forkId = '33333333333333333333333333333333'
const fileId = '44444444444444444444444444444444'
const cloneFileId = '55555555555555555555555555555555'
const addedFileId = '66666666666666666666666666666666'
const duplicateFileId = '77777777777777777777777777777777'
const duplicateCloneId = '88888888888888888888888888888888'
const at = '2026-09-14T00:00:00.000Z'
const prompts = ['첫 번째 개념을 알려줘.', '두 번째 설명을 짧게 해줘.', '마지막 예시를 알려줘.']
const answers = ['첫 번째 답변입니다.', '두 번째 답변입니다.', '마지막 답변입니다.']
const edited = '두 번째 설명을 세 문장으로 고쳐줘.'
const freshAnswer = '수정된 요청에 대한 새로운 답변입니다.'
const userIds = ['aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa3']
type Row = Record<string, unknown>
type Write = { method: string; path: string; data: Row }

function deferred() {
  let release!: () => void
  const promise = new Promise<void>((resolve) => { release = resolve })
  return { promise, release }
}

function message(id: string, role: string, content: string, attachments: Row[] = []) {
  return { id, role, content, attachments, createdAt: at, steps: null, variants: null,
    usage: null, model: role === 'assistant' ? 'fixture/model' : null,
    routing: null, startedFrom: null, rating: null, artifactIds: null, failure: null }
}

async function fixture(page: Page, info: TestInfo, options: {
  attachments?: boolean; privacy?: boolean; holdFork?: boolean; holdSend?: boolean;
  failFork?: boolean; failSend?: boolean; streamResponses?: boolean; skill?: boolean;
  duplicateAttachments?: boolean; failSendStored?: boolean; failRecoveryLookup?: boolean;
  holdPostSendList?: boolean;
} = {}) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  expect(new URL(origin).hostname).toBe('127.0.0.1')
  const forkGate = deferred()
  const sendGate = deferred()
  const listGate = deferred()
  if (!options.holdFork) forkGate.release()
  if (!options.holdSend) sendGate.release()
  if (!options.holdPostSendList) listGate.release()
  const originalFile = { id: fileId, name: 'notes.txt', mime: 'text/plain', type: 'text/plain',
    size: 32, tokens: 8, projectId: null, sessionId: sourceId, createdAt: at,
    preview: 'Synthetic attachment context.', error: null }
  const cloneFile = { ...originalFile, id: cloneFileId, sessionId: null }
  const duplicateFile = { ...originalFile, id: duplicateFileId, preview: 'A distinct file with the same name.' }
  const duplicateClone = { ...duplicateFile, id: duplicateCloneId, sessionId: null }
  const addedFile = { ...originalFile, id: addedFileId, name: 'additional.txt', sessionId: null }
  const messages = prompts.flatMap((prompt, index) => [
    message(userIds[index], 'user', prompt, options.attachments && index === 1
      ? [originalFile, ...(options.duplicateAttachments ? [duplicateFile] : [])] : []),
    message(`bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb${index + 1}`, 'assistant', answers[index]),
  ])
  const source = { id: sourceId, title: '원본 대화', kind: 'chat', model: 'fixture/model',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages, messageCount: messages.length, made: null, createdAt: at, updatedAt: at }
  const other = { ...source, id: otherId, title: '다른 대화', messages: [], messageCount: 0 }
  const rows = new Map<string, typeof source>([[sourceId, source], [otherId, other]])
  const original = JSON.stringify(source)
  const writes: Write[] = []
  const unexpected: string[] = []
  const pageErrors: string[] = []
  let forksFailed = 0
  let sendsFailed = 0
  let heldLists = 0
  let principal: 'a' | 'b' | null = 'a'
  const authSession = () => ({
    accessToken: 'fixture-only', expiresIn: 3600,
    user: { id: principal === 'b' ? 'fixture-user-b' : 'fixture-user',
      name: principal === 'b' ? 'Other fixture' : 'Edit fixture',
      email: principal === 'b' ? 'other@example.test' : 'fixture@example.test', role: 'user',
      status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
      allowedModels: [], createdAt: at, preferences: { autoMemory: false, showUsage: false,
        streamResponses: options.streamResponses ?? true } },
  })
  const forkCalls = () => writes.filter((write) => write.path.endsWith('/fork'))
  const sendCalls = () => writes.filter((write) => write.path.endsWith('/messages'))
  const decision = { code: 'privacy_decision_required',
    findings: [{ category: 'email', source: 'current_input', count: 1 }],
    requestedModels: ['fixture/model'], safeModels: [], allowedActions: ['mask_external', 'edit', 'cancel'],
    decisionToken: 'fixture-bound-to-fork', detectorVersion: 'privacy-detector-v1',
    policyVersion: 'external-data-guard-v1' }
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) {
      unexpected.push(`external ${url.origin}`)
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill(principal
      ? { json: authSession() } : { status: 401, json: { detail: 'not_authenticated' } })
    if (method === 'POST' && path === '/auth/logout') {
      principal = null
      return route.fulfill({ status: 204 })
    }
    if (method === 'POST' && path === '/auth/login') {
      principal = 'b'
      return route.fulfill({ json: authSession() })
    }
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: !!options.privacy }, passwordResetEnabled: false, dictationEnabled: false,
    } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: {
      models: [{ id: 'fixture/model', label: 'Fixture', name: 'Fixture', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external',
        creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/model', defaults: { chat: 'fixture/model' },
      litellmAvailable: true, autoRouting: { enabled: false, available: false },
    } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: {
      monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000,
    } })
    if (method === 'GET' && path === '/skills') return route.fulfill({ json: options.skill ? [{
      id: 'fixture-skill', name: '초안 검토', slug: 'draft-review', description: 'Synthetic style only',
      whenToUse: '', body: 'Preserve the supplied facts.', catalogKey: null, source: 'custom',
      kinds: ['chat'], requiredTools: [], estimatedTokens: 10, version: '1', enabled: true,
      visibility: 'private', installs: 0, originId: null, updatedAt: at,
    }] : [] })
    if (method === 'POST' && path === '/files') {
      writes.push({ method, path, data: { multipart: request.postData() ?? '' } })
      return route.fulfill({ status: 201, json: addedFile })
    }
    if (method === 'GET' && path === '/sessions') {
      const snapshot = structuredClone(principal === 'b' ? [other] : [...rows.values()])
      if (options.holdPostSendList && principal === 'a' && sendCalls().length > 0) {
        heldLists++
        await listGate.promise
      }
      return route.fulfill({ json: snapshot })
    }
    const sessionMatch = path.match(/^\/sessions\/([^/]+)$/)
    if (method === 'GET' && sessionMatch) {
      if (options.failRecoveryLookup && sendsFailed > 0 && sessionMatch[1] === forkId) {
        return route.fulfill({ status: 503, json: { detail: 'Synthetic lookup unavailable' } })
      }
      const row = principal === 'b' && sessionMatch[1] !== otherId ? undefined : rows.get(sessionMatch[1])
      return route.fulfill(row ? { json: row } : { status: 404, json: { detail: 'not_found' } })
    }
    const forkMatch = path.match(/^\/sessions\/([^/]+)\/messages\/([^/]+)\/fork$/)
    if (method === 'POST' && forkMatch) {
      writes.push({ method, path, data: request.postData() ? request.postDataJSON() : {} })
      await forkGate.promise
      if (options.failFork && forksFailed++ === 0) {
        return route.fulfill({ status: 503, json: { detail: 'Synthetic fork unavailable' } })
      }
      if (forkMatch[1] !== sourceId || !userIds.includes(forkMatch[2])) {
        unexpected.push(`invalid fork ${path}`)
        return route.fulfill({ status: 404, json: { detail: 'not_found' } })
      }
      const target = messages.findIndex((entry) => entry.id === forkMatch[2])
      const prefix = messages.slice(0, target).map((entry, index) => ({ ...entry, id: (index + 16).toString(16).padStart(32, '0') }))
      const fork = { ...source, id: forkId, title: '수정된 대화', messages: prefix, messageCount: prefix.length }
      rows.set(forkId, fork)
      const clones = options.attachments && forkMatch[2] === userIds[1]
        ? [cloneFile, ...(options.duplicateAttachments ? [duplicateClone] : [])] : []
      return route.fulfill({ json: { session: fork, attachments: clones,
        attachmentIdMap: clones.length ? { [fileId]: cloneFileId,
          ...(options.duplicateAttachments ? { [duplicateFileId]: duplicateCloneId } : {}) } : {} } })
    }
    const sendMatch = path.match(/^\/sessions\/([^/]+)\/messages$/)
    if (method === 'POST' && sendMatch) {
      const data = request.postDataJSON() as Row
      writes.push({ method, path, data })
      await sendGate.promise
      if (options.failSend && sendsFailed++ === 0) {
        if (options.failSendStored) {
          const row = rows.get(sendMatch[1])!
          row.messages = [...row.messages, message('cccccccccccccccccccccccccccccccc', 'user', String(data.content))]
          row.messageCount = row.messages.length
        }
        return route.fulfill({ status: 503, json: { detail: 'Synthetic send unavailable' } })
      }
      if (options.privacy && data.privacyAction !== 'mask_external') {
        return route.fulfill({ status: 409, json: { ...decision,
          decisionToken: sendCalls().length > 1 ? 'fixture-revised-envelope' : decision.decisionToken } })
      }
      const row = rows.get(sendMatch[1])
      if (!row) return route.fulfill({ status: 404, json: { detail: 'not_found' } })
      const files = [cloneFile, duplicateClone, addedFile].filter((file) => (data.attachments as string[] | undefined)?.includes(file.id))
      row.messages = [...row.messages, message('cccccccccccccccccccccccccccccccc', 'user', String(data.content), files),
        message('dddddddddddddddddddddddddddddddd', 'assistant', freshAnswer)]
      row.messageCount = row.messages.length
      return route.fulfill({ contentType: 'text/event-stream', body: [
        { type: 'delta', text: freshAnswer },
        { type: 'usage', inputTokens: 8, outputTokens: 8, credits: 0 }, { type: 'done' },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'GET' && (path.endsWith('/jobs') || ['/projects', '/skills', '/memory', '/tools',
      '/templates', '/connectors', '/connectors/catalog', '/designs', '/design-templates',
      '/prompt-templates', '/shares', '/artifacts', '/agents'].includes(path))) {
      return route.fulfill({ json: [] })
    }
    unexpected.push(`${method} ${path}`)
    if (method !== 'GET') writes.push({ method, path, data: request.postData() ? request.postDataJSON() : {} })
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sourceId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await expect(page.getByText(prompts[1], { exact: true })).toBeVisible()
  return { writes, unexpected, pageErrors, rows, original, source, forkCalls, sendCalls, forkGate, sendGate, listGate,
    heldLists: () => heldLists,
    assertPreserved: () => expect(JSON.stringify(source)).toBe(original),
    assertClean: () => { expect(unexpected).toEqual([]); expect(pageErrors).toEqual([]) },
    release: () => { forkGate.release(); sendGate.release(); listGate.release() } }
}

async function beginEdit(page: Page, index = 1) {
  await page.getByText(prompts[index], { exact: true }).hover()
  await page.getByRole('button', { name: '메시지 수정', exact: true }).nth(index).click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(prompts[index])
  await expect(page.getByRole('button', { name: '수정 취소', exact: true })).toBeVisible()
}

async function openSidebarControl(page: Page, name: string | RegExp) {
  const button = page.getByRole('button', { name, exact: typeof name === 'string' })
  const bounds = await button.boundingBox()
  if (!bounds || bounds.x < 0 || bounds.x + bounds.width > (page.viewportSize()?.width ?? 0)) {
    await page.getByRole('button', { name: '사이드바 토글', exact: true }).click()
  }
  await button.click()
}

async function settleUi(page: Page) {
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
}

for (const index of [1, 2]) {
  test(`${index === 1 ? 'middle' : 'last'} edit forks a prefix and survives reload without changing the original`, async ({ page }, info) => {
    const state = await fixture(page, info)
    await beginEdit(page, index)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.screenshot({ path: info.outputPath(`editing-${info.project.name}.png`), animations: 'disabled' })
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/s/${forkId}$`))
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    expect(state.forkCalls()).toHaveLength(1)
    expect(state.forkCalls()[0].path).toBe(`/sessions/${sourceId}/messages/${userIds[index]}/fork`)
    expect(state.sendCalls()).toEqual([{ method: 'POST', path: `/sessions/${forkId}/messages`,
      data: expect.objectContaining({ content: edited }) }])
    const saved = state.rows.get(forkId)!
    expect(saved.messages.map((entry) => entry.content)).toEqual([
      ...prompts.slice(0, index).flatMap((prompt, n) => [prompt, answers[n]]), edited, freshAnswer,
    ])
    state.assertPreserved()
    await page.reload()
    await expect(page.getByText(edited, { exact: true })).toBeVisible()
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    await expect(page.getByText(prompts[index], { exact: true })).toHaveCount(0)
    if (index === 1) await expect(page.getByText(prompts[2], { exact: true })).toHaveCount(0)
    await page.screenshot({ path: info.outputPath(`edited-fork-${info.project.name}.png`), animations: 'disabled' })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    await page.goto(`/s/${sourceId}`)
    for (const original of [...prompts, ...answers]) await expect(page.getByText(original, { exact: true })).toBeVisible()
    state.assertPreserved()
    state.assertClean()
  })
}

for (const action of ['cancel', 'Escape'] as const) {
  test(`${action} restores the existing draft without creating a fork`, async ({ page }, info) => {
    const state = await fixture(page, info)
    const input = page.getByLabel('프롬프트 입력')
    await input.fill('전송하지 않은 기존 초안')
    await beginEdit(page)
    await input.fill('취소할 수정 내용')
    if (action === 'cancel') await page.getByRole('button', { name: '수정 취소', exact: true }).click()
    else await input.press('Escape')
    await expect(input).toHaveValue('전송하지 않은 기존 초안')
    await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toHaveCount(0)
    expect(state.writes).toEqual([])
    state.assertPreserved()
    state.assertClean()
  })
}

test('blank edits and composing Enter do not fork or send', async ({ page }, info) => {
  const state = await fixture(page, info)
  await beginEdit(page)
  const input = page.getByLabel('프롬프트 입력')
  const submit = page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })
  await input.fill('   \n  ')
  await expect(submit).toBeDisabled()
  await input.press('Enter')
  await input.fill(edited)
  await input.dispatchEvent('compositionstart')
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 })
  expect(state.writes).toEqual([])
  await input.dispatchEvent('compositionend')
  await input.press('Enter')
  await expect.poll(() => state.sendCalls().length).toBe(1)
  state.assertPreserved()
  state.assertClean()
})

test('cancel restores draft files and selected skills changed during editing', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true, skill: true })
  const input = page.getByLabel('프롬프트 입력')
  await input.fill('파일과 스킬이 있는 기존 초안')
  await page.getByLabel('파일 선택', { exact: true }).setInputFiles({
    name: 'additional.txt', mimeType: 'text/plain', buffer: Buffer.from('Synthetic draft attachment.'),
  })
  await expect(page.getByRole('button', { name: 'additional.txt 제거', exact: true })).toBeVisible()
  const moreTools = page.getByRole('button', { name: '도구 더보기', exact: true })
  if (await moreTools.isVisible()) await moreTools.click()
  await page.getByRole('button', { name: '스킬', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: /초안 검토/ }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '초안 검토 제거', exact: true })).toBeVisible()
  await beginEdit(page)
  await page.getByRole('button', { name: '초안 검토 제거', exact: true }).click()
  await page.getByRole('button', { name: 'notes.txt 제거', exact: true }).click()
  await page.getByRole('button', { name: '수정 취소', exact: true }).click()
  await expect(input).toHaveValue('파일과 스킬이 있는 기존 초안')
  await expect(page.getByRole('button', { name: 'additional.txt 제거', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '초안 검토 제거', exact: true })).toBeVisible()
  expect(state.forkCalls()).toHaveLength(0)
  expect(state.sendCalls()).toHaveLength(0)
  state.assertPreserved()
  state.assertClean()
})

test('pending fork blocks duplicate submissions and restores the send control', async ({ page }, info) => {
  const state = await fixture(page, info, { holdFork: true })
  try {
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    const submit = page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })
    await submit.click()
    await expect.poll(() => state.forkCalls().length).toBe(1)
    await expect(submit).toBeDisabled()
    await page.getByLabel('프롬프트 입력').press('Enter')
    expect(state.forkCalls()).toHaveLength(1)
    expect(state.sendCalls()).toHaveLength(0)
    state.forkGate.release()
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    expect(state.sendCalls()).toHaveLength(1)
    state.assertPreserved()
    state.assertClean()
  } finally { state.release() }
})

for (const streamResponses of [true, false]) {
test(`an in-flight answer disables editing existing messages: streaming=${streamResponses}`, async ({ page }, info) => {
  const state = await fixture(page, info, { holdSend: true, streamResponses })
  try {
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect.poll(() => state.sendCalls().length).toBe(1)
    const pencils = page.getByRole('button', { name: '메시지 수정', exact: true })
    expect(await pencils.evaluateAll((buttons) => buttons.every((button) => (button as HTMLButtonElement).disabled))).toBe(true)
    state.sendGate.release()
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    state.assertPreserved()
    state.assertClean()
  } finally { state.release() }
})
}

test('edited attachment uses the cloned file ID, not the source attachment', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true })
  await beginEdit(page)
  await page.getByLabel('프롬프트 입력').fill(edited)
  await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.sendCalls()[0].data.attachments).toEqual([cloneFileId])
  expect(state.rows.get(forkId)!.messages.at(-2)?.attachments[0].id).toBe(cloneFileId)
  await page.reload()
  await expect(page.getByRole('button', { name: /notes\.txt/ })).toBeVisible()
  state.assertPreserved()
  state.assertClean()
})

test('removing an attachment while editing does not reattach its cloned file', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true })
  await beginEdit(page)
  await page.getByRole('button', { name: 'notes.txt 제거', exact: true }).click()
  await page.getByLabel('프롬프트 입력').fill(edited)
  await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.sendCalls()[0].data.attachments ?? []).toEqual([])
  expect(state.rows.get(forkId)!.messages.at(-2)?.attachments).toEqual([])
  state.assertPreserved()
  state.assertClean()
})

test('equal file names are mapped by ID after one attachment is removed', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true, duplicateAttachments: true })
  await beginEdit(page)
  const remove = page.getByRole('button', { name: 'notes.txt 제거', exact: true })
  await expect(remove).toHaveCount(2)
  await remove.first().click()
  await page.getByLabel('프롬프트 입력').fill(edited)
  await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.sendCalls()[0].data.attachments).toEqual([duplicateCloneId])
  expect(state.rows.get(forkId)!.messages.at(-2)?.attachments.map((file) => file.id)).toEqual([duplicateCloneId])
  state.assertPreserved()
  state.assertClean()
})

test('an upload during editing stays private until the forked message is sent', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true })
  await beginEdit(page)
  await page.getByLabel('파일 선택', { exact: true }).setInputFiles({
    name: 'additional.txt', mimeType: 'text/plain', buffer: Buffer.from('Synthetic added attachment.'),
  })
  await expect(page.getByRole('button', { name: 'additional.txt 제거', exact: true })).toBeVisible()
  const uploads = state.writes.filter((write) => write.path === '/files')
  expect(uploads).toHaveLength(1)
  expect(uploads[0].data.multipart).not.toContain(sourceId)
  expect(uploads[0].data.multipart).not.toMatch(/name="session_?[Ii]d"/)
  await page.getByLabel('프롬프트 입력').fill(edited)
  await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.sendCalls()[0].data.attachments).toEqual([cloneFileId, addedFileId])
  state.assertPreserved()
  state.assertClean()
})

test('privacy 409 retries the same fork and cloned attachments with its decision token', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true, privacy: true })
  await beginEdit(page)
  await page.getByLabel('프롬프트 입력').fill(edited)
  await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(dialog).toBeVisible()
  await expect(page).toHaveURL(new RegExp(`/s/${sourceId}$`))
  expect(state.rows.get(forkId)!.messages).toHaveLength(2)
  await dialog.getByRole('button', { name: '가린 뒤 기존 모델 사용' }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.forkCalls()).toHaveLength(1)
  expect(state.sendCalls()).toHaveLength(2)
  expect(state.sendCalls().every((call) => call.path === `/sessions/${forkId}/messages`)).toBe(true)
  expect(state.sendCalls()[1].data).toMatchObject({ content: edited, attachments: [cloneFileId],
    privacyAction: 'mask_external', privacyDecisionToken: 'fixture-bound-to-fork' })
  state.assertPreserved()
  state.assertClean()
})

test('privacy edit return removes a cloned attachment and requests fresh consent on the same fork', async ({ page }, info) => {
  const state = await fixture(page, info, { attachments: true, privacy: true })
  await beginEdit(page)
  await page.getByLabel('프롬프트 입력').fill(edited)
  const submit = page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })
  await submit.click()
  const dialog = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '편집으로 돌아가기', exact: true }).click()
  await page.getByRole('button', { name: 'notes.txt 제거', exact: true }).click()
  await submit.click()
  await expect(dialog).toBeVisible()
  expect(state.sendCalls()[1].data.attachments ?? []).toEqual([])
  expect(state.sendCalls()[1].data.privacyDecisionToken).toBeUndefined()
  await dialog.getByRole('button', { name: '가린 뒤 기존 모델 사용', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  expect(state.forkCalls()).toHaveLength(1)
  expect(state.sendCalls()).toHaveLength(3)
  expect(state.sendCalls()[2].data).toMatchObject({ privacyAction: 'mask_external',
    privacyDecisionToken: 'fixture-revised-envelope' })
  expect(state.sendCalls()[2].data.attachments ?? []).toEqual([])
  state.assertPreserved()
  state.assertClean()
})

for (const failure of ['fork', 'send'] as const) {
  test(`${failure} failure keeps the edited draft and permits retry`, async ({ page }, info) => {
    const state = await fixture(page, info, { failFork: failure === 'fork', failSend: failure === 'send' })
    await page.getByLabel('프롬프트 입력').fill('보존할 기존 초안')
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('전송하지 못했습니다')
    await expect(page.getByLabel('프롬프트 입력')).toHaveValue(edited)
    await expect(page).toHaveURL(new RegExp(`/s/${sourceId}$`))
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    expect(state.forkCalls()).toHaveLength(failure === 'fork' ? 2 : 1)
    expect(state.sendCalls()).toHaveLength(failure === 'fork' ? 1 : 2)
    state.assertPreserved()
    state.assertClean()
  })
}

for (const recovery of ['stored', 'unknown'] as const) {
  test(`a ${recovery} send outcome does not offer automatic edit resubmission`, async ({ page }, info) => {
    const state = await fixture(page, info, { failSend: true, failSendStored: recovery === 'stored',
      failRecoveryLookup: recovery === 'unknown' })
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/s/${forkId}$`))
    await expect(page.getByText(recovery === 'stored'
      ? '새 대화에 전송 기록이 있습니다. 저장된 답변을 확인한 뒤 다시 시도하세요.'
      : '전송 상태를 확인하지 못했습니다. 새 대화를 새로고침해 확인한 뒤 다시 시도하세요.', { exact: true })).toBeVisible()
    const input = page.getByLabel('프롬프트 입력')
    await expect(input).toHaveValue('')
    await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '전송', exact: true })).toBeDisabled()
    await input.press('Enter')
    expect(state.forkCalls()).toHaveLength(1)
    expect(state.sendCalls()).toHaveLength(1)
    if (recovery === 'stored') {
      await expect(page.getByText(edited, { exact: true })).toBeVisible()
      expect(state.rows.get(forkId)!.messages.at(-1)?.content).toBe(edited)
    }
    state.assertPreserved()
    state.assertClean()
  })
}

test('a newly sent message becomes editable after stored IDs reconcile without reload', async ({ page }, info) => {
  const state = await fixture(page, info)
  const content = '저장 ID를 확인할 새 질문입니다.'
  await page.getByLabel('프롬프트 입력').fill(content)
  await page.getByRole('button', { name: '전송', exact: true }).click()
  await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
  await page.getByText(content, { exact: true }).hover()
  const pencil = page.getByRole('button', { name: '메시지 수정', exact: true }).last()
  await expect(pencil).toBeEnabled()
  await pencil.click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(content)
  await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toBeVisible()
  expect(state.forkCalls()).toHaveLength(0)
  expect(state.sendCalls()).toHaveLength(1)
  expect(state.rows.get(sourceId)!.messages.at(-2)?.id).toBe('cccccccccccccccccccccccccccccccc')
  state.assertClean()
})

test('moving to another session does not carry the edit target or edited draft', async ({ page }, info) => {
  const state = await fixture(page, info)
  await beginEdit(page)
  await page.getByLabel('프롬프트 입력').fill(edited)
  await openSidebarControl(page, '다른 대화')
  await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toHaveCount(0)
  expect(state.writes).toEqual([])
  state.assertPreserved()
  state.assertClean()
})

for (const phase of ['fork', 'send', 'privacy'] as const) {
  test(`a delayed ${phase} response does not take over another session`, async ({ page }, info) => {
    const state = await fixture(page, info, { holdFork: phase === 'fork', holdSend: phase !== 'fork',
      privacy: phase === 'privacy' })
    try {
      await beginEdit(page)
      await page.getByLabel('프롬프트 입력').fill(edited)
      await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
      await expect.poll(() => phase === 'fork' ? state.forkCalls().length : state.sendCalls().length).toBe(1)
      await openSidebarControl(page, '다른 대화')
      await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
      const response = page.waitForResponse((res) => res.request().method() === 'POST'
        && res.url().endsWith(phase === 'fork' ? '/fork' : '/messages'))
      state.release()
      await response
      await settleUi(page)
      await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
      await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
      await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toHaveCount(0)
      await expect(page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })).toHaveCount(0)
      await expect(page.getByText(edited, { exact: true })).toHaveCount(0)
      await expect(page.getByText(freshAnswer, { exact: true })).toHaveCount(0)
      expect(state.sendCalls()).toHaveLength(phase === 'fork' ? 0 : 1)
      state.assertPreserved()
      state.assertClean()
    } finally { state.release() }
  })
}

test('a fork started by account A cannot populate account B after login', async ({ page }, info) => {
  const state = await fixture(page, info, { holdFork: true })
  try {
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect.poll(() => state.forkCalls().length).toBe(1)
    await openSidebarControl(page, /계정 메뉴.*fixture@example\.test/)
    await page.getByRole('menuitem', { name: '로그아웃', exact: true }).click()
    await page.getByLabel('이메일', { exact: true }).fill('other@example.test')
    await page.getByLabel('비밀번호', { exact: true }).fill('Synthetic-fixture-only-123!')
    await page.locator('form').getByRole('button', { name: '로그인', exact: true }).click()
    await openSidebarControl(page, '다른 대화')
    await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
    const response = page.waitForResponse((res) => res.request().method() === 'POST' && res.url().endsWith('/fork'))
    state.forkGate.release()
    await response
    await settleUi(page)
    await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
    await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
    await expect(page.getByRole('button', { name: '원본 대화', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '수정된 대화', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '수정 후 다시 보내기', exact: true })).toHaveCount(0)
    for (const text of [...prompts, edited, freshAnswer]) await expect(page.getByText(text, { exact: true })).toHaveCount(0)
    expect(state.sendCalls()).toHaveLength(0)
    state.assertPreserved()
    state.assertClean()
  } finally { state.release() }
})

test('a post-send session list from account A cannot replace account B workspace', async ({ page }, info) => {
  const state = await fixture(page, info, { holdPostSendList: true })
  try {
    await beginEdit(page)
    await page.getByLabel('프롬프트 입력').fill(edited)
    await page.getByRole('button', { name: '수정 후 다시 보내기', exact: true }).click()
    await expect(page.getByText(freshAnswer, { exact: true })).toBeVisible()
    await expect.poll(state.heldLists).toBeGreaterThan(0)
    await openSidebarControl(page, /계정 메뉴.*fixture@example\.test/)
    await page.getByRole('menuitem', { name: '로그아웃', exact: true }).click()
    await page.getByLabel('이메일', { exact: true }).fill('other@example.test')
    await page.getByLabel('비밀번호', { exact: true }).fill('Synthetic-fixture-only-123!')
    await page.locator('form').getByRole('button', { name: '로그인', exact: true }).click()
    await openSidebarControl(page, '다른 대화')
    await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
    const response = page.waitForResponse((res) => res.request().method() === 'GET' && res.url().endsWith('/sessions'))
    state.listGate.release()
    await response
    await settleUi(page)
    await expect(page).toHaveURL(new RegExp(`/s/${otherId}$`))
    await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
    await expect(page.getByRole('button', { name: '원본 대화', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '수정된 대화', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '다른 대화', exact: true })).toHaveCount(1)
    for (const text of [...prompts, edited, freshAnswer]) await expect(page.getByText(text, { exact: true })).toHaveCount(0)
    expect(state.sendCalls()).toHaveLength(1)
    state.assertPreserved()
    state.assertClean()
  } finally { state.release() }
})
