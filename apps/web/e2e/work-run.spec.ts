import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { approvePlan, signInAs } from './helpers'
import { workScenarios, type WorkScenario } from './work-scenario-catalog'

/**
 * The catalogue, actually run.
 *
 * `work-scenario-catalog.spec.ts` proves the 1,152 rows exist and that each
 * one names a surface somebody can reach. That is the map. This is walking it:
 * every row here signs in, brings its evidence, types the request, presses the
 * document surfaces through their plan, and then *reads what came out* —
 * because the failure this exists to catch is not "no bytes arrived", it is a
 * report whose sections are empty, a deck of eight identical slides, an export
 * that opens to a different document than the screen showed.
 *
 * **Sliced, because a turn is a minute.** Running all 1,152 in one go is a day
 * of wall clock. `WORK_FROM` / `WORK_COUNT` take a window, and `WORK_STRIDE`
 * walks the catalogue by a step instead of a block — a block of 24 is one
 * persona doing one job four ways, while a stride spreads the same 24 across
 * every persona, job, evidence and follow-up there is. The default is a
 * stride, so a short run is a wide run.
 *
 * Findings go to `work-findings/*.json` as well as to the console: a defect
 * found on row 900 has to survive the run that found it.
 */

const ACCOUNTS = {
  user: { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' },
  admin: { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' },
}

/**
 * 한 배치를 여럿이 나눠 든다.
 *
 * A row is two and a half minutes — a document surface plans, waits to be
 * approved, then writes a section at a time — and 1,152 of them in one worker
 * is two days of wall clock. The catalogue is only useful if it can actually
 * be walked.
 *
 * Workers cannot share an account: two of them signing in as the same person
 * see each other's conversations in 기록, each other's rows in 아티팩트, and
 * the follow-up that opens 「the most recent conversation」 opens somebody
 * else's. So a shard is an account, and Playwright's own `--shard` splits the
 * work between them.
 *
 * These are the seeded e2e accounts, all active with a real allowance. They
 * are not the two the audits use, because those two are the accounts a person
 * is looking at while this runs.
 */
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

/** How many rows this invocation takes, and which. */
const COUNT = Number(process.env.WORK_COUNT || 12)
//: Only meaningful with `WORK_STRIDE`; the default selection is by coverage,
//: which has no cursor to start from.
const FROM = Number(process.env.WORK_FROM || 0)
const STRIDE = process.env.WORK_STRIDE === '0' ? 0 : Number(process.env.WORK_STRIDE || 0)

/**
 * Every scenario id an earlier run already reported on.
 *
 * Asked for rather than checked for. Four shards read and write this directory
 * at once, so a `existsSync` before a `readdirSync` is a claim that has expired
 * by the time it is used — and the only thing it can tell us is what the read
 * itself says a moment later.
 */
function alreadyRun(): Set<string> {
  const done = new Set<string>()
  let names: string[]
  try {
    names = fs.readdirSync(OUT)
  } catch {
    // Nothing has run yet, or nothing is readable. Both mean the same here.
    return done
  }
  for (const name of names) {
    if (!name.startsWith('run-') || !name.endsWith('.json')) continue
    try {
      for (const row of JSON.parse(fs.readFileSync(path.join(OUT, name), 'utf8')) as Finding[]) {
        done.add(row.id)
      }
    } catch {
      // A run killed mid-write leaves a half file. It is a log, not state.
    }
  }
  return done
}

/**
 * 어느 열을 아직 건드리지 않았는지 보고 고른다.
 *
 * 1,152 rows at a minute apiece is a day of wall clock, so a run is always a
 * sample — and which sample decides what the run can find. Taking a block is
 * one persona doing one job four ways; taking every nth row spreads evenly but
 * repeats the same combinations run after run, because nothing remembers.
 *
 * So earlier runs are read back and their rows skipped, and what is left is
 * ordered by how thin its four columns are: a persona nobody has played, a job
 * nobody has asked for, evidence nobody has brought. Twelve rows a run, and
 * the fourth run is walking ground the first three did not.
 */
function slice(): WorkScenario[] {
  const done = alreadyRun()
  const shard = Number(process.env.WORK_SHARD || 0)
  const shards = Number(process.env.WORK_SHARDS || 0)
  const fresh = workScenarios
    .filter((row) => !done.has(row.id))
    // Each shard takes every nth row, so two of them never pick the same
    // scenario — the coverage ranking below would otherwise hand both the
    // thinnest one.
    .filter((_, index) => !shards || index % shards === (shard - 1 + shards) % shards)
  // Everything has run once: start the catalogue over rather than stop.
  const pool = fresh.length ? fresh : workScenarios

  if (STRIDE > 0) {
    const picked: WorkScenario[] = []
    for (let i = FROM; picked.length < COUNT && i < pool.length; i += STRIDE) {
      picked.push(pool[i])
    }
    return picked
  }

  // How many times each column has been played, counted off the rows that ran.
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
  // One pass preferring a persona and a job this run has not used either, so a
  // single batch is wide before it is deep; then whatever is thinnest.
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
  /** How long the whole row took, so a slow surface is visible as one. */
  seconds: number
  /** The first 400 characters of what the person would read. */
  sample: string
}

/**
 * 첨부가 근거일 때, 그 자료도 요청의 일부다.
 *
 * A row whose evidence is an attachment says 「조사 결과 발표자료를 만들어
 * 주세요」 and hands over a spreadsheet — and the subject of the work is in the
 * spreadsheet, not in the sentence. Judged against the sentence alone, a deck
 * correctly titled 「교육 비용 절감과 수료율 개선 방안」 reads as off-topic,
 * which is the judgement being wrong rather than the product.
 */
const EVIDENCE_SUBJECT = '교육 비용 단가 기간 이수 인원 내부 전담팀 외부 위탁'

/**
 * The evidence file a row brings.
 *
 * Written every time rather than written when missing. Four shards run at
 * once and they share this path, so "does it exist? then write it" is two
 * steps with a gap in the middle — one shard can be reading the file while
 * another is part-way through creating it. The contents are the same bytes on
 * every call, so writing unconditionally has no check to race against.
 */
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

/**
 * 산출물만. 요청은 빼고.
 *
 * The first version of this read `main` and looked for the request's subject
 * word in it — and `main` contains the request, echoed back in the transcript.
 * So the check passed for every row including the ones that were visibly
 * wrong: a 박사후연구원 연구계획 that came back as 「2026년 한국형 디지털 전환
 * 전략」 still contained 「계획」, because the person had typed it.
 *
 * The work is the artifact panel for a document surface, and for chat it is
 * the transcript with the request cut out of it.
 */
async function readWork(page: Page, surface: string, request: string): Promise<string> {
  if (surface === 'report' || surface === 'slides') {
    const panel = page.locator('[data-panel="artifact"]')
    if (await panel.isVisible().catch(() => false)) {
      return (await panel.innerText().catch(() => '')) || ''
    }
    return ''
  }
  const all = (await page.locator('main').innerText().catch(() => '')) || ''
  // Cut every echo of the request, and the composer's copy of it.
  return all.split(request).join(' ')
}

/** The panel's own heading — the title of whatever document is open. */
async function panelTitle(page: Page): Promise<string> {
  const panel = page.locator('[data-panel="artifact"]')
  if (!(await panel.isVisible().catch(() => false))) return ''
  const text = (await panel.innerText().catch(() => '')) || ''
  return text.split('\n')[0]?.trim() ?? ''
}

/**
 * 판단에 쓸 만한 낱말. 조사와 흔한 말은 뺀다.
 *
 * Used to ask whether a finished document is about what was asked for. Two
 * texts that share no content word at all are about different things, which is
 * the failure worth naming — a request for a research plan answered with a
 * national digital-transformation strategy.
 */
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
  // 조사가 붙은 낱말은 앞 두 글자로도 견준다 — 「미시사가」와 「미시사」.
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
    // 두 글자 이상 겹치면 같은 말로 본다.
    for (const other of asked) {
      if (word.length >= 3 && other.includes(word)) return true
      if (other.length >= 3 && word.includes(other)) return true
    }
  }
  return false
}

/**
 * 답이 온 뒤에 사람이 실제로 하는 일.
 *
 * The catalogue's fourth column — 수정 · 내보내기 · 공유 · 이어하기 — was
 * recorded on every row and performed on none, so a quarter of what the
 * catalogue claims to cover was never executed. These are the moments a piece
 * of work stops being a screen and becomes a file, a link, or something to
 * come back to, and each of them is a place the product has broken before.
 *
 * Returns `''` when the follow-up went through, or what went wrong.
 */
async function followUp(page: Page, scenario: WorkScenario): Promise<string> {
  const documentSurface = scenario.surface === 'report' || scenario.surface === 'slides'

  if (scenario.followUpId === 'export') {
    if (!documentSurface) return ''
    // 파일 탭 안에 내보내기가 있고, 형식은 그 아래 메뉴다.
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

    // 아직 공유한 적 없는 대화에는 링크가 없다 — 만들어야 생긴다. The first
    // version read the field straight away and reported 「공유 링크가 만들어
    // 지지 않았습니다: 」 with nothing after the colon, which was true and
    // said nothing: there was no field yet, only the button that makes one.
    const make = dialog.getByRole('button', { name: '링크 만들기' })
    if (await make.isVisible().catch(() => false)) {
      await make.click()
    }

    // 값이 들어올 때까지. `getByLabel` finds the field the moment it is drawn,
    // and it is drawn empty for the instant between the request going out and
    // the row coming back — reading it then gives `''` and reads as a link
    // that was never made, while the top bar is already saying 공유 중.
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
      // 공유 중 배지가 떴는지도 함께 본다 — 링크는 못 읽었는데 공유는 된
      // 경우와, 정말 아무 일도 없었던 경우는 다른 결함이다.
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
    // 기록에서 다시 찾아 이어서 연다 — 어제 하던 일을 오늘 여는 길.
    const here = page.url()
    await page.goto('/history')
    await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
    // 시간이 적힌 행만. `/전/` 하나로 고르면 「보이는 항목 *전*체 선택」이
    // 먼저 걸리고, 그 한 번이 목록 전체를 고른 채로 삭제 버튼 옆에 세워 둔다.
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

  // revise — 만든 것을 한 번 고쳐 본다.
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

    // The evidence this row is supposed to be working from.
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

    // 보고서 and 슬라이드 plan and stop; nothing is stored until it is approved.
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
      // 이 대화의 문서인가. A panel left open from the conversation before
      // this one shows a finished document beside a request it has nothing to
      // do with — and every check below would pass on somebody else's work.
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

    // The subject of the request has to be in the answer. Kept to a word the
    // request itself forces, so a miss is the work being about something else
    // rather than the model declining to guess a fact.
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

  // 한 건 끝날 때마다 적는다. A run of these is 20 minutes and anything can
  // stop it — a redeploy, a timeout, a stray signal — and a finding that only
  // exists in memory is a finding the next run has to earn again.
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const shard = process.env.WORK_SHARD ? `-s${process.env.WORK_SHARD}` : ''
  const log = path.join(OUT, `run-${stamp}${shard}.json`)

  const findings: Finding[] = []
  for (const [n, scenario] of chosen.entries()) {
    const finding = await runOne(page, scenario)
    // 잘못된 것은 사진으로 남긴다. A verdict names what went wrong; the screen
    // says why, and by the time anybody reads the log the run is long over and
    // the session is one of two hundred.
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

  // The run reports; it does not fail the suite on a model's judgement. Only
  // the app breaking — a blank screen, a panel that never opened, a thrown
  // error — is a failure this file is willing to assert.
  // `follow-up` is in here and `off-topic` is not, on purpose. A missing export
  // button or a share link that never appears is the app failing at something
  // it promises; a document about the wrong subject is the model, and a suite
  // that fails on a model's judgement fails at random.
  const broken = findings.filter((f) =>
    ['empty', 'no-artifact', 'follow-up', 'stalled', 'error'].includes(f.verdict),
  )
  expect(broken.map((f) => `${f.id}: ${f.verdict} ${f.detail}`), '앱이 결과물을 내놓지 못한 시나리오').toEqual([])
})
