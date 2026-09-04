import { expect, test, type Page } from '@playwright/test'
import { gotoSurface, openSidebar, pickToolModel, seedPendingUser, signIn, surfaceOn } from './helpers'
import { personas } from './personas'

/** Persona coverage: every `test.step` is one `Need` id from `personas.ts`. */

const composer = (page: Page) => page.getByLabel('프롬프트 입력')

/** Existence probe: a need is on screen now or it is not. */
const probe = expect.configure({ timeout: 5_000 })

// One missing control must not consume the whole budget.
test.use({ actionTimeout: 5_000 })

/** Opens the newest artifact of a kind in its own conversation. */
async function openNewest(page: import('@playwright/test').Page, tab: string) {
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).click()
  await page.getByText('원본 작업 열기').first().click()
  await page.waitForURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
}

/** Needs recorded as open rather than as regressions, with the reason. */
const KNOWN_OPEN: Record<string, string> = {
  // Controls on a finished deck; a clean account has none (slides.spec.ts covers creation).
  'biz-pptx': '완성된 슬라이드 fixture가 없는 계정에서는 확인할 수 없음',
  'biz-share': '완성된 슬라이드 fixture가 없는 계정에서는 확인할 수 없음',
  'sal-share': '완성된 슬라이드 fixture가 없는 계정에서는 확인할 수 없음',
  'biz-notes': '완성된 슬라이드 fixture가 없는 계정에서는 확인할 수 없음',
  'biz-factcheck': '완성된 슬라이드 fixture가 없는 계정에서는 확인할 수 없음',
  // Owned by persona-journeys.
  'grad-knowledge': 'persona-journeys의 프로젝트 업로드·검색 실사용 검증으로 대체',
  'res-agent-share': '에이전트 조직 공유 UI 미구현',
  // Connectors left out of the catalogue until verified against real credentials.
  'hum-citation': 'Zotero 커넥터 미검증 — 카탈로그에서 제외',
  'grad-zotero': 'Zotero 커넥터 미검증 — 카탈로그에서 제외',
  'soc-citation': '문헌 커넥터 미검증 — 카탈로그에서 제외',
  'soc-stats-db': 'PostgreSQL 커넥터 미검증 — 카탈로그에서 제외',
  'dev-db': 'PostgreSQL 커넥터 미검증 — 카탈로그에서 제외',
  'eng-arxiv': 'arXiv 커넥터 제외 — 도구 14개, 대상 사용자 대비 과함',
  'dev-github': 'GitHub 커넥터 미검증 — 카탈로그에서 제외',
  'off-drive': 'Google Drive 커넥터 미검증 — 카탈로그에서 제외',
}

// One shared account and workspace: parallel runs would see each other's state.
test.describe.configure({ mode: 'serial' })

test.describe('페르소나 커버리지', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
  })

  /* ── shared groundwork ─────────────────────────────────────────────── */

  test('켜져 있는 축은 모두 진입 가능', async ({ page }) => {
    // `image` and `av` default to off and show an EmptyState with no composer.
    const reached: string[] = []
    for (const kind of ['chat', 'report', 'slides', 'image', 'av']) {
      if (!(await surfaceOn(page, kind))) continue
      await probe(composer(page)).toBeVisible()
      reached.push(kind)
    }
    // The three document surfaces are not optional.
    expect(reached, `진입하지 못한 축이 있다: ${reached.join(', ')}`).toEqual(
      expect.arrayContaining(['chat', 'report', 'slides']),
    )
  })

  test('회원가입은 관리자 승인 대기 상태로 들어간다', async ({ page }) => {
    // A fresh address every run, deleted at the end.
    const pending = await seedPendingUser(page, `e2e-pending-${Date.now().toString(36)}@example.com`)
    try {
      await page.goto('/admin/users')
      await page.getByPlaceholder('이름 또는 이메일').fill(pending)
      const row = page.locator('tr', { hasText: pending })
      await probe(row).toBeVisible({ timeout: 15_000 })
      await probe(page.getByRole('heading', { name: '사용자 · 크레딧' })).toBeVisible()
      // The account's name is also "승인 대기"; the badge is the status.
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
        test.setTimeout(240_000)
        const missing: string[] = []
        const check = async (id: string, fn: () => Promise<void>) => {
          // Close anything a previous check left open.
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
                // By role and name: a text match also hits the policy's explanation.
                await probe(page.getByRole('switch', { name: '개인정보 마스킹' })).toBeVisible()
                break
              case 'off-audit':
                // Signing in for this test guarantees at least one row.
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

              /* a recording, read */
              case 'off-voice':
                // A recording arrives as an attached file.
                await gotoSurface(page, 'report')
                await probe(page.getByRole('button', { name: '첨부' }).first()).toBeVisible()
                break

              /* templates and starting points */
              case 'hum-template':
              case 'off-template':
              case 'sal-template':
                // Templates belong to document surfaces, not the persona's first surface.
                await gotoSurface(
                  page,
                  need.id === 'hum-template' || need.id === 'off-template' ? 'report' : 'slides',
                )
                await probe(page.getByRole('button', { name: '작업 시작하기' })).toBeVisible()
                break

              /* maths */
              case 'eng-math': {
                await page.goto('/new/chat')
                // The renderer contract, with the response shape pinned.
                await page.route('**/api/sessions/*/messages', async (route) => {
                  if (route.request().method() !== 'POST') return route.continue()
                  await route.fulfill({
                    status: 200,
                    headers: { 'content-type': 'text/event-stream' },
                    body:
                      `data: ${JSON.stringify({ type: 'delta', text: '$$x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$$' })}\n\n` +
                      `data: ${JSON.stringify({ type: 'done' })}\n\n`,
                  })
                })
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
                // A real turn.
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
                await page.goto('/artifacts')
                await page.getByRole('tab', { name: /^차트/ }).click()
                await page.locator('button.aspect-video').first().click()
                await probe(page.getByRole('dialog').locator('svg[role="img"]').first()).toBeVisible()
                await probe(page.getByRole('dialog').getByRole('button', { name: '데이터' })).toBeVisible()
                break

              /* report */
              case 'eng-report-toc':
                await openNewest(page, '보고서')
                await page.getByRole('tab', { name: '보기', exact: true }).click()
                await probe(page.getByRole('button', { name: /^목차/ }).first()).toBeVisible()
                break
              case 'hum-sources':
              case 'res-sources':
                await openNewest(page, '보고서')
                await page.getByRole('tab', { name: '검토', exact: true }).click()
                await probe(page.getByRole('button', { name: /출처/ })).toBeVisible()
                break
              case 'grad-section-regen':
                await openNewest(page, '보고서')
                // Section controls are in the web view; a styled document opens in the page view.
                if ((await page.locator('.page').count()) > 0) {
                  await page.getByRole('button', { name: '웹뷰' }).click()
                }
                await page.getByRole('button', { name: /절 편집$/ }).first().click()
                await probe(
                  page.getByRole('menuitem', { name: /다시 쓰기|재생성/ }).first(),
                ).toBeVisible()
                await page.keyboard.press('Escape')
                break
              case 'hum-export-docx':
              case 'off-docx':
              case 'res-export-pdf':
                await openNewest(page, '보고서')
                await page.getByRole('tab', { name: '파일', exact: true }).click()
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
                await page.getByRole('tab', { name: '파일', exact: true }).click()
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
                // Exact: three controls on this screen contain 공유.
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
                // Any owned project has the same knowledge surface.
                {
                  const response = await page.request.get('/api/projects')
                  const projects = (await response.json()) as { id: string }[]
                  expect(projects.length, '지식 화면을 확인할 프로젝트가 없습니다').toBeGreaterThan(0)
                  await page.goto(`/projects/${projects[0].id}`)
                }
                await probe(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 15_000 })
                await probe(page.getByRole('tab', { name: /지식/ })).toBeVisible()
                break
              case 'grad-memory':
                await page.goto('/memory')
                await probe(page.getByRole('heading', { name: '메모리' })).toBeVisible()
                break
              case 'grad-version': {
                await openNewest(page, '보고서')
                // `.first()`: a conversation may show several artifacts, each with its own history.
                await page.getByRole('tab', { name: '검토', exact: true }).click()
                const history = page.getByRole('button', { name: '버전 기록' }).first()
                await probe(history).toBeVisible()
                await history.click()
                const listed = page.getByRole('dialog', { name: '버전 기록' })
                await probe(listed).toBeVisible()
                await probe(listed.getByText(/현재 v\d+/)).toBeVisible()
                // A version to go back to, or the dialog saying there is none yet.
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
                // `/keys` redirects home.
                await page.goto('/settings/keys')
                await probe(page.getByRole('button', { name: '새 키' })).toBeVisible()
                break
              case 'off-search':
                await openSidebar(page)
                await probe(page.getByPlaceholder('검색')).toBeVisible()
                break

              /* development */
              case 'dev-steps': {
                // A web search turn emits steps; the strict-local default has no web tool.
                await gotoSurface(page, 'chat')
                await pickToolModel(page)
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
