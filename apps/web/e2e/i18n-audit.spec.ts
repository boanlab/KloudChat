import { test, expect, type Page } from '@playwright/test'

/**
 * Switches to English and hunts for Korean still on the screen.
 *
 * A string added to the dictionary but never wrapped in `t()` at the call site
 * is caught by neither the type checker nor the linter. Reading what was
 * actually rendered is the only reliable way to find it.
 */

const ADMIN = { email: 'admin@example.com', password: 'KloudChat-Admin-1234' }

/**
 * Things that are correct to leave in Korean.
 *
 * What a user typed is not translatable content. Neither are slugs: `@공문-작성`
 * is the name something is invoked by rather than text to read, and
 * translating it would make that name stop working.
 */
const ALLOWED = [
  /^[가-힣]$/, // 이름 첫 글자로 만든 마크, 언어 토글의 '한'
  /^[가-힣A-Za-z0-9]+(-[가-힣A-Za-z0-9]+)+$/, // 슬러그
  // Interface strings are translated whole, sentence by sentence, so English
  // and Korean mixed on one line means user content has been interpolated into
  // it — `Delete 회의 메모`, for instance.
  /[A-Za-z]{3}.*[가-힣]|[가-힣].*[A-Za-z]{3}/,
]

/** Conversations, memories and skills this account and these tests created.
 *  User data, so not translated. */
const SEEDED_BY_TESTS = [
  '서울 날씨',
  '예시대학교',
  '스펙트럼 자기지도',
  '라만 스펙트럼 SSL',
  '피크 검출',
  '계산을 반드시 검증',
  '수치의 단위를 검산한다',
  '소속',
  '단위 검산',
  '관리자',
  '기록 삭제 확인용',
  '이름 ',
]

type Finding = { where: string; text: string; selector: string }

async function scan(page: Page, where: string): Promise<Finding[]> {
  await page.waitForTimeout(600)
  return page.evaluate(
    ({ where, patterns, literals }) => {
      const found: { where: string; text: string; selector: string }[] = []
      const seen = new Set<string>()
      const ignore = patterns.map((p) => new RegExp(p))
      // User data also appears interpolated into a sentence (`{name} 삭제`).
      const skip = (text: string) =>
        ignore.some((re) => re.test(text)) || literals.some((l) => text.includes(l))

      const label = (el: Element): string => {
        const parts: string[] = []
        let cur: Element | null = el
        for (let i = 0; cur && i < 3; i++) {
          const tag = cur.tagName.toLowerCase()
          const cls = (cur.getAttribute('class') || '').split(/\s+/).slice(0, 2).join('.')
          parts.unshift(cls ? `${tag}.${cls}` : tag)
          cur = cur.parentElement
        }
        return parts.join(' > ')
      }

      const visible = (el: Element): boolean => {
        const r = el.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) return false
        const s = getComputedStyle(el)
        return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'
      }

      // Read per text node. Reading per element makes each parent repeat its
      // children's text, so one string is reported once per ancestor.
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = (n.textContent || '').trim()
        if (!text || !/[가-힣]/.test(text)) continue
        const el = n.parentElement
        if (!el || !visible(el)) continue
        if (skip(text)) continue
        const key = `${where}|${text}`
        if (seen.has(key)) continue
        seen.add(key)
        found.push({ where, text, selector: label(el) })
      }

      // Not visible as text, but read out by a screen reader.
      for (const attr of ['aria-label', 'placeholder', 'title', 'alt']) {
        for (const el of Array.from(document.querySelectorAll(`[${attr}]`))) {
          const text = (el.getAttribute(attr) || '').trim()
          if (!text || !/[가-힣]/.test(text)) continue
          if (!visible(el)) continue
          if (skip(text)) continue
          const key = `${where}|${attr}|${text}`
          if (seen.has(key)) continue
          seen.add(key)
          found.push({ where, text: `[${attr}] ${text}`, selector: label(el) })
        }
      }
      return found
    },
    { where, patterns: ALLOWED.map((r) => r.source), literals: SEEDED_BY_TESTS },
  )
}

const ROUTES: [string, string][] = [
  ['/', '홈'],
  ['/new/chat', '새 챗'],
  ['/new/report', '새 보고서'],
  ['/new/slides', '새 슬라이드'],
  ['/new/image', '새 이미지'],
  ['/new/av', '새 오디오·동영상'],
  ['/projects', '프로젝트'],
  ['/artifacts', '아티팩트'],
  ['/agents', '에이전트'],
  ['/skills', '스킬'],
  ['/memory', '메모리'],
  ['/history', '기록'],
  ['/usage', '사용량'],
  ['/connectors', '커넥터'],
  ['/agent-setup', 'AI 에이전트 연동'],
  ['/designs', '디자인'],
  ['/settings', '설정'],
  ['/settings/preferences', '설정·환경설정'],
  ['/settings/keys', '설정·API 키'],
  ['/admin/users', '관리자·사용자'],
  ['/admin/usage', '관리자·사용량'],
  ['/admin/system', '관리자·시스템'],
  ['/admin/system/routing', '관리자·라우팅'],
  ['/admin/system/features', '관리자·기능'],
  ['/admin/system/templates', '관리자·공용 템플릿'],
  ['/admin/system/branding', '관리자·브랜딩'],
  ['/admin/system/mail', '관리자·메일'],
  ['/admin/governance', '관리자·거버넌스'],
]

test('영어 모드에 남은 한글', async ({ page }) => {
  const findings: Finding[] = []

  // Start at the sign-in screen. The store is empty there, so the language is
  // planted directly.
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await page.goto('/')
  await page.waitForTimeout(1500)
  findings.push(...(await scan(page, '로그인')))

  // The password reset screen is part of the sign-in surface too.
  await page.getByText(/Forgot|비밀번호/).first().click().catch(() => {})
  await page.waitForTimeout(500)
  findings.push(...(await scan(page, '로그인 › 비밀번호 재설정')))
  await page.goto('/')
  await page.waitForTimeout(800)

  await page.getByLabel(/^(Email|이메일)$/).fill(ADMIN.email)
  await page.getByLabel(/^(Password|비밀번호)$/).fill(ADMIN.password)
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/auth/login')),
    page.locator('form').getByRole('button').last().click(),
  ])
  await page.waitForTimeout(2000)

  for (const [path, name] of ROUTES) {
    await page.goto(path)
    findings.push(...(await scan(page, name)))

    // Screens with tabs render different content per tab.
    const tabs = page.getByRole('tab')
    const count = await tabs.count().catch(() => 0)
    for (let i = 0; i < count; i++) {
      await tabs.nth(i).click().catch(() => {})
      const tabName = (await tabs.nth(i).textContent().catch(() => '')) || `탭${i}`
      findings.push(...(await scan(page, `${name} › ${tabName.trim()}`)))
    }
  }

  // The account menu is a popover rather than a route, so it is opened here.
  await page.goto('/')
  const avatar = page.locator('aside button').last()
  await avatar.click().catch(() => {})
  findings.push(...(await scan(page, '사용자 메뉴')))
  await page.keyboard.press('Escape')

  const report = findings
    .map((f) => `  [${f.where}] ${f.text}\n      ${f.selector}`)
    .join('\n')
  console.log(`\n=== 영어 모드에 남은 한글: ${findings.length}건 ===\n${report}\n`)

  expect(findings, `${findings.length}건`).toEqual([])
})
