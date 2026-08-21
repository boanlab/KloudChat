import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Keyboard sweep: where focus goes, whether it gets back out, and whether the
 * same action is named the same on every screen offering it.
 *
 * Discovery only — writes `audit/keyboard-audit.json` and never fails the run.
 */

interface Defect {
  rule: string
  subject: string
  screens: string[]
  hurts: string
}

const ROUTES: [string, string][] = [
  ['/', '홈'],
  ['/new/chat', '새 챗'],
  ['/new/report', '새 보고서'],
  ['/new/slides', '새 슬라이드'],
  ['/projects', '프로젝트'],
  ['/artifacts', '아티팩트'],
  ['/agents', '에이전트'],
  ['/skills', '스킬'],
  ['/memory', '메모리'],
  ['/history', '대화 기록'],
  ['/usage', '내 사용량'],
  ['/connectors', '커넥터'],
  ['/designs', '디자인'],
  ['/settings', '설정'],
  ['/settings/preferences', '설정 · 환경'],
  ['/settings/keys', '설정 · 키'],
  ['/admin/users', '관리자 · 사용자'],
  ['/admin/governance', '관리자 · 정책'],
]

/** Synonyms that must not both appear: 편집 beside 수정 reads as two actions. */
const SYNONYMS: [name: string, words: string[]][] = [
  ['삭제', ['삭제', '지우기', '제거', '없애기']],
  ['수정', ['수정', '편집', '고치기']],
  ['저장', ['저장', '적용']],
  ['만들기', ['만들기', '생성']],
  ['닫기', ['닫기']],
  ['공유', ['공유', '내보내기 링크']],
]

/**
 * Focus ring measured as a *difference*: each control is sampled at rest and
 * focused. "Has an outline" is the wrong question — a resting `shadow-sm`
 * answers yes and shows the keyboard user nothing.
 *
 * A real Tab comes first: Chrome withholds `:focus-visible` from programmatic
 * focus, so without it every control reads as unstyled.
 */
async function focusRingReport(page: Page) {
  await page.keyboard.press('Tab')
  return page.evaluate(() => {
    const skin = (el: Element) => {
      const s = getComputedStyle(el)
      return [s.outlineWidth, s.outlineStyle, s.outlineColor, s.boxShadow, s.borderColor,
        s.backgroundColor].join('|')
    }
    const out: { name: string; ring: boolean }[] = []
    const seen = new Set<string>()
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>(
        'button, a[href], input, textarea, select, [role="switch"], [role="tab"]',
      ),
    ).filter((el) => {
      const r = el.getBoundingClientRect()
      // A disabled control cannot take focus, and is not supposed to. Asking
      // it to show a focus ring is asking the wrong question.
      if ((el as HTMLButtonElement).disabled) return false
      return r.width > 0 && r.height > 0
    })
    for (const el of controls) {
      const name = (
        el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.tagName
      ).replace(/\s+/g, ' ').trim().slice(0, 40)
      if (seen.has(name)) continue
      seen.add(name)
      // The Tab that switched the browser into keyboard modality also focused
      // something. Measuring that element's "resting" look while it is still
      // focused compares it against itself, and it reads as having no ring.
      ;(document.activeElement as HTMLElement | null)?.blur()
      const before = skin(el)
      el.focus()
      const after = skin(el)
      el.blur()
      out.push({ name, ring: before !== after })
    }
    return out
  })
}

test('키보드·일관성 감사', async ({ page }) => {
  test.setTimeout(1_200_000)
  const defects = new Map<string, Defect>()
  let checks = 0
  const note = (rule: string, subject: string, where: string, hurts: string) => {
    const key = `${rule}|${subject}`
    const hit = defects.get(key) ?? { rule, subject, screens: [], hurts }
    if (!hit.screens.includes(where)) hit.screens.push(where)
    defects.set(key, hit)
  }

  await signIn(page)

  /** Every button label seen anywhere, for the consistency pass. */
  const labels = new Map<string, Set<string>>()

  for (const [path, where] of ROUTES) {
    await page.goto(path)
    await page.waitForTimeout(500)

    // ── R9: focus has to be visible ──────────────────────────────────
    for (const c of await focusRingReport(page)) {
      checks++
      if (!c.ring) note('focus-ring', c.name, where, '키보드로 어디에 있는지 보이지 않는다')
    }

    // ── R13: one action, one word ────────────────────────────────────
    for (const text of await page.evaluate(() =>
      Array.from(document.querySelectorAll('button'))
        .filter((b) => {
          const r = b.getBoundingClientRect()
          return r.width > 0 && r.height > 0
        })
        .map((b) => (b.innerText || '').replace(/\s+/g, ' ').trim())
        .filter(Boolean),
    )) {
      const set = labels.get(text) ?? new Set<string>()
      set.add(where)
      labels.set(text, set)
    }
  }

  for (const [group, words] of SYNONYMS) {
    const used = words.filter((w) => [...labels.keys()].some((l) => l === w))
    checks++
    if (used.length > 1) {
      note(
        'term-consistency',
        `${group}: ${used.join(' / ')}`,
        [...new Set(used.flatMap((w) => [...(labels.get(w) ?? [])]))].join(', '),
        '같은 일을 화면마다 다른 말로 부른다',
      )
    }
  }

  /* ── R10–R12: modals and the keyboard ───────────────────────────────
     A dialog that does not take focus is a dialog a keyboard user has to Tab
     *into* from wherever they were, past everything behind it — and Tab will
     walk them straight back out again. */
  const MODALS: [route: string, open: string, where: string][] = [
    ['/agents', '새 에이전트', '에이전트 · 새로 만들기'],
    ['/skills', '새 스킬', '스킬 · 새로 만들기'],
    ['/memory', '새 메모리', '메모리 · 새로 만들기'],
    ['/projects', '새 프로젝트', '프로젝트 · 새로 만들기'],
  ]
  for (const [route, open, where] of MODALS) {
    await page.goto(route)
    await page.waitForTimeout(400)
    const trigger = page.getByRole('button', { name: open }).first()
    if ((await trigger.count()) === 0) continue
    await trigger.click()
    const dialog = page.getByRole('dialog')
    if ((await dialog.count()) === 0) continue
    await page.waitForTimeout(300)

    // R10 — focus moves into the dialog.
    checks++
    const inside = await page.evaluate(() => {
      const d = document.querySelector('[role="dialog"]')
      return !!d && !!document.activeElement && d.contains(document.activeElement)
    })
    if (!inside) note('modal-focus', open, where, '열려도 초점이 뒤 화면에 남는다')

    // R11 — and stays there. Twenty-five stops is more than any of these
    // dialogs holds, so a loop that never leaves is a loop that is trapped.
    checks++
    let escaped = false
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press('Tab')
      const still = await page.evaluate(() => {
        const d = document.querySelector('[role="dialog"]')
        return !!d && !!document.activeElement && d.contains(document.activeElement)
      })
      if (!still) {
        escaped = true
        break
      }
    }
    if (escaped) note('modal-trap', open, where, 'Tab 이 뒤 화면으로 새어 나간다')

    // R12 — closing gives focus back to what opened it.
    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
    checks++
    const restored = await page.evaluate(
      (name) => (document.activeElement as HTMLElement | null)?.innerText?.includes(name) ?? false,
      open,
    )
    if (!restored) note('modal-restore', open, where, '닫으면 초점이 문서 맨 앞으로 튄다')
  }

  /* ── R14: destroying something asks first ───────────────────────────
     Run against rows created here and thrown away, so a real click on a real
     delete is safe. Nothing on the server restores a skill, an agent or a
     memory — so if the click goes straight through, the only copy is gone. */
  const THROWAWAY: [route: string, open: string, nameField: string, where: string][] = [
    ['/skills', '새 스킬', '이름', '스킬'],
    ['/memory', '새 메모리', '이름', '메모리'],
  ]
  for (const [route, open, nameField, where] of THROWAWAY) {
    const name = `zz-throwaway-${where}-${Date.now().toString(36)}`
    await page.goto(route)
    await page.waitForTimeout(400)
    await page.getByRole('button', { name: open }).first().click()
    const form = page.getByRole('dialog')
    await form.getByLabel(new RegExp(nameField)).first().fill(name)
    await form.getByRole('button', { name: /^저장$|^추가$|^만들기$/ }).last().click()
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 20_000 })

    const del = page.getByRole('button', { name: `${name} 삭제` })
    if ((await del.count()) === 0) continue
    await del.click()
    await page.waitForTimeout(400)
    checks++
    const asked = (await page.getByRole('dialog').count()) > 0
    if (!asked) {
      note('destructive-confirm', `${where} 삭제`, where, '한 번의 오클릭으로 되돌릴 수 없이 사라진다')
    } else {
      // Confirmed here so the throwaway does not pile up in the workspace.
      await page.getByRole('dialog').getByRole('button', { name: '삭제' }).click()
      await page.waitForTimeout(600)
    }
    // Whatever happened, do not leave it behind. Retried and then asserted:
    // one unchecked attempt left rows on the instance, and a memory left behind
    // is `global` scope — it joins every later turn's prompt.
    for (let attempt = 0; attempt < 3; attempt++) {
      if ((await page.getByText(name).count()) === 0) break
      await del.first().click().catch(() => {})
      await page
        .getByRole('dialog')
        .getByRole('button', { name: '삭제' })
        .click()
        .catch(() => {})
      await page.waitForTimeout(600)
    }
    await expect(page.getByText(name), `${name} 를 지우지 못했습니다`).toHaveCount(0, {
      timeout: 10_000,
    })
  }

  const list = [...defects.values()].sort((a, b) => b.screens.length - a.screens.length)
  const byRule = list.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})

  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/keyboard-audit.json',
    JSON.stringify({ checks, defects: list.length, byRule, list }, null, 2),
  )
  console.log(`checks=${checks} defects=${list.length}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
