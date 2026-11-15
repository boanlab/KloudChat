import { expect, test, type Page } from '@playwright/test'
import { at, chatId, composerFixture } from './composer-fixture'
import { openTool } from './composer-tools'
import { viewport } from './project'

/**
 * 양식 (form files): a PowerPoint attached as a form is followed (its chip says 양식, the deck
 * becomes a PowerPoint file), a project's saved form shows beside the 형식 chip and can be left
 * off, the project keeps one saved form per kind, and an answer's file says which form it
 * followed. Screenshots of each go to `FORM_FILES_SHOT_DIR` when set.
 */

const projectId = 'p0000000000000000000000000000001'
const savedId = 'f9'.padEnd(32, '9')
const formId = 'f1'.padEnd(32, '1')
const slidesId = 'c9'.padEnd(32, '9')
const deckId = 'd9'.padEnd(32, '9')

const pptxSummary = (extra: Record<string, unknown> = {}) => ({
  kind: 'pptx', verdict: 'form', score: 0.95, layouts: 9, size: '16:9', font: '맑은 고딕', accent: '#1F4E8C', ...extra,
})
const fileRow = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id, name, size: 48213, mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  tokens: 120, projectId: null, sessionId: null, preview: '', error: null, origin: 'upload', createdAt: at, ...extra,
})
const saved = fileRow(savedId, '발표_기본.pptx', { role: 'form', form: pptxSummary() })
const project = (formFiles: Record<string, string> = { pptx: savedId }) => ({
  id: projectId, name: '2027 지원사업 신청', description: '', emoji: '📁', instructions: '', skillIds: [],
  designSystemId: null, renderTemplates: {}, formFiles, formRows: formFiles.pptx ? [saved] : [],
  designBrief: '강조색 #334155.\n본문은 명조.\n제목은 명사구로 쓴다.\n\n(테마에서 옮김)',
  strictLocalWebSearch: null, files: [], sessionIds: [slidesId], createdAt: at, updatedAt: at,
})

const shots = process.env.FORM_FILES_SHOT_DIR
async function shot(page: Page, name: string, info: Parameters<Parameters<typeof test>[2]>[1]) {
  if (!shots) return
  // the start screen fades in; the shot waits for it
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${shots}/${name}-${viewport(info)}.png`, fullPage: false })
}

/** The project, its forms, and a deck written on its form, over the composer fixture. */
async function formsFixture(page: Page, info: Parameters<Parameters<typeof test>[2]>[1]) {
  const fixture = await composerFixture(page, info, {
    files: [
      fileRow(formId, '2027_사업계획_발표양식.pptx', { form: pptxSummary({ layouts: 11 }) }),
      fileRow('f2'.padEnd(32, '2'), '사업계획서_초안_v3.docx', {
        mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        form: { kind: 'docx', verdict: 'material', score: 0.1 },
      }),
      fileRow('f5'.padEnd(32, '5'), '신청서_별지1호.hwpx', {
        mime: 'application/hwp+zip',
        form: { kind: 'hwpx', verdict: 'form', score: 0.8, fields: 6, tables: 2 },
      }),
      fileRow('f4'.padEnd(32, '4'), '사업계획서_서식.docx', {
        mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        form: { kind: 'docx', verdict: 'form', score: 0.85, headings: 3, sections: 5, header: true, cover: true, fields: 4 },
      }),
    ],
  })
  let current = project()
  const patches: Record<string, unknown>[] = []
  const converted: string[] = []
  const deck = {
    id: deckId, kind: 'pptx', title: '2027 사업계획 발표', version: 1, sessionId: slidesId, projectId,
    createdAt: at, updatedAt: at, partial: false,
    data: {
      format: 'pptx', size: 81234, slides: 8, text: '2027 사업계획 발표',
      form: {
        kind: 'pptx', name: '발표_기본.pptx', source: 'project', fileId: savedId,
        followed: { layouts: { 'Title Slide': 2, 'Title and Content': 3, 'Title Only': 2, Blank: 1 },
          slides: 8, size: '16:9', fonts: { major: '맑은 고딕', minor: '맑은 고딕' },
          standIns: [{ slide: 7, kind: 'free', layout: 'Title Only' }] },
      },
    },
  }
  const slidesSession = {
    id: slidesId, kind: 'slides', title: '사업계획 발표', model: 'fixture/model', routingMode: 'manual',
    projectId, agentId: null, artifactId: null, pending: null, skillIds: [], renderTemplateId: null,
    formFiles: null, pinned: false, messageCount: 2, createdAt: at, updatedAt: at, preview: null, made: null,
    messages: [
      { id: 'm9'.padEnd(32, '1'), role: 'user', content: '초안 내용으로 심사 발표 자료 8장 만들어 줘.', createdAt: at,
        attachments: [
          { id: 'f2'.padEnd(32, '2'), name: '사업계획서_초안_v3.docx', size: 20480, type: 'docx', role: 'material' },
          { id: savedId, name: '발표_기본.pptx', role: 'form', formKind: 'pptx', source: 'project', size: '', type: '' },
          { id: 'f3'.padEnd(32, '3'), name: '신청서 양식.hwp', size: 30720, type: 'hwp', role: 'material',
            formNote: '이 파일은 예전 한글 형식(.hwp)이라 양식으로 따를 수 없습니다. 한글에서 파일 › 다른 이름으로 저장 › 파일 형식 「한글 표준 문서 (*.hwpx)」로 저장한 뒤 다시 올려 주세요. 이번에는 이 파일을 자료로만 읽었습니다.' },
        ] },
      { id: 'm9'.padEnd(32, '2'), role: 'assistant', content: '발표 자료를 프로젝트 양식에 맞춰 PowerPoint로 만들었습니다. 2단계 종료일은 자료에 없어 [2단계 종료일]로 비워 두었습니다.',
        attachments: [], model: 'fixture/model', createdAt: at, artifactIds: [deckId], artifactVersion: 1 },
    ],
  }
  const reportId = 'c8'.padEnd(32, '8')
  const wordId = 'd8'.padEnd(32, '8')
  const word = {
    id: wordId, kind: 'docx', title: '설비 점검 통합 서비스 사업계획서', version: 1, sessionId: reportId, projectId,
    createdAt: at, updatedAt: at, partial: false,
    data: {
      format: 'docx', size: 40211, text: '사업계획서',
      form: {
        kind: 'docx', name: '사업계획서_서식.docx', source: 'attached', fileId: 'f4'.padEnd(32, '4'),
        followed: { chapters: ['1. 사업 개요', '2. 추진 계획', '3. 예산'], added: ['4. 위험 요인'], unwritten: [],
          filled: ['사업명', '기관명', '작성일'], guidanceRemoved: 3, header: true, footer: true, numbered: false },
      },
    },
  }
  const hwpId = 'd7'.padEnd(32, '7')
  const hangul = {
    id: hwpId, kind: 'hwp', title: '신청서_별지1호', version: 2, sessionId: 'c7'.padEnd(32, '7'), projectId,
    createdAt: at, updatedAt: at, partial: false,
    data: {
      format: 'hwpx', size: 21004, text: '사업명 설비 점검 통합 서비스',
      form: { kind: 'hwpx', name: '신청서_별지1호.hwpx', source: 'attached', fileId: 'f5'.padEnd(32, '5'),
        followed: { cells: 6, cellsFilled: 4, cellsEmpty: ['대표자', '연락처'] } },
    },
  }
  const reportSession = {
    ...slidesSession, id: reportId, kind: 'report', title: '사업계획서', artifactId: null,
    messages: [
      { id: 'm8'.padEnd(32, '1'), role: 'user', content: '초안으로 사업계획서를 써 줘.', createdAt: at,
        attachments: [
          { id: 'f4'.padEnd(32, '4'), name: '사업계획서_서식.docx', size: 30210, type: 'docx', role: 'form', formKind: 'docx' },
          { id: 'f2'.padEnd(32, '2'), name: '사업계획서_초안_v3.docx', size: 20480, type: 'docx', role: 'material' },
        ] },
      { id: 'm8'.padEnd(32, '2'), role: 'assistant', content: '사업계획서를 첨부한 Word 양식에 맞춰 썼습니다.',
        attachments: [], model: 'fixture/model', createdAt: at, artifactIds: [wordId], artifactVersion: 1 },
      { id: 'm8'.padEnd(32, '3'), role: 'user', content: '이 신청서도 채워 줘.', createdAt: at,
        attachments: [{ id: 'f5'.padEnd(32, '5'), name: '신청서_별지1호.hwpx', size: 21004, type: 'hwpx', role: 'form', formKind: 'hwpx' }] },
      { id: 'm8'.padEnd(32, '4'), role: 'assistant', content: '신청서를 채웠습니다. 대표자와 연락처는 자료에 없어 [대표자], [연락처]로 남겼습니다.',
        attachments: [], model: 'fixture/model', createdAt: at, artifactIds: [hwpId], artifactVersion: 2 },
    ],
  }
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.slice(4)
    const method = route.request().method()
    if (method === 'GET' && path === '/projects') return route.fulfill({ json: [current] })
    if (method === 'GET' && path === `/projects/${projectId}`) return route.fulfill({ json: current })
    if (method === 'PATCH' && path === `/projects/${projectId}`) {
      const body = route.request().postDataJSON() as { formFiles?: Record<string, string | null> }
      patches.push(body)
      const slots = { ...current.formFiles } as Record<string, string>
      for (const [kind, id] of Object.entries(body.formFiles ?? {})) {
        if (id) slots[kind] = id
        else delete slots[kind]
      }
      current = project(slots)
      return route.fulfill({ json: current })
    }
    if (method === 'POST' && path === '/design-brief/read') {
      return route.fulfill({ json: { spec: { accent: '#334155', body_face: 'serif' }, tone: [] } })
    }
    if (method === 'GET' && path === '/user-templates') {
      return route.fulfill({ json: { mine: [{ id: 'ut-1', name: '우리 회의록', description: '팀 회의록', mine: true }], shared: [] } })
    }
    if (method === 'POST' && path === '/user-templates/ut-1/to-agent') {
      converted.push(path)
      return route.fulfill({ status: 201, json: { id: 'a9'.padEnd(32, '0'), name: '우리 회의록' } })
    }
    if (method === 'POST' && path === '/forms/from-file') {
      return route.fulfill({ status: 201, json: saved })
    }
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [deck, word, hangul] })
    if (method === 'GET' && path === `/artifacts/${hwpId}`) return route.fulfill({ json: hangul })
    if (method === 'GET' && path === `/artifacts/${wordId}`) return route.fulfill({ json: word })
    if (method === 'GET' && path === `/sessions/${reportId}`) return route.fulfill({ json: reportSession })
    if (method === 'GET' && path === `/sessions/${reportId}/messages`) return route.fulfill({ json: reportSession.messages })
    if (method === 'GET' && path === `/artifacts/${deckId}`) return route.fulfill({ json: deck })
    if (method === 'GET' && path === `/sessions/${slidesId}`) return route.fulfill({ json: slidesSession })
    if (method === 'GET' && path === `/sessions/${slidesId}/messages`) return route.fulfill({ json: slidesSession.messages })
    if (method === 'GET' && path === '/sessions') {
      return route.fulfill({ json: [{ ...slidesSession, messages: null }, { ...reportSession, messages: null }] })
    }
    return route.fallback()
  })
  return { ...fixture, patches, converted }
}

test('a PowerPoint attached as a form is followed and the project form steps aside', { tag: '@phone' }, async ({ page }, info) => {
  const { sends } = await formsFixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('button', { name: '새로 시작' }).click()
  await page.getByRole('menuitem', { name: '슬라이드' }).click()
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()

  // the project's saved form shows beside 형식, and can be left off for this conversation
  const savedChip = page.getByTestId('saved-form-chip')
  await expect(savedChip).toContainText('발표_기본.pptx')
  await expect(savedChip).toContainText('프로젝트 양식')
  await shot(page, 'composer-saved', info)
  await page.getByRole('button', { name: '발표_기본.pptx 양식 빼기' }).click()
  await expect(savedChip).toHaveAttribute('data-off', '')
  await savedChip.click()
  await expect(savedChip).not.toHaveAttribute('data-off', '')

  // attached: a PowerPoint the server read as a form, and a Word file read as material
  await openTool(page, '파일함에서 고르기')
  const dialog = page.getByRole('dialog', { name: '파일함에서 고르기' })
  await dialog.getByLabel('2027_사업계획_발표양식.pptx 고르기').check()
  await dialog.getByLabel('사업계획서_초안_v3.docx 고르기').check()
  await dialog.getByRole('button', { name: '2개 붙이기' }).click()
  // both are Office files the writers follow: each chip says how it is read
  await expect(page.getByTestId('form-role')).toHaveCount(2)
  const role = page.getByTestId('form-role').first()
  await expect(role).toHaveAttribute('data-role', 'form')
  await expect(page.getByTestId('form-role').nth(1)).toHaveAttribute('data-role', 'material')
  // a form that is attached outranks the saved one, and the deck becomes a PowerPoint file
  await expect(savedChip).toHaveCount(0)
  await page.getByLabel('프롬프트 입력').fill('초안 내용으로 심사 발표 자료 10장 만들어 줘. 표지는 양식 그대로.')
  await shot(page, 'composer-form', info)

  // read as material instead, then back: the chip's pick goes with the turn
  await role.getByRole('radio', { name: '자료' }).click()
  await expect(role).toHaveAttribute('data-role', 'material')
  await expect(savedChip).toBeVisible()
  await role.getByRole('radio', { name: '양식' }).click()
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.attachmentRoles).toEqual({ [formId]: 'form' })
  expect(sends[0].body.outputFormat).toBe('pptx')
})

test('a project keeps one saved form per kind', { tag: '@phone' }, async ({ page }, info) => {
  const { patches } = await formsFixture(page, info)
  await page.goto(`/projects/${projectId}`)
  const card = page.getByTestId('project-form-files')
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByTestId('form-slot-pptx')).toContainText('발표_기본.pptx')
  await expect(card.getByTestId('form-slot-pptx')).toContainText('레이아웃 9개')
  await expect(card.getByTestId('form-slot-docx')).toContainText('양식 없음')
  await expect(card.getByTestId('form-slot-hwpx')).toContainText('양식 없음')
  await shot(page, 'project-forms', info)
  await card.getByRole('button', { name: '발표_기본.pptx 양식 빼기' }).click()
  await expect(card.getByTestId('form-slot-pptx')).toContainText('양식 없음')
  expect(patches).toEqual([{ formFiles: { pptx: null } }])
  await card.getByRole('button', { name: 'PowerPoint 양식을 파일함에서 고르기' }).click()
  const dialog = page.getByRole('dialog', { name: '파일함에서 고르기' })
  await dialog.getByLabel('2027_사업계획_발표양식.pptx 고르기').check()
  await dialog.getByRole('button', { name: '1개 붙이기' }).click()
  await expect(card.getByTestId('form-slot-pptx')).toContainText('발표_기본.pptx')
  expect(patches.at(-1)).toEqual({ formFiles: { pptx: savedId } })
})

test('an answer says which form its file followed', { tag: '@phone' }, async ({ page }, info) => {
  await formsFixture(page, info)
  await page.goto(`/s/${slidesId}`)
  await expect(page.getByTestId('attachment-form')).toHaveText('프로젝트 양식')
  await expect(page.getByTestId('attachment-form-note')).toContainText('(*.hwpx)')
  const line = page.getByTestId('followed-form')
  await expect(line).toContainText('양식을 따름')
  await expect(line).toContainText('발표_기본.pptx (프로젝트 기본)')
  await line.getByRole('button').click()
  await expect(line).toContainText('쓴 레이아웃: Title Slide 2, Title and Content 3')
  await expect(line).toContainText('7번 슬라이드')
  await line.scrollIntoViewIfNeeded()
  await shot(page, 'answer-followed', info)
  expect(chatId).toBeTruthy()
})

test('a Word form attached in a report conversation makes the report a Word file on it', { tag: '@phone' }, async ({ page }, info) => {
  const { sends } = await formsFixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('button', { name: '새로 시작' }).click()
  await page.getByRole('menuitem', { name: '보고서' }).click()
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await openTool(page, '파일함에서 고르기')
  const dialog = page.getByRole('dialog', { name: '파일함에서 고르기' })
  await dialog.getByLabel('사업계획서_서식.docx 고르기').check()
  await dialog.getByLabel('사업계획서_초안_v3.docx 고르기').check()
  await dialog.getByRole('button', { name: '2개 붙이기' }).click()
  const roles = page.getByTestId('form-role')
  await expect(roles).toHaveCount(2)
  // a Word form, not the 한글 one, for this send
  await expect(roles.first()).toHaveAttribute('data-role', 'form')
  await expect(roles.nth(1)).toHaveAttribute('data-role', 'material')
  await page.getByLabel('프롬프트 입력').fill('초안으로 사업계획서를 써 줘.')
  await shot(page, 'composer-word-form', info)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.outputFormat).toBe('docx')
})

test('a Word report says which form it followed and what it filled', { tag: '@phone' }, async ({ page }, info) => {
  await formsFixture(page, info)
  await page.goto(`/s/${'c8'.padEnd(32, '8')}`)
  await expect(page.getByTestId('attachment-form').first()).toHaveText('양식')
  const line = page.getByTestId('followed-form').first()
  await expect(line).toContainText('사업계획서_서식.docx (첨부)')
  await line.getByRole('button').click()
  await expect(line).toContainText('양식의 장에 맞춰 씀: 1. 사업 개요, 2. 추진 계획, 3. 예산')
  await expect(line).toContainText('표지 칸 채움: 사업명, 기관명, 작성일')
  await expect(line).toContainText('「4. 위험 요인」 장은')
  await line.scrollIntoViewIfNeeded()
  await shot(page, 'answer-word-followed', info)
})

test('a project writes its look as a design brief and sees what is read from it', { tag: '@phone' }, async ({ page }, info) => {
  await formsFixture(page, info)
  await page.goto(`/projects/${projectId}`)
  const card = page.getByTestId('project-design-brief')
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByTestId('brief-from-theme')).toBeVisible()
  await expect(card.getByTestId('brief-reading')).toContainText('강조 #334155')
  await expect(card.getByTestId('brief-reading')).toContainText('본문 명조')
  // no theme or default template cards are left
  await expect(page.getByText('기본 템플릿')).toHaveCount(0)
  await shot(page, 'project-design-brief', info)
})

test('templates a person made become agents on request', { tag: '@phone' }, async ({ page }, info) => {
  const { converted } = await formsFixture(page, info)
  await page.goto('/customize?tab=agents')
  const card = page.getByTestId('old-templates')
  await expect(card).toContainText('우리 회의록')
  await card.scrollIntoViewIfNeeded()
  await shot(page, 'old-templates', info)
  await card.getByRole('button', { name: '우리 회의록 템플릿을 에이전트로 옮기기' }).click()
  await expect.poll(() => converted.length).toBe(1)
  await expect(page).toHaveURL(/\/customize\/agents\/a9/)
})

test('a 한글 form makes the report the form itself, filled, and says what is still empty', { tag: '@phone' }, async ({ page }, info) => {
  const { sends } = await formsFixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('button', { name: '새로 시작' }).click()
  await page.getByRole('menuitem', { name: '보고서' }).click()
  await openTool(page, '파일함에서 고르기')
  const dialog = page.getByRole('dialog', { name: '파일함에서 고르기' })
  await dialog.getByLabel('신청서_별지1호.hwpx 고르기').check()
  await dialog.getByRole('button', { name: '1개 붙이기' }).click()
  await expect(page.getByTestId('form-role')).toHaveAttribute('data-role', 'form')
  await page.getByLabel('프롬프트 입력').fill('초안 내용으로 이 신청서를 채워 줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.outputFormat).toBe('hwp')

  await page.goto(`/s/${'c8'.padEnd(32, '8')}`)
  const line = page.getByTestId('followed-form').last()
  await expect(line).toContainText('신청서_별지1호.hwpx (첨부)')
  await line.getByRole('button').click()
  await expect(line).toContainText('채운 칸 4/6')
  await expect(line).toContainText('아직 빈 칸: 대표자, 연락처')
  await line.scrollIntoViewIfNeeded()
  await shot(page, 'answer-hangul-followed', info)
})
