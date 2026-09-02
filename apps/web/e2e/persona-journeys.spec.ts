/**
 * Persona journeys against a real backend.
 *
 * Where `personas.spec.ts` asks whether a capability is on the screen, this
 * asks whether one person can get their job done end to end: create a project,
 * attach material, get an answer that used it, and find it again the next day.
 *
 * Slow, because every assertion costs a model call or a database write.
 *
 * Requires the API running, LiteLLM connected, and one run of
 * `bash scripts/e2e-seed.sh`.
 * Run: npm run test:journeys
 */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { answerText, artifactIds, pickToolModel, signIn, storedArtifacts } from './helpers'

/**
 * Retried, and only here.
 *
 * What this file asserts is what a model wrote — that an instruction was
 * obeyed, that a figure came back out of an uploaded file. A small model does
 * both most of the time and not every time: run back to back, this suite has
 * failed on retrieval and passed four seconds later with nothing changed. A
 * single attempt therefore reports the model's mood as if it were the
 * product's behaviour.
 *
 * Deliberately not applied to the UI and infrastructure suites. A control that
 * is missing is missing on the second attempt too, and a retry there would only
 * buy a slower red — or, worse, hide a real intermittent fault.
 */
test.describe.configure({ retries: 2 })


test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const stamp = () => Math.random().toString(36).slice(2, 7)

/** Local model: free, and the suite must not depend on an external provider. */
const useLocalModel = pickToolModel

/**
 * Sends from `/new/chat`. The navigation only happens once the server has
 * created the session, so the send has to be awaited rather than assumed.
 */
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

/* ── graduate student: project instructions and knowledge files really do
      reach the answer ─────────────────────────────────────────────────── */

test('대학원생 — 프로젝트 지식을 올리고 그 안에서만 아는 값을 답하게 한다', async ({ page }) => {
  const token = `RUN-${stamp().toUpperCase()}`
  // Spelled out with an example and the exceptions named. A one-line rule
  // ("답변은 반드시 [연구] 로 시작한다") reaches the model intact but a small
  // one obeys it about two turns in three — so a single assertion on it is a
  // coin flip, and what fails is the suite rather than the product.
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

  // A chat started from inside the project inherits its instructions and files.
  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  await useLocalModel(page)
  // "Copy it" rather than "paraphrase it": a small model asked to summarise
  // rounds the figure and the assertion then tests its mood, not retrieval.
  //
  // And nothing that reads as "only the number" — the project's standing
  // instruction asks for a "[연구]" prefix, and a prompt that forbids anything
  // besides the figure makes the two assertions below contradict each other.
  await page
    .getByLabel('프롬프트 입력')
    .fill(`지식 파일에서 ${token} 행의 macro_f1 값이 얼마야? 파일에 적힌 숫자를 그대로 옮겨 적어줘.`)
  await page.getByLabel('프롬프트 입력').press('Enter')

  // The number exists nowhere but the uploaded file, and that is the whole of
  // what this checks. `0.914` rather than `0.9142`: a small model asked for a
  // figure sometimes hands back `0.914` or `91.4%`, and the last digit is its
  // arithmetic rather than the product's retrieval — the file was read either
  // way, which is the claim. Nothing shorter would do: `0.9` is a number the
  // model could have invented.
  await expect(answerText(page, /0[.,]914|91[.,]4\s*%/)).toBeVisible({ timeout: 150_000 })
  // And the project's standing instruction survived into the same turn.
  await expect(answerText(page, '[연구]')).toBeVisible({ timeout: 30_000 })
})

/* ── developer: code execution actually runs and changes the answer ──── */

test('개발직 — 계산을 암산이 아니라 코드로 검증한다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, 'execute_code 로 2**31 - 1 을 계산해서 숫자만 알려줘.')

  // Thousands separators are the model's choice, not the product's: the same run
  // answers 2147483647 or 2,147,483,647 and both are correct. Pinning the bare
  // digits failed roughly one run in eight against a working code execution;
  // the sibling office-worker test already allows the commas.
  await expect(answerText(page, /2[,\s]?147[,\s]?483[,\s]?647/)).toBeVisible({ timeout: 150_000 })
  // The step timeline is how a user knows the number came from a run, not a guess.
  await expect(page.getByText(/코드 실행/).first()).toBeVisible({ timeout: 30_000 })
})

/* ── office worker: reads an attached table and answers from it ──────── */

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

/* ── researcher: a memory survives into the next conversation ────────── */

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
    // A brand-new conversation — nothing carried over but the memory itself.
    await page.goto('/new/chat')
    await useLocalModel(page)
    // A random token is the only way to tell recall from prior knowledge, but a
    // small local model needs to be told plainly to copy it rather than answer.
    await ask(
      page,
      '기억하고 있는 사실 중 "실험실 코드"를 찾아, 그 코드 문자열을 그대로 한 번만 적어줘. 다른 말은 하지 마.',
    )
    await expect(answerText(page, token)).toBeVisible({ timeout: 150_000 })
  } finally {
        // Left behind, each run adds a contradicting fact and the model ends
        // up choosing between them. Deleted by name rather than by walking the
        // card DOM.
    await page.goto('/memory')
    await page.getByRole('button', { name: `${name} 삭제` }).click({ timeout: 15_000 })
    // The row leaves the screen at once and the request is held for the undo
    // window, so waiting on the card only proves the screen. Waited on the
    // response instead: a context closed inside that window leaves the memory
    // on the instance, and a memory is `global` scope — it joins every later
    // turn's prompt.
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

/* ── sales rep: the agent persona holds for the whole conversation ───── */

test('영업직 — 만든 에이전트로 대화하면 그 지침이 적용된다', async ({ page }) => {
  await page.goto('/agents')
  await page.getByRole('button', { name: /새 에이전트|에이전트 만들기/ }).first().click()
  const agentName = `제안 도우미 ${stamp()}`
  await page.getByLabel(/이름/).first().fill(agentName)
  // Spelled out with an example and the exceptions named. A one-line rule
  // reaches the model intact but a small one obeys it about two turns in
  // three, which makes a single assertion on it a coin flip.
  await page.getByLabel(/시스템 프롬프트|프롬프트/).first().fill(
    '당신은 영업 제안 도우미입니다. 출력 형식 규칙: 모든 답변의 첫 글자는 반드시 ' +
      '"제안:" 이어야 합니다. 인사와 짧은 답에도 예외 없이 맨 앞에 붙입니다. ' +
      '예: "제안: 안녕하세요."',
  )
  await page.getByRole('button', { name: '저장', exact: true }).last().click()
  await page.waitForTimeout(1_000)

  try {
  await page.goto('/new/chat')
  // Choosing an agent starts a session with it; the prompt applies to every turn.
  // Substring matching would also hit the sidebar's account button, whose label
  // contains an email address.
  await page.getByRole('button', { name: '@', exact: true }).click()
  await page.getByRole('menuitem', { name: agentName }).first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
    // `navigate()` changes the URL immediately but React commits a tick
    // later, and typing in that window lands in the previous composer.
    //
    // The agent badge is the anchor: it proves both that the session committed
    // and that **it committed with that agent attached**.
  await expect(page.getByText(agentName).first()).toBeVisible({ timeout: 20_000 })

  await page.getByLabel('프롬프트 입력').fill('안녕하세요')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText(/제안:/).first()).toBeVisible({ timeout: 150_000 })
  } finally {
    // Removed even when the assertion above fails. Left behind, an agent with
    // no description is what `starter.spec.ts` reports as a broken starter set
    // — one flake then fails a test that has nothing to do with it.
    await page.goto('/agents')
    await page.getByRole('button', { name: `${agentName} 삭제` }).first().click()
    const confirm = page.getByRole('dialog')
    if (await confirm.isVisible().catch(() => false)) {
      await confirm.getByRole('button', { name: /^삭제$/ }).last().click().catch(() => {})
    }
    await expect(page.getByText(agentName)).toHaveCount(0, { timeout: 15_000 })
  }
})

/* ── undergraduate: conversations accumulate in the sidebar and can be
      found again tomorrow ──────────────────────────────────────────────── */

test('학부생 — 어제 한 대화를 제목으로 다시 찾는다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, '삼투압이 무엇인지 한 문장으로 설명해줘.')
  await expect(page.getByText(/삼투/).first()).toBeVisible({ timeout: 150_000 })

  // Reload proves the sidebar entry came from Postgres, and the generated title
  // is what makes it findable at all.
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

/* ── the artifacts a conversation itself produces ────────────────────────
      챗은 답만 하지 않는다. 문서를 만들어 달라거나 수치를 그려 달라고 하면
      도구를 불러 결과물을 만들고, 그것은 대화가 끝나도 남는 것이어야 한다.
      아래 셋이 재는 것은 화면에 무엇이 그려졌는지가 아니라 **저장되었는지**
      다 — 패널에 떴다가 다음 로그인에 없는 문서는 테두리를 두른 메시지일
      뿐이고, 그 차이는 화면만 봐서는 알 수 없다. */

/**
 * 만들어진 아티팩트가 서버에 남을 때까지 기다린다.
 *
 * 도구가 끝난 것과 아티팩트가 저장된 것은 같은 순간이 아니다 — 턴이 끝나며
 * 기록되므로, 스트림이 멎자마자 목록을 읽으면 아직 비어 있다. 폴링하는 쪽이
 * 고정 대기보다 빠르고, 무엇을 기다렸는지도 실패 메시지에 남는다.
 */
interface ArtifactRow {
  id: string
  title?: string
  version?: number
  data?: Record<string, unknown>
}

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
  // 토큰을 본문에 넣게 한다. 아티팩트가 **이 요청에서** 나온 것인지 아니면
  // 계정에 이미 있던 문서인지, 제목만으로는 구별되지 않는다.
  //
  // "파일로 저장해서 쓸 것" 이 프롬프트에 들어가는 이유는 문체가 아니라
  // `create_artifact` 의 규칙이다. 짧은 글은 문서로 만들지 않고 되돌려 보내며,
  // 그 관문을 여는 것은 사용자가 문서를 직접 요구했다는 사실(`userRequested`)
  // 하나다. 사람이 애매하게 말하면 작은 모델은 그 판단을 놓치고 본문으로
  // 답해 버린다 — 한 번 그렇게 흔들렸고, 이 문장이 그 흔들림을 줄인다.
  await ask(
    page,
    `사내 보안 교육 안내 페이지를 한 페이지 HTML 문서로 만들어줘. 파일로 저장해서 ` +
      `사내 게시판에 올릴 거야. 제목, 소개, 주요 내용 3가지로 구성하고 ` +
      `본문 어딘가에 ${token} 를 그대로 넣어줘.`,
  )

  // 결정의 흔적. 이 줄이 없으면 모델이 본문으로 답해도 통과한다 — 그건 이
  // 시나리오가 재려는 것이 아니다.
  await expect(page.getByText(/아티팩트 (만드는 중|생성)/).first()).toBeVisible({
    timeout: 240_000,
  })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  const stored = await waitForArtifact(page, 'html', (row) =>
    String(row.data?.content ?? '').includes(token),
  )
  expect(String(stored.data?.content ?? '').toLowerCase()).toContain('<html')
  expect(String(stored.title ?? '').length).toBeGreaterThan(1)

  // 그리고 갤러리에서 다시 찾을 수 있다. 저장은 됐는데 화면에서 못 찾는
  // 결과물은 만들어 준 적 없는 것과 같다.
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

  // 표는 그린 점에서 파생된다 — 그림과 표가 서로 다른 말을 할 수 없다는 것이
  // `create_chart` 의 설계이고, 여기서 그 약속을 확인한다.
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

  // `html` 이 아니라 `code` 다. 둘은 패널에서 다르게 그려지고 다르게 내보내지므로,
  // 스크립트가 html 로 저장되면 미리보기가 소스를 렌더링하려 든다.
  const stored = await waitForArtifact(page, 'code', (row) =>
    String(row.data?.content ?? '').includes(token),
  )
  const content = String(stored.data?.content ?? '')
  expect(content, '스크립트가 아니라 설명이 저장됐습니다').toMatch(/#!|for |tar |gzip|zip/i)
  // 언어가 붙어야 패널이 색을 입히고 내보낼 때 확장자가 정해진다.
  expect(String(stored.data?.language ?? '')).not.toBe('')
})

test('사무직 — 짧은 메일 초안은 문서가 아니라 답변으로 온다', async ({ page }) => {
  test.setTimeout(300_000)
  // 이 서식의 약속 하나를 지킨다. 세 문장짜리 메일을 패널에 넣으면 읽으려고
  // 패널을 여는 수고만 늘고, `create_artifact` 는 그래서 짧은 글을 되돌려
  // 보낸다 — 그 규칙이 풀리면 대화가 문서 공장이 된다.
  // 종류를 가리지 않고 센다. 처음에는 `html` 만 셌는데, 실제로 만들어진
  // 메일 초안은 `code` 였다 — 시험은 초록이었고 패널에는 문서가 앉아 있었다.
  const before = await artifactIds(page)
  await page.goto('/new/chat')
  await useLocalModel(page)
  await ask(page, '내일 회의가 30분 미뤄졌다고 알리는 짧은 메일 초안 세 문장만 써줘.')
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // 본문이 대화 안에 있다. 이것이 없으면 "문서로 만들지 않았다" 는 그냥
  // 아무 답도 하지 않은 것이다.
  await expect(answerText(page, /회의|미뤄|연기|30분/).first()).toBeVisible({ timeout: 60_000 })
  const after = await artifactIds(page)
  expect(
    after.filter((id) => !before.includes(id)),
    '짧은 메일 초안이 문서로 만들어졌습니다',
  ).toEqual([])
})
