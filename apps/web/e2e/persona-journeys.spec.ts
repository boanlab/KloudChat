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
import { answerText, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const stamp = () => Math.random().toString(36).slice(2, 7)

/** Local model: free, and the suite must not depend on an external provider. */
async function useLocalModel(page: Page) {
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
}

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

  // The number exists nowhere but the uploaded file.
  await expect(answerText(page, '0.9142')).toBeVisible({ timeout: 150_000 })
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
