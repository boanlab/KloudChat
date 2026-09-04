import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'
import { personas } from './personas'

/**
 * A sweep for missing detail, not for missing features.
 *
 * `personas.spec.ts` asks whether a persona *can* do the job. This asks what
 * doing it feels like: whether the control that does it has a name, whether a
 * panel that opened can be closed, whether the thing revealed by hovering
 * exists at all for the persona holding a tablet.
 *
 * Observations are counted per screen; **defects are counted per control**. The
 * sidebar's row menu appears on all twenty-three screens at all three widths,
 * and reporting it seventy times would say the product has seventy problems
 * when it has one. Each defect therefore carries the list of screens it was
 * seen on instead of a copy per screen, and the same holds down a list: one
 * delete button rendered once per row is one defect, not one per row. What
 * counts as the same control is `identity`.
 *
 * It is a discovery tool and never fails the run — a red suite would only make
 * the notes harder to read. Everything goes to `audit/detail-audit.json`.
 */

interface Observation {
  rule: string
  /** The control, named the way the persona would point at it. */
  subject: string
  /**
   * Which control this is, as opposed to which copy of it — see `identity`.
   * Absent where the subject already names one thing, as it does for a panel.
   */
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
  /**
   * A phone. No persona in `personas.ts` works at this width — but the layout
   * plainly expects it (`useNarrowLayout`, `sm:` throughout), and a workspace
   * people sign into with a university account is one they will open on a
   * phone to check something between classes. Measured rather than assumed.
   */
  phone: { width: 390, height: 844 },
} as const

/** Who works at each width, so a finding can say whose day it spoils. */
const WHO: Record<string, string> = Object.fromEntries(
  (Object.keys(VIEWPORTS) as (keyof typeof VIEWPORTS)[]).map((v) => [
    v,
    personas.filter((p) => p.viewport === v).map((p) => `${p.name}(${p.role})`).join(', ') || '—',
  ]),
)

/**
 * Every rule that can be decided by looking at one rendered screen.
 *
 * Run inside the page so a screen is one round trip; a per-element Playwright
 * query would turn a 69-screen sweep into an hour.
 */
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

      /** A stable way to say which control this was, across screens. */
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
        // Nameless: fall back to where it sits, which is the only handle left.
        const region = el.closest('[aria-label]')?.getAttribute('aria-label')
        return `<${el.tagName.toLowerCase()}${region ? ` in ${region}` : ''}>`
      }

      /**
       * Which control this is, as opposed to which copy of it.
       *
       * The label of a control inside a list row is the row's own name, so the
       * one delete button in a list of thirty designs arrives under thirty
       * different labels and is filed as thirty defects — the same miscount the
       * per-screen dedupe already exists to prevent, one level down, and one
       * that makes the headline number a function of how much seed data the
       * database happens to hold. What the row shares is what the JSX wrote:
       * the tag and the class list, plus the icon, which is the only thing
       * telling the three identically styled ghost buttons of a reorder row
       * apart. Two genuinely different controls that match on all three would
       * be merged, and they would also take the same one-line fix.
       */
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

        // R1 — a control with no name is one a screen reader cannot announce.
        // For inputs the bar is a real label, not the placeholder: placeholders
        // vanish the moment you type into the field they were explaining.
        const named = isInput
          ? !!(aria || el.getAttribute('id') && document.querySelector(`label[for="${el.getAttribute('id')}"]`) || el.closest('label'))
          : !!(text || aria)
        add('name', named, id, '읽어 주지 못하고, 무엇을 넣는 칸인지 알 수 없다', who)

        // R2 — icon-only controls need a tooltip. An icon is a guess until
        // something spells it out, and hovering is where people look first.
        if (!text && !!el.querySelector('svg')) {
          add('tooltip', !!title, id, '아이콘만 보고 뜻을 짐작해야 한다', who)
        }

        // R3 — revealed on hover. There is no hover on a tablet, so for the
        // persona holding one the control does not exist at all.
        if (touch) {
          let hidden = false
          for (let cur: Element | null = el, i = 0; cur && i < 4; cur = cur.parentElement, i++) {
            const cls = cur.getAttribute('class') || ''
            if (/opacity-0/.test(cls) && /group-hover|focus-within/.test(cls)) hidden = true
          }
          add('touch-reach', !hidden, id, '터치 기기에서는 존재하지 않는 버튼이다', who)

          // R4 — a target smaller than a fingertip.
          // An input wrapped in a label is as big as the label — that is what
          // the finger lands on, and it is the honest measurement.
          const target = (el.closest('label') as Element | null) ?? el
          const r = target.getBoundingClientRect()
          const tiny = Math.min(r.width, r.height) < 32 && el.tagName !== 'A'
          add('tap-size', !tiny, `${id} · ${Math.round(r.width)}×${Math.round(r.height)}`,
            '손가락으로 정확히 누르기 어렵다', who)
        }

        // R5 — disabled with no reason on screen. "Why can't I click this"
        // has no answer anywhere.
        if ((el as HTMLButtonElement).disabled) {
          add('disabled-reason', !!title, id, '왜 못 누르는지 알 수 없다', who)
        }
      }

      // R6 — the page must not scroll sideways.
      const de = document.documentElement
      add('no-h-scroll', de.scrollWidth <= de.clientWidth + 1,
        `${where} ${de.scrollWidth}>${de.clientWidth}`, '가로로 밀어야 내용이 보인다')

      // R7 — an empty screen has to offer the thing that fills it.
      for (const el of Array.from(document.querySelectorAll('p')).filter(
        (e) =>
          visible(e) &&
          /없습니다|비어 있습니다/.test(e.textContent || '') &&
          // A document thumbnail can contain an ordinary sentence such as
          // 「이 장애물은 없습니다」.  It is content inside the button that
          // opens the artifact, not the product announcing an empty state.
          !e.closest('button') &&
          // A row that says "no messages yet" inside a card you can click into
          // is a subtitle, not an empty screen. The card is the action.
          !e.closest('[class*="cursor-pointer"]'),
      )) {
        const box = el.closest('div')?.parentElement
        add('empty-action', !!box?.querySelector('button, a[href]'),
          (el.textContent || '').slice(0, 40).trim(),
          '비어 있다고만 하고 다음 할 일을 주지 않는다')
      }

      // R8 — every screen names itself.
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

  /* ── artifact panels ────────────────────────────────────────────────
     The surface the request named. Opened from the gallery, which loads
     asynchronously — querying the tab before the list lands reads every kind
     as "none of these exist" and skips the whole section. */
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
    // The gallery's own dialog chrome carries a close button, so panel rules
    // look *inside* it — at the artifact's own header — or every kind would
    // report a close button it does not have.
    const body = dialog.locator('header').last()
    const hasIn = async (scope: typeof dialog, n: RegExp) =>
      (await scope.getByRole('button', { name: n }).count()) > 0

    checks++
    // A panel that already opens wide offers the next step as "문서만 보기".
    // That is an expansion control too; checking only the narrow-state label
    // reports a working three-position control as absent.
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
    // Waited for, not sampled: closing is a transition, and reading the DOM
    // once at 300ms reports a dialog on its way out as one that would not go.
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

  /* ── the panel in a live session ────────────────────────────────────
     Closing it there is only safe if it can be opened again. */
  const session = await page.evaluate(async () => {
    const r = await fetch('/api/sessions', { headers: {} })
    return r.ok ? await r.json() : null
  }).catch(() => null)
  void session

  /* ── dedupe ─────────────────────────────────────────────────────────
     One defect per (rule, control), carrying every screen it showed up on and
     how many copies of it were counted, so collapsing a thirty-row list does
     not quietly turn a wide problem into a narrow-looking one. */
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
