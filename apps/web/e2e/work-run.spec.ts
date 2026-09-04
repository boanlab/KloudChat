import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { approvePlan, signInAs } from './helpers'
import { workScenarios, type WorkScenario } from './work-scenario-catalog'

/** Runs a slice of the work scenario catalogue end to end and reads what came out.
 *  Slice: `WORK_COUNT` rows, chosen by coverage or by `WORK_FROM`/`WORK_STRIDE`; `WORK_SHARD`/`WORK_SHARDS`
 *  split rows across accounts. Findings are written to `work-findings/*.json`. */

const ACCOUNTS = {
  user: { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' },
  admin: { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' },
}

/** One seeded account per shard: workers sharing an account see each other's 기록 and 아티팩트. */
const SHARD_ACCOUNTS = [
  { email: 'e2e-user-mtiyua51@example.com', password: 'another-long-password' },
  { email: 'e2e-user-mtiuih2t@example.com', password: 'another-long-password' },
  { email: 'e2e-user-mthk8dpa@example.com', password: 'another-long-password' },
  { email: 'e2e-user-mthhxh56@example.com', password: 'another-long-password' },
]

/** Which account this invocation works as. */
function whoAmI(): { email: string; password: string } {
  const shard = Number(process.env.WORK_SHARD || 0)
  if (!shard) return ACCOUNTS.user
  const picked = SHARD_ACCOUNTS[(shard - 1) % SHARD_ACCOUNTS.length]
  return picked ?? ACCOUNTS.user
}

const OUT = 'work-findings'

/** Rows per invocation. */
const COUNT = Number(process.env.WORK_COUNT || 12)
/** Start index; only meaningful with `WORK_STRIDE`. */
const FROM = Number(process.env.WORK_FROM || 0)
const STRIDE = process.env.WORK_STRIDE === '0' ? 0 : Number(process.env.WORK_STRIDE || 0)

/** Scenario ids an earlier run already reported on. Reads without an existence check: shards write concurrently. */
function alreadyRun(): Set<string> {
  const done = new Set<string>()
  let names: string[]
  try {
    names = fs.readdirSync(OUT)
  } catch {
    // Nothing has run yet.
    return done
  }
  for (const name of names) {
    if (!name.startsWith('run-') || !name.endsWith('.json')) continue
    try {
      for (const row of JSON.parse(fs.readFileSync(path.join(OUT, name), 'utf8')) as Finding[]) {
        done.add(row.id)
      }
    } catch {
      // A run killed mid-write leaves a half file.
    }
  }
  return done
}

/** Picks this run's rows: unplayed rows first, ranked by how thinly their persona/job/evidence/follow-up
 *  have been covered; or a fixed stride when `WORK_STRIDE` is set. */
function slice(): WorkScenario[] {
  const done = alreadyRun()
  const shard = Number(process.env.WORK_SHARD || 0)
  const shards = Number(process.env.WORK_SHARDS || 0)
  const fresh = workScenarios
    .filter((row) => !done.has(row.id))
    // Each shard takes every nth row, so two shards never pick the same scenario.
    .filter((_, index) => !shards || index % shards === (shard - 1 + shards) % shards)
  // Everything has run once: start over.
  const pool = fresh.length ? fresh : workScenarios

  if (STRIDE > 0) {
    const picked: WorkScenario[] = []
    for (let i = FROM; picked.length < COUNT && i < pool.length; i += STRIDE) {
      picked.push(pool[i])
    }
    return picked
  }

  // Times each column value has been played.
  const seen = { persona: new Map<string, number>(), work: new Map<string, number>(), evidence: new Map<string, number>(), followUp: new Map<string, number>() }
  const bump = (map: Map<string, number>, key: string) => map.set(key, (map.get(key) ?? 0) + 1)
  for (const id of done) {
    const [persona, work, evidence, followUp] = id.split('.')
    bump(seen.persona, persona)
    bump(seen.work, work)
    bump(seen.evidence, evidence)
    bump(seen.followUp, followUp)
  }
  const thinness = (row: WorkScenario) =>
    (seen.persona.get(row.personaId) ?? 0) +
    (seen.work.get(row.workId) ?? 0) +
    (seen.evidence.get(row.evidenceId) ?? 0) +
    (seen.followUp.get(row.followUpId) ?? 0)

  const picked: WorkScenario[] = []
  const takenPersona = new Set<string>()
  const takenWork = new Set<string>()
  const ranked = [...pool].sort((a, b) => thinness(a) - thinness(b))
  // First pass: distinct persona and job per row; then whatever is thinnest.
  for (const row of ranked) {
    if (picked.length >= COUNT) break
    if (takenPersona.has(row.personaId) || takenWork.has(row.workId)) continue
    picked.push(row)
    takenPersona.add(row.personaId)
    takenWork.add(row.workId)
  }
  for (const row of ranked) {
    if (picked.length >= COUNT) break
    if (picked.includes(row)) continue
    picked.push(row)
  }
  return picked
}

interface Finding {
  id: string
  persona: string
  work: string
  surface: string
  evidence: string
  followUp: string
  /** `ok` when the work was produced and read back; otherwise what went wrong. */
  verdict:
    | 'ok'
    | 'empty'
    | 'off-topic'
    | 'missing-subject'
    | 'no-artifact'
    | 'follow-up'
    | 'stalled'
    | 'error'
  detail: string
  /** Seconds the whole row took. */
  seconds: number
  /** The first 400 characters of what the person would read. */
  sample: string
}

/** Subject words of the evidence file; part of the request when the evidence is an attachment. */
const EVIDENCE_SUBJECT = '교육 비용 단가 기간 이수 인원 내부 전담팀 외부 위탁'

/** The evidence file a row attaches. Written unconditionally: shards share the path and the bytes never change. */
function evidenceFile(): string {
  const dir = path.join(OUT, 'fixtures')
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, 'evidence.csv')
  fs.writeFileSync(
    file,
    [
      '항목,2024,2025,2026',
      '내부 전담팀 단가(만원),1800,1600,1400',
      '외부 위탁 단가(만원),1500,1800,2100',
      '교육 기간(주),20,16,12',
      '이수 인원(명),42,68,95',
    ].join('\n'),
    'utf8',
  )
  return file
}

/** Types the request and waits the turn out. Returns false if it never ran. */
async function send(page: Page, text: string, timeout: number): Promise<boolean> {
  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toBeVisible({ timeout: 30_000 })
  await box.click()
  await box.fill(text)
  await page.keyboard.press('Enter')
  const stop = page.getByLabel('중지')
  const started = await stop
    .waitFor({ state: 'visible', timeout: 45_000 })
    .then(() => true)
    .catch(() => false)
  if (!started) return false
  await stop.waitFor({ state: 'hidden', timeout }).catch(() => undefined)
  await page.waitForTimeout(1_000)
  return true
}

/** The produced work: the artifact panel for document surfaces, else the transcript with the request cut out. */
async function readWork(page: Page, surface: string, request: string): Promise<string> {
  if (surface === 'report' || surface === 'slides') {
    const panel = page.locator('[data-panel="artifact"]')
    if (await panel.isVisible().catch(() => false)) {
      return (await panel.innerText().catch(() => '')) || ''
    }
    return ''
  }
  const all = (await page.locator('main').innerText().catch(() => '')) || ''
  // Every echo of the request, including the composer's copy.
  return all.split(request).join(' ')
}

/** Title of the document open in the panel. */
async function panelTitle(page: Page): Promise<string> {
  const panel = page.locator('[data-panel="artifact"]')
  if (!(await panel.isVisible().catch(() => false))) return ''
  const text = (await panel.innerText().catch(() => '')) || ''
  return text.split('\n')[0]?.trim() ?? ''
}

/** Words too common to tell two texts apart. */
const NOISE = new Set([
  '주세요', '만들어', '해주세요', '합니다', '입니다', '있는', '위한', '대한', '그리고',
  '내용', '자료', '정리', '설명', '보고서', '발표자료', '슬라이드', '문서', '표로',
  '각각', '함께', '순서로', '중심으로', '기준으로', '경우', '것을', '것이', '한다',
])
function contentWords(text: string): Set<string> {
  const words = text
    .toLowerCase()
    .split(/[^0-9A-Za-z가-힣]+/)
    .filter((w) => w.length >= 2 && !NOISE.has(w))
  // Also index the stem without a trailing particle: 「미시사가」 → 「미시사」.
  const out = new Set<string>()
  for (const word of words) {
    out.add(word)
    if (/^[가-힣]+$/.test(word) && word.length >= 3) out.add(word.slice(0, word.length - 1))
  }
  return out
}

/** Whether the title is about the request at all. */
function shares(title: string, request: string): boolean {
  const asked = contentWords(request)
  for (const word of contentWords(title)) {
    if (asked.has(word)) return true
    // Substring of three or more characters counts as the same word.
    for (const other of asked) {
      if (word.length >= 3 && other.includes(word)) return true
      if (other.length >= 3 && word.includes(other)) return true
    }
  }
  return false
}

/** Performs the row's follow-up (수정 · 내보내기 · 공유 · 이어하기). Returns '' on success, else what went wrong. */
async function followUp(page: Page, scenario: WorkScenario): Promise<string> {
  const documentSurface = scenario.surface === 'report' || scenario.surface === 'slides'

  if (scenario.followUpId === 'export') {
    if (!documentSurface) return ''
    // 내보내기 lives in the 파일 tab; the format is a menu under it.
    const fileTab = page.getByRole('tab', { name: '파일' }).first()
    if (await fileTab.isVisible().catch(() => false)) {
      await fileTab.click()
      await page.waitForTimeout(600)
    }
    const menu = page.getByRole('button', { name: '내보내기' }).first()
    if (!(await menu.isVisible().catch(() => false))) return '내보내기 버튼이 없습니다'
    await menu.click()
    await page.waitForTimeout(900)
    const format = page
      .getByRole('menuitem', { name: /PDF|Word 문서|한글 문서|PowerPoint|발표 파일/ })
      .first()
    if (!(await format.isVisible().catch(() => false))) {
      await page.keyboard.press('Escape')
      return '내보낼 형식이 하나도 없습니다'
    }
    const download = page.waitForEvent('download', { timeout: 180_000 }).catch(() => null)
    await format.click()
    const file = await download
    await page.keyboard.press('Escape')
    if (!file) return '내보내기를 눌렀지만 파일이 오지 않았습니다'
    if (!file.suggestedFilename()) return '받은 파일에 이름이 없습니다'
    return ''
  }

  if (scenario.followUpId === 'share') {
    const share = page.getByRole('button', { name: '공유' }).first()
    if (!(await share.isVisible().catch(() => false))) return '공유 버튼이 없습니다'
    await share.click()
    const dialog = page.getByRole('dialog')
    if (!(await dialog.isVisible({ timeout: 15_000 }).catch(() => false))) {
      return '공유 창이 열리지 않았습니다'
    }

    // A conversation never shared has no link until 링크 만들기 is pressed.
    const make = dialog.getByRole('button', { name: '링크 만들기' })
    if (await make.isVisible().catch(() => false)) {
      await make.click()
    }

    // The field is drawn empty until the row comes back; wait for a value.
    let link = ''
    for (let waited = 0; waited < 40_000; waited += 500) {
      link = await page
        .getByLabel('공유 링크')
        .first()
        .inputValue()
        .catch(() => '')
      if (/\/share\/[A-Za-z0-9_-]+/.test(link)) break
      await page.waitForTimeout(500)
    }
    await page.keyboard.press('Escape')
    if (!/\/share\/[A-Za-z0-9_-]+/.test(link)) {
      // Shared-but-unreadable link and nothing-happened are different defects.
      const badge = await page
        .getByText('공유 중')
        .first()
        .isVisible()
        .catch(() => false)
      return badge
        ? '공유는 되었는데 링크 주소를 읽지 못했습니다'
        : `공유 링크가 만들어지지 않았습니다: ${link}`
    }
    return ''
  }

  if (scenario.followUpId === 'resume') {
    const here = page.url()
    await page.goto('/history')
    await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
    // Rows with a timestamp only; a bare /전/ also matches 「보이는 항목 전체 선택」.
    const first = page
      .locator('main button')
      .filter({ hasText: /\d+\s*(분|시간|일|주|개월)\s*전|방금/ })
      .first()
    if (!(await first.isVisible().catch(() => false))) return '기록에 대화가 없습니다'
    await first.click()
    await page.waitForTimeout(1_500)
    if (!/\/s\/[0-9a-f]{32}/.test(page.url())) return '기록에서 대화가 열리지 않았습니다'
    await page.goto(here)
    return ''
  }

  // revise
  if (documentSurface) {
    const edit = page.getByRole('button', { name: /장 편집|절 편집|내용 편집|문서 수정/ }).first()
    if (!(await edit.isVisible().catch(() => false))) return '고칠 방법이 없습니다'
    await edit.click()
    await page.waitForTimeout(1_500)
    await page.keyboard.press('Escape')
  }
  return ''
}

async function runOne(page: Page, scenario: WorkScenario): Promise<Finding> {
  const began = Date.now()
  const base: Omit<Finding, 'verdict' | 'detail' | 'seconds' | 'sample'> = {
    id: scenario.id,
    persona: scenario.persona,
    work: scenario.work,
    surface: scenario.surface,
    evidence: scenario.evidence,
    followUp: scenario.followUp,
  }
  const done = (verdict: Finding['verdict'], detail: string, sample = ''): Finding => ({
    ...base,
    verdict,
    detail,
    seconds: Math.round((Date.now() - began) / 1000),
    sample: sample.replace(/\s+/g, ' ').slice(0, 400),
  })

  try {
    await page.goto(`/new/${scenario.surface}`)
    const composer = page.getByLabel('프롬프트 입력')
    const off = page.getByText(/기능이 꺼져 있습니다/)
    await expect(composer.or(off).first()).toBeVisible({ timeout: 25_000 })
    if (!(await composer.count())) return done('ok', '이 워크스페이스에서 꺼진 화면 — 건너뜀')

    if (scenario.evidenceId === 'attachment') {
      const attach = page.getByRole('button', { name: '첨부' })
      if (await attach.isVisible().catch(() => false)) {
        const chooser = page.waitForEvent('filechooser', { timeout: 10_000 }).catch(() => null)
        await attach.click()
        const picked = await chooser
        if (picked) await picked.setFiles(evidenceFile())
        await page.waitForTimeout(2_500)
      }
    }
    if (scenario.evidenceId === 'web') {
      const web = page.getByRole('button', { name: '웹 검색' })
      if (await web.isVisible().catch(() => false)) await web.click()
    }

    const ran = await send(page, scenario.prompt, 300_000)
    if (!ran) return done('stalled', '전송했지만 턴이 시작되지 않았습니다')

    // 보고서 and 슬라이드 store nothing until the plan is approved.
    if (scenario.surface === 'report' || scenario.surface === 'slides') {
      await approvePlan(page, 420_000).catch(() => undefined)
      await page
        .getByLabel('중지')
        .waitFor({ state: 'hidden', timeout: 420_000 })
        .catch(() => undefined)
      await page.waitForTimeout(1_500)
    }

    const text = await readWork(page, scenario.surface, scenario.prompt)

    if (scenario.surface === 'report' || scenario.surface === 'slides') {
      const panel = page.locator('[data-panel="artifact"]')
      if (!(await panel.isVisible().catch(() => false))) {
        return done('no-artifact', '문서 패널이 열리지 않았습니다', text)
      }
      // A panel left open from the previous conversation would pass every check below.
      const title = await panelTitle(page)
      const asked =
        scenario.evidenceId === 'attachment'
          ? `${scenario.prompt} ${EVIDENCE_SUBJECT}`
          : scenario.prompt
      if (title && !shares(title, asked)) {
        return done(
          'off-topic',
          `요청과 무관한 제목: 「${title}」`,
          text,
        )
      }
    }

    if (text.trim().length < 80) return done('empty', '산출물에 읽을 것이 없습니다', text)

    // The request's subject word must be in the answer.
    const lower = text.toLowerCase()
    const missed = scenario.expect.filter((word) => !lower.includes(word.toLowerCase()))
    if (scenario.expect.length && missed.length === scenario.expect.length) {
      return done('missing-subject', `요청의 주제어가 없습니다: ${missed.join(', ')}`, text)
    }

    const wrong = await followUp(page, scenario)
    if (wrong) return done('follow-up', `${scenario.followUp}: ${wrong}`, text)

    return done('ok', '', text)
  } catch (err) {
    return done('error', String(err).slice(0, 300))
  }
}

test('업무 시나리오를 실제로 수행하고 결과물을 읽는다', async ({ page }) => {
  const chosen = slice()
  test.setTimeout(chosen.length * 480_000 + 120_000)
  fs.mkdirSync(OUT, { recursive: true })

  const who = whoAmI()
  console.log(`계정: ${who.email}`)
  await signInAs(page, who.email, who.password)

  // Written after every row, so an interrupted run keeps its findings.
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const shard = process.env.WORK_SHARD ? `-s${process.env.WORK_SHARD}` : ''
  const log = path.join(OUT, `run-${stamp}${shard}.json`)

  const findings: Finding[] = []
  for (const [n, scenario] of chosen.entries()) {
    const finding = await runOne(page, scenario)
    // Screenshot every non-ok verdict.
    if (finding.verdict !== 'ok') {
      await page
        .screenshot({ path: path.join(OUT, `${finding.verdict}-${scenario.id}.png`) })
        .catch(() => undefined)
    }
    findings.push(finding)
    fs.writeFileSync(log, JSON.stringify(findings, null, 2), 'utf8')
    const mark = finding.verdict === 'ok' ? '·' : '✗'
    console.log(
      `${mark} [${n + 1}/${chosen.length}] ${finding.id}  (${finding.seconds}초)` +
        (finding.verdict === 'ok' ? '' : `  << ${finding.verdict}: ${finding.detail}`),
    )
  }

  const bad = findings.filter((f) => f.verdict !== 'ok')
  console.log(`\n===== ${findings.length}건 중 문제 ${bad.length}건 =====`)
  for (const f of bad) {
    console.log(`✗ ${f.id} [${f.surface}/${f.evidence}] ${f.verdict}: ${f.detail}`)
    if (f.sample) console.log(`    화면: ${f.sample.slice(0, 200)}`)
  }
  const slow = findings.filter((f) => f.seconds > 240)
  if (slow.length) {
    console.log(`\n느린 시나리오 ${slow.length}건: ${slow.map((f) => `${f.id}(${f.seconds}초)`).join(', ')}`)
  }

  // Only app failures fail the suite; `off-topic` and `missing-subject` are the model's judgement.
  const broken = findings.filter((f) =>
    ['empty', 'no-artifact', 'follow-up', 'stalled', 'error'].includes(f.verdict),
  )
  expect(broken.map((f) => `${f.id}: ${f.verdict} ${f.detail}`), '앱이 결과물을 내놓지 못한 시나리오').toEqual([])
})
