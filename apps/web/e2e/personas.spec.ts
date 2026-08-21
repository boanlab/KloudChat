import { expect, test, type Page } from '@playwright/test'
import { gotoSurface, openSidebar, seedPendingUser, signIn } from './helpers'
import { personas } from './personas'

/**
 * Persona coverage review.
 *
 * Every `test.step` maps to one `Need` id from `personas.ts`. A failure means
 * the persona cannot do that part of their job in the current UI — that is the
 * finding, and the fix belongs in the app, not here.
 */

const composer = (page: Page) => page.getByLabel('프롬프트 입력')

/** Needs with no backend behind them yet. The moment an entry is removed,
 *  the checks below run against it unchanged. */
/**
 * An existence probe. It does not wait.
 *
 * Each need is either on the screen right now or it is not. Waiting the
 * default 30 seconds means a persona with several gaps spends minutes and ends
 * in "timed out" — when what is missing was the answer.
 */
const probe = expect.configure({ timeout: 5_000 })

// Clicking a button that is not there waits until the test dies. This keeps
// one gap from consuming the whole budget.
test.use({ actionTimeout: 5_000 })

/**
 * Opens the newest artifact of a kind on its own surface.
 *
 * It deliberately does not point at a seeded demo session: when a fixture like
 * that disappears, every need behind it reads as "not implemented" while the
 * screen works perfectly well.
 */
async function openNewest(page: import('@playwright/test').Page, tab: string) {
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).click()
  await page.getByText('원본 작업 열기').first().click()
  await page.waitForURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
}

const KNOWN_OPEN: Record<string, string> = {
  // No MCP server available to start.
  'res-agent-share': '에이전트 조직 공유 UI 미구현',
  // Connectors absent from the catalogue. Not unimplemented — **left out
  // because they have not been verified against real credentials**. To restore
  // one, start it once and check how many tools it adds to a turn.
  'hum-citation': 'Zotero 커넥터 미검증 — 카탈로그에서 제외',
  'grad-zotero': 'Zotero 커넥터 미검증 — 카탈로그에서 제외',
  'soc-citation': '문헌 커넥터 미검증 — 카탈로그에서 제외',
  'soc-stats-db': 'PostgreSQL 커넥터 미검증 — 카탈로그에서 제외',
  'dev-db': 'PostgreSQL 커넥터 미검증 — 카탈로그에서 제외',
  'eng-arxiv': 'arXiv 커넥터 제외 — 도구 14개, 대상 사용자 대비 과함',
  'dev-github': 'GitHub 커넥터 미검증 — 카탈로그에서 제외',
  'off-drive': 'Google Drive 커넥터 미검증 — 카탈로그에서 제외',
}


// Every persona signs in as the same shared account and walks the same
// workspace, so running them in parallel means one test's open modal or
// half-saved row shows up in another's assertions. Serial is the honest cost of
// sharing state.
test.describe.configure({ mode: 'serial' })

test.describe('페르소나 커버리지', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
  })

  /* ── shared groundwork ─────────────────────────────────────────────── */

  test('다섯 축 모두 진입 가능', async ({ page }) => {
    for (const kind of ['chat', 'report', 'slides', 'image', 'av']) {
      await gotoSurface(page, kind)
      await probe(composer(page)).toBeVisible()
    }
  })

  test('회원가입은 관리자 승인 대기 상태로 들어간다', async ({ page }) => {
    // A fresh address every run, deleted at the end. Reusing one inherits
    // whatever an earlier run left it as — a rejected account sits at the bottom
    // of the list as `suspended` and the probe never finds what it expects.
    const pending = await seedPendingUser(page, `e2e-pending-${Date.now().toString(36)}@example.com`)
    try {
      await page.goto('/admin/users')
      await page.getByPlaceholder('이름 또는 이메일').fill(pending)
      const row = page.locator('tr', { hasText: pending })
      await probe(row).toBeVisible({ timeout: 15_000 })
      await probe(page.getByRole('heading', { name: '사용자 · 크레딧' })).toBeVisible()
      // The account's *name* is also "승인 대기" (pending approval); the badge
      // is the status.
      await probe(row.getByText('승인 대기').last()).toBeVisible()
      await probe(row.getByRole('button', { name: '승인', exact: true })).toBeVisible()
    } finally {
      await page.goto('/admin/users')
      await page.getByPlaceholder('이름 또는 이메일').fill(pending)
      const row = page.locator('tr', { hasText: pending })
      await row.getByRole('button', { name: '계정 삭제' }).click({ timeout: 10_000 }).catch(() => {})
      await page
        .getByRole('dialog')
        .getByRole('button', { name: '삭제', exact: true })
        .click({ timeout: 5_000 })
        .catch(() => {})
    }
  })

  /* ── per persona ───────────────────────────────────────────────────── */

  for (const persona of personas) {
    test.describe(`${persona.name} — ${persona.role}`, () => {
      test('필요 기능이 UI에 존재한다', async ({ page }, testInfo) => {
        // Two dozen needs, each its own navigation, plus a probe's wait for every
        // one that is not built yet.
        test.setTimeout(240_000)
        const missing: string[] = []
        const check = async (id: string, fn: () => Promise<void>) => {
          // Each need is independent, but they share one page. A previous check
          // that opened a modal and did not close it would make the next one
          // fail on an intercepted click — a harness artefact reported as a
          // missing feature. Escape costs nothing when nothing is open.
          await page.keyboard.press('Escape').catch(() => {})
          try {
            await test.step(id, fn)
          } catch {
            missing.push(id)
          }
        }

        /* shared checks */
        await check('surface-entry', async () => {
          await gotoSurface(page, persona.surfaces[0])
          await probe(composer(page)).toBeVisible()
        })

        for (const need of persona.needs) {
          await check(need.id, async () => {
            switch (need.id) {
              /* attachments and uploads */
              case 'hum-upload':
              case 'soc-csv':
                await gotoSurface(page, 'chat')
                // Attaching is a real file picker now, not a menu of fake names.
                await probe(page.getByRole('button', { name: '첨부' }).first()).toBeVisible()
                await probe(page.locator('input[type="file"]').first()).toBeAttached()
                break

              /* model comparison */
              case 'hum-compare':
              case 'res-compare':
                await gotoSurface(page, 'chat')
                await page.getByRole('button', { name: '모델 비교' }).click()
                await probe(page.getByText('비교 모드')).toBeVisible()
                await page.keyboard.press('Escape')
                break

              /* slide fact-checking */
              case 'biz-factcheck':
                await probe(page.getByRole('button', { name: /팩트체크/ })).toBeVisible()
                break

              /* shared agent store */
              case 'res-agent-share':
                await page.goto('/agents')
                await page.getByRole('tab', { name: /스토어/ }).click()
                await probe(page.getByText('공유됨').first()).toBeVisible()
                break

              /* governance */
              case 'off-pii':
                await page.goto('/admin/governance')
                // The switch alone, by role and name. A loose text match also
                // caught the sentence explaining what the policy forbids —
                // which is rendered or not depending on the policy's own
                // state, so the probe passed or exploded according to what
                // some other spec had last left switched on.
                await probe(page.getByRole('switch', { name: '개인정보 마스킹' })).toBeVisible()
                break
              case 'off-audit':
                // The trail lives behind its own tab now that the policy panel
                // is back. Someone logged in to run this test, so there is at
                // least one row.
                await page.goto('/admin/governance')
                await page.getByRole('tab', { name: /감사 로그/ }).click()
                await probe(page.getByRole('columnheader', { name: '행위' })).toBeVisible()
                await probe(page.getByText('로그인').first()).toBeVisible()
                break
              case 'off-usage':
                await page.goto('/admin/usage')
                await probe(page.getByRole('heading', { name: '사용량' })).toBeVisible()
                await probe(page.getByText('모델별')).toBeVisible()
                break

              /* web search */
              case 'soc-websearch':
              case 'res-websearch':
                await gotoSurface(page, 'chat')
                await probe(page.getByRole('button', { name: '웹 검색' })).toBeVisible()
                break

              /* dictation */
              case 'off-voice':
                await gotoSurface(page, 'chat')
                await probe(page.getByRole('button', { name: '음성 입력' })).toBeVisible()
                break

              /* templates and starting points */
              case 'hum-template':
              case 'off-template':
              case 'sal-template':
                await gotoSurface(page, persona.surfaces[0])
                await probe(page.getByRole('button', { name: '시작점 고르기' })).toBeVisible()
                break

              /* maths */
              case 'eng-math': {
                                // Verified with a real turn, not a seeded
                                // conversation.
                await page.goto('/new/chat')
                                // The format has to be asked for: "show it as
                                // a formula" alone often comes back as plain
                                // text.
                await page
                  .getByLabel('프롬프트 입력')
                  .fill('이차방정식의 근의 공식을 $...$ 로 감싼 LaTeX 수식으로만 보여줘. 설명은 붙이지 마.')
                await page.getByLabel('프롬프트 입력').press('Enter')
                await page.waitForURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
                await probe(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })
                await probe(page.locator('.katex').first()).toBeVisible({ timeout: 20_000 })
                break
              }

              /* copying a code block */
              case 'eng-code':
              case 'dev-code-artifact':
              case 'eng-code-artifact':
                // Chat extracts substantial code into an artifact and opens the
                // panel on it. Driven by a real turn, not a seeded transcript.
                await gotoSurface(page, 'chat')
                await page
                  .getByLabel('프롬프트 입력')
                  .fill('JSON 을 읽어 키별 개수를 세는 파이썬 함수를 예외 처리 포함해 20줄 이상으로 써줘.')
                await page.getByLabel('프롬프트 입력').press('Enter')
                await probe(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })
                await probe(page.getByRole('button', { name: '복사' }).first()).toBeVisible({
                  timeout: 20_000,
                })
                break

              /* chart artifacts */
              case 'biz-chart':
              case 'soc-chart':
              case 'eng-chart':
                // Asserts that a chart actually exists and opens with its
                // underlying data — not that a seeded artifact has a title.
                await page.goto('/artifacts')
                await page.getByRole('tab', { name: /^차트/ }).click()
                await page.locator('button.aspect-video').first().click()
                await probe(page.getByRole('dialog').locator('svg[role="img"]').first()).toBeVisible()
                await probe(page.getByRole('dialog').getByRole('button', { name: '데이터' })).toBeVisible()
                break

              /* report */
              case 'eng-report-toc':
                await openNewest(page, '보고서')
                // Wide viewports get the left rail, narrow ones the header
                // button; one of the two has to be visible.
                await probe(page.getByText(/^목차/).filter({ visible: true }).first()).toBeVisible()
                break
              case 'hum-sources':
              case 'res-sources':
                await openNewest(page, '보고서')
                await probe(page.getByRole('button', { name: /출처/ })).toBeVisible()
                break
              case 'grad-section-regen':
                await openNewest(page, '보고서')
                await probe(page.getByRole('button', { name: /다시 쓰기|재생성/ }).first()).toBeVisible()
                break
              case 'hum-export-docx':
              case 'off-docx':
              case 'res-export-pdf':
                await openNewest(page, '보고서')
                await page.getByRole('button', { name: '내보내기', exact: true }).click()
                await probe(page.getByText(/Word|PDF/).first()).toBeVisible()
                break

              /* slides */
              case 'biz-deck':
              case 'sal-deck':
                await gotoSurface(page, 'slides')
                await probe(composer(page)).toBeVisible()
                break
              case 'biz-pptx':
                await openNewest(page, '슬라이드')
                await page.getByRole('button', { name: '내보내기', exact: true }).click()
                await probe(page.getByText('PowerPoint')).toBeVisible()
                break
              case 'biz-notes':
                await openNewest(page, '슬라이드')
                await probe(
                  page.locator('aside').getByText('발표 노트', { exact: true }),
                ).toBeVisible()
                break

              /* sharing */
              case 'biz-share':
              case 'sal-share':
                await openNewest(page, '슬라이드')
                // Exact: `name` matches by substring, and three controls on
                // this screen contain 공유 — the probe was failing on strict
                // mode and recording a capability that is right there.
                await probe(
                  page.getByRole('button', { name: '공유', exact: true }),
                ).toBeVisible()
                break

              /* connectors */
              case 'hum-citation':
              case 'grad-zotero':
              case 'soc-citation':
                await page.goto('/connectors')
                await page.getByRole('tab', { name: /카탈로그/ }).click()
                await probe(page.getByText('Zotero').first()).toBeVisible()
                break
              case 'soc-stats-db':
              case 'dev-db':
                await page.goto('/connectors')
                await page.getByRole('tab', { name: /카탈로그/ }).click()
                await probe(page.getByText('PostgreSQL').first()).toBeVisible()
                break
              case 'eng-arxiv':
                await page.goto('/connectors')
                await page.getByRole('tab', { name: /카탈로그/ }).click()
                await probe(page.getByText('arXiv').first()).toBeVisible()
                break
              case 'dev-github':
                await page.goto('/connectors')
                await page.getByRole('tab', { name: /카탈로그/ }).click()
                await probe(page.getByText('GitHub').first()).toBeVisible()
                break
              case 'off-drive':
                await page.goto('/connectors')
                await page.getByRole('tab', { name: /카탈로그/ }).click()
                await probe(page.getByText('Google Drive').first()).toBeVisible()
                break
              case 'res-custom-mcp':
                await page.goto('/connectors')
                await probe(page.getByRole('button', { name: '서버 직접 추가' })).toBeVisible()
                break
              case 'dev-tool-scope':
                await page.goto('/connectors')
                await page.getByRole('button', { name: '도구 설정' }).first().click()
                await probe(page.getByText('도구 권한')).toBeVisible()
                await probe(page.getByText('쓰기').first()).toBeVisible()
                break

              /* workspace */
              case 'grad-project':
                await page.goto('/projects')
                await probe(page.getByRole('heading', { name: '프로젝트' })).toBeVisible()
                break
              case 'grad-knowledge':
                await page.goto('/projects')
                // The card is a clickable div, so the click has to land on its text.
                await page.getByText('학위논문', { exact: true }).first().click()
                await probe(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 15_000 })
                await probe(page.getByRole('tab', { name: /지식/ })).toBeVisible()
                break
              case 'grad-memory':
                await page.goto('/memory')
                await probe(page.getByRole('heading', { name: '메모리' })).toBeVisible()
                break
              case 'grad-version': {
                await openNewest(page, '보고서')
                // `.first()`, because a conversation shows several artifacts
                // and each carries its own history — the need is that an
                // earlier judgement is reachable, not that there is exactly
                // one of them on screen.
                const history = page.getByRole('button', { name: '버전 기록' }).first()
                await probe(history).toBeVisible()
                await history.click()
                // Asked of the dialog, not of the page. The old check searched
                // the whole document for `v3` or 버전 and took the first hit,
                // which the dialog satisfied only while it was rendered at the
                // end of the report panel; the shared control opens it beside
                // the button instead. It also never asked the question the need
                // asks — the dialog's own title passed it.
                const listed = page.getByRole('dialog', { name: '버전 기록' })
                await probe(listed).toBeVisible()
                await probe(listed.getByText(/현재 v\d+/)).toBeVisible()
                // Either 판 to go back to, or the dialog saying plainly that
                // there is none yet. Both prove the affordance; requiring a
                // 되돌리기 button made the probe depend on whether this
                // account's newest report had ever been edited, which is an
                // accident of the data and not a fact about the product.
                await probe(
                  listed
                    .getByRole('button', { name: /되돌리기/ })
                    .or(listed.getByText('아직 저장된 이전 판이 없습니다.'))
                    .first(),
                ).toBeVisible()
                break
              }
              case 'res-agent':
                await page.goto('/agents')
                await probe(page.getByRole('heading', { name: '에이전트' })).toBeVisible()
                break
              case 'res-apikey':
              case 'dev-apikey':
                // A tab under Settings; `/keys` redirects home.
                await page.goto('/settings/keys')
                await probe(page.getByRole('button', { name: '새 키' })).toBeVisible()
                break
              case 'off-search':
                await openSidebar(page)
                await probe(page.getByPlaceholder('검색')).toBeVisible()
                break

              /* development */
              case 'dev-steps': {
                // Tool-call steps are real: a web search turn emits them. The
                // screen default is strict-local, which is given no web tool
                // at all, so the model comes first — the need is that the
                // steps appear, not that the default can search.
                await gotoSurface(page, 'chat')
                await page
                  .getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi/i })
                  .first()
                  .click()
                const rows = page.getByRole('button', { name: /qwen3\.6/i })
                for (let i = 0; i < (await rows.count()); i++) {
                  const label =
                    (await rows.nth(i).getAttribute('aria-label')) ?? (await rows.nth(i).innerText())
                  if (!/strict/i.test(label)) {
                    await rows.nth(i).click()
                    break
                  }
                }
                await page.getByRole('button', { name: '웹 검색' }).first().click()
                await page.getByLabel('프롬프트 입력').fill('올해 노벨 물리학상 수상자를 웹에서 찾아줘.')
                await page.getByLabel('프롬프트 입력').press('Enter')
                await probe(page.getByText('웹 검색 중').first()).toBeVisible({ timeout: 60_000 })
                break
              }

              /* responsive layout */
              case 'sal-mobile': {
                await page.setViewportSize({ width: 820, height: 1180 })
                await page.goto('/new/chat')
                await probe(composer(page)).toBeVisible()
                // The body must not scroll horizontally on a narrow viewport.
                const overflow = await page.evaluate(
                  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
                )
                expect(overflow).toBeLessThanOrEqual(1)
                break
              }

              default:
                throw new Error(`unhandled need: ${need.id}`)
            }
          })
        }

        const open = missing.filter((id) => id in KNOWN_OPEN)
        const regressions = missing.filter((id) => !(id in KNOWN_OPEN))

        await testInfo.attach('missing-needs', {
          body: JSON.stringify(
            {
              persona: persona.id,
              regressions,
              knownOpen: open.map((id) => ({ id, reason: KNOWN_OPEN[id] })),
            },
            null,
            2,
          ),
          contentType: 'application/json',
        })
        expect(regressions, `${persona.name} 미지원 기능`).toEqual([])
      })
    })
  }
})
