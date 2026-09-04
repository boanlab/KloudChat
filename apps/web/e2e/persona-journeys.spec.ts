/** Persona journeys end to end against a real backend and model. */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { answerText, artifactIds, pickToolModel, signIn, storedArtifacts } from './helpers'

// Retried: these assertions are on what a small model wrote.
test.describe.configure({ retries: 2 })

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const stamp = () => Math.random().toString(36).slice(2, 7)

/** Local model: free, and no external provider. */
const useLocalModel = pickToolModel

/** Sends from `/new/chat` and waits for the session to be created. */
async function ask(page: Page, prompt: string) {
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST',
    ),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
}

async function newProject(page: Page, name: string, instructions: string) {
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).first().click()
  await page.getByLabel(/이름/).first().fill(name)
  await page.getByLabel(/지침/).first().fill(instructions)
  await page.getByRole('button', { name: '만들기', exact: true }).last().click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
}


test('대학원생 — 프로젝트 지식을 올리고 그 안에서만 아는 값을 답하게 한다', async ({ page }) => {
  const token = `RUN-${stamp().toUpperCase()}`
  // Spelled out with an example: a small model obeys a one-line rule only most of the time.
  await newProject(
    page,
    `실험 ${stamp()}`,
    '출력 형식 규칙: 모든 답변의 첫 글자는 반드시 "[연구]" 여야 한다. 인사, 짧은 답, ' +
      '숫자만 있는 답에도 예외 없이 맨 앞에 붙인다. 예: "[연구] 값은 0.5 입니다."',
  )

  await page.getByRole('tab', { name: /지식/ }).click()
  await page.getByLabel('지식 파일 선택').setInputFiles({
    name: 'results.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(`run,macro_f1\n${token},0.9142\n`, 'utf-8'),
  })
  await expect(page.getByText('results.csv')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  await useLocalModel(page)
  // "Copy it": a summarising model rounds the figure. Must not forbid the "[연구]" prefix.
  await page
    .getByLabel('프롬프트 입력')
    .fill(`지식 파일에서 ${token} 행의 macro_f1 값이 얼마야? 파일에 적힌 숫자를 그대로 옮겨 적어줘.`)
  await page.getByLabel('프롬프트 입력').press('Enter')

  // The figure exists only in the uploaded file; `0.914` tolerates the model's rounding.
  await expect(answerText(page, /0[.,]914|91[.,]4\s*%/)).toBeVisible({ timeout: 150_000 })
  // The project instruction reached the same turn.
  await expect(answerText(page, '[연구]')).toBeVisible({ timeout: 30_000 })
})

test('개발직 — 계산을 암산이 아니라 코드로 검증한다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, 'execute_code 로 2**31 - 1 을 계산해서 숫자만 알려줘.')

  // Thousands separators are the model's choice.
  await expect(answerText(page, /2[,\s]?147[,\s]?483[,\s]?647/)).toBeVisible({ timeout: 150_000 })
  // The step timeline shows the number came from a run.
  await expect(page.getByText(/코드 실행/).first()).toBeVisible({ timeout: 30_000 })
})

test('사무직 — 올린 표에서 값을 찾아 답한다', async ({ page }) => {
  const token = `INV-${stamp().toUpperCase()}`
  await page.goto('/new/chat')
  await useLocalModel(page)

  await page.getByLabel('파일 선택').setInputFiles({
    name: 'budget.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(`항목,금액\n${token},4820000\n비품,120000\n`, 'utf-8'),
  })
  await expect(page.getByText('budget.csv')).toBeVisible({ timeout: 30_000 })
  await ask(page, `${token} 항목의 금액이 얼마인지 첨부 파일에서 찾아 숫자만 알려줘.`)

  await expect(answerText(page, /4[,]?820[,]?000/)).toBeVisible({ timeout: 150_000 })
})

test('연구직 — 한 번 기억시킨 사실을 다른 대화에서도 안다', async ({ page }) => {
  const token = `LAB-${stamp().toUpperCase()}`
  const name = `소속 ${stamp()}`
  await page.goto('/memory')
  await page.getByRole('button', { name: /새 메모리|메모리 추가/ }).first().click()
  await page.getByLabel(/이름/).first().fill(name)
  await page.getByLabel(/내용|본문/).first().fill(`사용자의 실험실 코드는 ${token} 이다.`)
  await page.getByRole('button', { name: '저장', exact: true }).last().click()
  await expect(page.getByText(token)).toBeVisible({ timeout: 20_000 })

  try {
    await page.goto('/new/chat')
    await useLocalModel(page)
    // A random token tells recall from prior knowledge.
    await ask(
      page,
      '기억하고 있는 사실 중 "실험실 코드"를 찾아, 그 코드 문자열을 그대로 한 번만 적어줘. 다른 말은 하지 마.',
    )
    await expect(answerText(page, token)).toBeVisible({ timeout: 150_000 })
  } finally {
    // Left behind, each run adds a contradicting fact.
    await page.goto('/memory')
    await page.getByRole('button', { name: `${name} 삭제` }).click({ timeout: 15_000 })
    // Wait for the DELETE: the request is held for the undo window, and a global memory joins every later turn.
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/memory/') && r.request().method() === 'DELETE',
        { timeout: 20_000 },
      ),
      page.getByRole('dialog').getByRole('button', { name: '삭제' }).click(),
    ])
    await expect(page.getByRole('button', { name: `${name} 삭제` })).toHaveCount(0, {
      timeout: 10_000,
    })
  }
})

test('영업직 — 만든 에이전트로 대화하면 그 지침이 적용된다', async ({ page }) => {
  await page.goto('/agents')
  await page.getByRole('button', { name: /새 에이전트|에이전트 만들기/ }).first().click()
  const agentName = `제안 도우미 ${stamp()}`
  await page.getByLabel(/이름/).first().fill(agentName)
  // Spelled out with an example: a small model obeys a one-line rule only most of the time.
  await page.getByLabel(/시스템 프롬프트|프롬프트/).first().fill(
    '당신은 영업 제안 도우미입니다. 출력 형식 규칙: 모든 답변의 첫 글자는 반드시 ' +
      '"제안:" 이어야 합니다. 인사와 짧은 답에도 예외 없이 맨 앞에 붙입니다. ' +
      '예: "제안: 안녕하세요."',
  )
  await page.getByRole('button', { name: '저장', exact: true }).last().click()
  await page.waitForTimeout(1_000)

  try {
  await page.goto('/new/chat')
  // Exact: the sidebar's account button label contains an email address.
  await page.getByRole('button', { name: '@', exact: true }).click()
  await page.getByRole('menuitem', { name: agentName }).first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  // The agent badge proves the session committed with that agent attached.
  await expect(page.getByText(agentName).first()).toBeVisible({ timeout: 20_000 })

  await page.getByLabel('프롬프트 입력').fill('안녕하세요')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText(/제안:/).first()).toBeVisible({ timeout: 150_000 })
  } finally {
    // Removed even on failure: a leftover agent without a description fails `starter.spec.ts`.
    await page.goto('/agents')
    await page.getByRole('button', { name: `${agentName} 삭제` }).first().click()
    const confirm = page.getByRole('dialog')
    if (await confirm.isVisible().catch(() => false)) {
      await confirm.getByRole('button', { name: /^삭제$/ }).last().click().catch(() => {})
    }
    await expect(page.getByText(agentName)).toHaveCount(0, { timeout: 15_000 })
  }
})

test('학부생 — 어제 한 대화를 제목으로 다시 찾는다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, '삼투압이 무엇인지 한 문장으로 설명해줘.')
  await expect(page.getByText(/삼투/).first()).toBeVisible({ timeout: 150_000 })

  // A reload proves the sidebar entry is stored.
  await page.reload()
  await expect
    .poll(
      async () => {
        const labels = await page.getByRole('button').allInnerTexts()
        return labels.some((t) => t.includes('삼투'))
      },
      { timeout: 60_000, intervals: [2_000] },
    )
    .toBe(true)
})

interface ArtifactRow {
  id: string
  title?: string
  version?: number
  data?: Record<string, unknown>
}

/** Polls until an artifact of the kind matching `match` is stored; artifacts are written as the turn ends. */
async function waitForArtifact(
  page: Page,
  kind: string,
  match: (row: ArtifactRow) => boolean,
  timeout = 120_000,
): Promise<ArtifactRow> {
  const deadline = Date.now() + timeout
  let seen: ArtifactRow[] = []
  while (Date.now() < deadline) {
    seen = (await storedArtifacts(page, kind)) as ArtifactRow[]
    const found = seen.find(match)
    if (found) return found
    await page.waitForTimeout(2_000)
  }
  throw new Error(
    `${kind} 아티팩트가 저장되지 않았습니다. 최근 ${seen.length}건: ` +
      JSON.stringify(seen.map((row) => row.title)),
  )
}

test('기획직 — 대화에서 한 페이지 문서를 만들어 달라고 하면 결과물로 남는다', async ({ page }) => {
  test.setTimeout(300_000)
  const token = `PLAN-${stamp().toUpperCase()}`
  await page.goto('/new/chat')
  await useLocalModel(page)
  // The token identifies this request's artifact. "파일로 저장해서" makes the document
  // request explicit: `create_artifact` returns short text unless `userRequested`.
  await ask(
    page,
    `사내 보안 교육 안내 페이지를 한 페이지 HTML 문서로 만들어줘. 파일로 저장해서 ` +
      `사내 게시판에 올릴 거야. 제목, 소개, 주요 내용 3가지로 구성하고 ` +
      `본문 어딘가에 ${token} 를 그대로 넣어줘.`,
  )

  // The tool must have been called; an inline answer is not a pass.
  await expect(page.getByText(/아티팩트 (만드는 중|생성)/).first()).toBeVisible({
    timeout: 240_000,
  })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  const stored = await waitForArtifact(page, 'html', (row) =>
    String(row.data?.content ?? '').includes(token),
  )
  expect(String(stored.data?.content ?? '').toLowerCase()).toContain('<html')
  expect(String(stored.title ?? '').length).toBeGreaterThan(1)

  // Findable in the gallery.
  await page.goto('/artifacts')
  await expect(page.getByText(String(stored.title)).first()).toBeVisible({ timeout: 30_000 })
})

test('분석직 — 대화에서 준 숫자로 차트를 그리면 그 숫자가 결과물에 남는다', async ({ page }) => {
  test.setTimeout(300_000)
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(
    page,
    '분기별 가입자 수를 막대 차트로 그려줘. 1분기 120, 2분기 340, 3분기 510, 4분기 780.',
  )

  await expect(page.getByText(/차트 (그리는 중|그리기)/).first()).toBeVisible({ timeout: 240_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // The data table derives from the drawn points.
  const stored = await waitForArtifact(page, 'chart', (row) =>
    JSON.stringify(row.data ?? {}).includes('780'),
  )
  const source = JSON.stringify(stored.data ?? {})
  for (const value of ['120', '340', '510', '780']) {
    expect(source, `차트에 ${value} 가 없습니다`).toContain(value)
  }
})

test('개발직 — 스크립트를 만들어 달라고 하면 실행할 수 있는 코드 문서로 남는다', async ({
  page,
}) => {
  test.setTimeout(300_000)
  const token = `JOB-${stamp().toUpperCase()}`
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(
    page,
    `로그 파일을 날짜별로 묶어 압축하는 bash 스크립트를 파일로 만들어줘. ` +
      `스크립트 안 주석에 ${token} 를 그대로 남겨줘.`,
  )
  await expect(page.getByText(/아티팩트 (만드는 중|생성)/).first()).toBeVisible({
    timeout: 240_000,
  })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // `code`, not `html`: an html preview would try to render the source.
  const stored = await waitForArtifact(page, 'code', (row) =>
    String(row.data?.content ?? '').includes(token),
  )
  const content = String(stored.data?.content ?? '')
  expect(content, '스크립트가 아니라 설명이 저장됐습니다').toMatch(/#!|for |tar |gzip|zip/i)
  // The language sets highlighting and the export extension.
  expect(String(stored.data?.language ?? '')).not.toBe('')
})

test('사무직 — 짧은 메일 초안은 문서가 아니라 답변으로 온다', async ({ page }) => {
  test.setTimeout(300_000)
  // `create_artifact` returns short text instead of storing it. Counted across every kind.
  const before = await artifactIds(page)
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, '내일 회의가 30분 미뤄졌다고 알리는 짧은 메일 초안 세 문장만 써줘.')
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // The draft is in the answer itself.
  await expect(answerText(page, /회의|미뤄|연기|30분/).first()).toBeVisible({ timeout: 60_000 })
  const after = await artifactIds(page)
  expect(
    after.filter((id) => !before.includes(id)),
    '짧은 메일 초안이 문서로 만들어졌습니다',
  ).toEqual([])
})
