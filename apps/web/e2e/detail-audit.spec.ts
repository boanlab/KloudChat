import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'
import { personas } from './personas'

/** Detail sweep over every route and viewport: names, tooltips, tap targets, empty states, panels.
 *  Defects are deduplicated per control (`identity`) and written to `audit/detail-audit.json`; never fails. */

interface Observation {
  rule: string
  /** The control, named the way the persona would point at it. */
  subject: string
  /** Which control this is, as opposed to which copy of it (`identity`). */
  key?: string
  where: string
  viewport: string
  hurts: string
}

/** Routes every signed-in persona can reach. */
const ROUTES: [path: string, label: string][] = [
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
  ['/history', '대화 기록'],
  ['/usage', '내 사용량'],
  ['/connectors', '커넥터'],
  ['/agent-setup', '에이전트 설정'],
  ['/designs', '디자인'],
  ['/settings', '설정'],
  ['/settings/preferences', '설정 · 환경'],
  ['/settings/keys', '설정 · 키'],
  ['/admin/users', '관리자 · 사용자'],
  ['/admin/usage', '관리자 · 사용량'],
  ['/admin/system', '관리자 · 시스템'],
  ['/admin/system/routing', '관리자 · 라우팅'],
  ['/admin/system/features', '관리자 · 기능'],
  ['/admin/system/templates', '관리자 · 공용 템플릿'],
  ['/admin/system/branding', '관리자 · 브랜딩'],
  ['/admin/system/mail', '관리자 · 메일'],
  ['/admin/governance', '관리자 · 정책'],
]

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  laptop: { width: 1280, height: 800 },
  tablet: { width: 820, height: 1180 },
  /** No persona works at this width, but the layout supports it. */
  phone: { width: 390, height: 844 },
} as const

/** Personas per viewport, for the report. */
const WHO: Record<string, string> = Object.fromEntries(
  (Object.keys(VIEWPORTS) as (keyof typeof VIEWPORTS)[]).map((v) => [
    v,
    personas.filter((p) => p.viewport === v).map((p) => `${p.name}(${p.role})`).join(', ') || '—',
  ]),
)

/** Every rule decidable from one rendered screen, run in-page so a screen is one round trip. */
async function auditScreen(page: Page, where: string, viewport: string) {
  return page.evaluate(
    ({ touch, where, viewport }) => {
      const out: {
        rule: string
        subject: string
        key: string
        where: string
        viewport: string
        hurts: string
        ok: boolean
      }[] = []
      const add = (rule: string, ok: boolean, subject: string, hurts: string, key = subject) =>
        out.push({ rule, ok, subject, key, where, viewport, hurts })

      const visible = (el: Element) => {
        const r = el.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) return false
        const s = getComputedStyle(el)
        return s.visibility !== 'hidden' && s.display !== 'none'
      }

      /** A stable name for a control across screens. */
      const label = (el: Element) => {
        const n = (
          el.getAttribute('aria-label') ||
          el.getAttribute('title') ||
          (el as HTMLElement).innerText ||
          el.getAttribute('placeholder') ||
          ''
        )
          .replace(/\s+/g, ' ')
          .trim()
        if (n) return n.slice(0, 40)
        // Nameless: fall back to where it sits.
        const region = el.closest('[aria-label]')?.getAttribute('aria-label')
        return `<${el.tagName.toLowerCase()}${region ? ` in ${region}` : ''}>`
      }

      /** Which control this is, as opposed to which copy: tag, class list and icon class.
       *  A per-row control carries its row's name as label, so the label cannot be the key. */
      const identity = (el: Element) =>
        [
          el.tagName.toLowerCase(),
          (el.getAttribute('class') || '').replace(/\s+/g, ' ').trim(),
          el.querySelector('svg')?.getAttribute('class') || '',
        ].join('|')

      const controls = Array.from(
        document.querySelectorAll(
          'button, a[href], input, textarea, select, [role="switch"], [role="tab"], [role="menuitem"]',
        ),
      ).filter(visible)

      for (const el of controls) {
        const text = ((el as HTMLElement).innerText || '').trim()
        const aria = el.getAttribute('aria-label')
        const title = el.getAttribute('title')
        const id = label(el)
        const who = identity(el)
        const isInput = /^(input|textarea|select)$/i.test(el.tagName)

        // R1: every control has a name; for inputs a real label, not the placeholder.
        const named = isInput
          ? !!(aria || el.getAttribute('id') && document.querySelector(`label[for="${el.getAttribute('id')}"]`) || el.closest('label'))
          : !!(text || aria)
        add('name', named, id, '읽어 주지 못하고, 무엇을 넣는 칸인지 알 수 없다', who)

        // R2: icon-only controls need a tooltip.
        if (!text && !!el.querySelector('svg')) {
          add('tooltip', !!title, id, '아이콘만 보고 뜻을 짐작해야 한다', who)
        }

        // R3: hover-revealed controls do not exist on touch.
        if (touch) {
          let hidden = false
          for (let cur: Element | null = el, i = 0; cur && i < 4; cur = cur.parentElement, i++) {
            const cls = cur.getAttribute('class') || ''
            if (/opacity-0/.test(cls) && /group-hover|focus-within/.test(cls)) hidden = true
          }
          add('touch-reach', !hidden, id, '터치 기기에서는 존재하지 않는 버튼이다', who)

          // R4: tap target under 32px. An input wrapped in a label is as big as the label.
          const target = (el.closest('label') as Element | null) ?? el
          const r = target.getBoundingClientRect()
          const tiny = Math.min(r.width, r.height) < 32 && el.tagName !== 'A'
          add('tap-size', !tiny, `${id} · ${Math.round(r.width)}×${Math.round(r.height)}`,
            '손가락으로 정확히 누르기 어렵다', who)
        }

        // R5: disabled with no reason on screen.
        if ((el as HTMLButtonElement).disabled) {
          add('disabled-reason', !!title, id, '왜 못 누르는지 알 수 없다', who)
        }
      }

      // R6: no horizontal scroll.
      const de = document.documentElement
      add('no-h-scroll', de.scrollWidth <= de.clientWidth + 1,
        `${where} ${de.scrollWidth}>${de.clientWidth}`, '가로로 밀어야 내용이 보인다')

      // R7: an empty state offers an action.
      for (const el of Array.from(document.querySelectorAll('p')).filter(
        (e) =>
          visible(e) &&
          /없습니다|비어 있습니다/.test(e.textContent || '') &&
          // Content inside a thumbnail button or a clickable card is not an empty state.
          !e.closest('button') &&
          !e.closest('[class*="cursor-pointer"]'),
      )) {
        const box = el.closest('div')?.parentElement
        add('empty-action', !!box?.querySelector('button, a[href]'),
          (el.textContent || '').slice(0, 40).trim(),
          '비어 있다고만 하고 다음 할 일을 주지 않는다')
      }

      // R8: every screen has an h1.
      add('page-heading', !!document.querySelector('h1'), where, '어느 화면인지 제목이 없다')

      return out
    },
    { touch: viewport === 'tablet' || viewport === 'phone', where, viewport },
  )
}

test('디테일 감사 — 페르소나 · 화면 · 규칙', async ({ page }) => {
  test.setTimeout(1_200_000)
  const seen: Observation[] = []
  let checks = 0

  const record = (rows: { ok: boolean }[] & Observation[]) => {
    checks += rows.length
    seen.push(...rows.filter((r) => (r as unknown as { ok: boolean }).ok === false))
  }

  await signIn(page)

  for (const [viewport, size] of Object.entries(VIEWPORTS)) {
    await page.setViewportSize(size)
    for (const [path, label] of ROUTES) {
      await page.goto(path)
      await page.waitForTimeout(550)
      record((await auditScreen(page, label, viewport)) as never)
    }
  }

  // Artifact panels, opened from the gallery once its list has landed.
  await page.setViewportSize(VIEWPORTS.desktop)
  for (const tab of ['보고서', '슬라이드', '차트', '코드', 'HTML', '이미지']) {
    await page.goto('/artifacts')
    await page
      .locator('button.aspect-video')
      .first()
      .waitFor({ timeout: 15_000 })
      .catch(() => {})
    const tabButton = page.getByRole('tab', { name: new RegExp(`^${tab}`) })
    if ((await tabButton.count()) === 0) continue
    if (Number((await tabButton.innerText()).replace(/\D+/g, '') || 0) === 0) continue
    await tabButton.click()
    const card = page.locator('button.aspect-video').first()
    if ((await card.count()) === 0) continue
    await card.click()

    const dialog = page.getByRole('dialog')
    await dialog.waitFor({ timeout: 15_000 }).catch(() => {})
    const where = `아티팩트 · ${tab}`
    // Panel rules look at the artifact's own header, inside the dialog chrome.
    const body = dialog.locator('header').last()
    const hasIn = async (scope: typeof dialog, n: RegExp) =>
      (await scope.getByRole('button', { name: n }).count()) > 0

    checks++
    // A panel that opens wide offers "문서만 보기" as its expansion control.
    if (!(await hasIn(body, /넓게 보기|문서만 보기|전체 화면|크게 보기/))) {
      seen.push({ rule: 'panel-expand', subject: `${tab} 패널`, where, viewport: 'desktop',
        hurts: '좁은 패널에서만 읽어야 하고 넓힐 방법이 없다' })
    }
    checks++
    if (!(await hasIn(dialog, /^닫기$/))) {
      seen.push({ rule: 'panel-close', subject: `${tab} 패널`, where, viewport: 'desktop',
        hurts: '연 것을 되돌릴 방법이 화면에 없다' })
    }
    checks++
    await page.keyboard.press('Escape')
    // Closing is a transition; wait for it.
    const closed = await page
      .getByRole('dialog')
      .waitFor({ state: 'detached', timeout: 3_000 })
      .then(() => true)
      .catch(() => false)
    if (!closed) {
      seen.push({ rule: 'panel-escape', subject: `${tab} 패널`, where, viewport: 'desktop',
        hurts: '키보드만으로 빠져나올 수 없다' })
    }
  }

  // One defect per (rule, control), with every screen it showed up on and how many copies.
  const defects = new Map<string, {
    rule: string; subject: string; hurts: string; seen: number
    screens: Set<string>; viewports: Set<string>
  }>()
  for (const o of seen) {
    const key = `${o.rule}|${o.key ?? o.subject}`
    const hit = defects.get(key) ?? {
      rule: o.rule, subject: o.subject, hurts: o.hurts, seen: 0,
      screens: new Set(), viewports: new Set(),
    }
    hit.seen += 1
    hit.screens.add(o.where)
    hit.viewports.add(o.viewport)
    defects.set(key, hit)
  }

  const list = [...defects.values()]
    .map((d) => ({ ...d, screens: [...d.screens], viewports: [...d.viewports] }))
    .sort((a, b) => b.screens.length - a.screens.length || b.seen - a.seen)

  const byRule = list.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})

  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/detail-audit.json',
    JSON.stringify(
      { checks, observations: seen.length, defects: list.length, byRule, who: WHO, list },
      null, 2,
    ),
  )

  console.log(`checks=${checks} observations=${seen.length} defects=${list.length}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(1000)
})
