import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/** Mobile sweep at 390px, portrait and landscape: drawer, dialogs, artifact panel, composer.
 *  Writes `audit/mobile-audit.json`; never fails. */

interface Defect {
  rule: string
  subject: string
  where: string
  hurts: string
}

const PHONE = { width: 390, height: 844 }
/** Landscape: height is the scarce dimension. */
const LANDSCAPE = { width: 844, height: 390 }

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init || {}),
    headers: { ...((init || {}).headers || {}), Authorization: 'Bearer ' + accessToken },
  })
  if (!r.ok || r.status === 204) return null
  return await r.json()
}`

/** An element's box. */
async function boxOf(page: Page, selector: string) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x) }
  }, selector)
}

test('모바일 감사 — 390px 에서 일이 끝나는가', async ({ page }) => {
  test.setTimeout(900_000)
  const defects: Defect[] = []
  let checks = 0
  const note = (rule: string, subject: string, where: string, hurts: string) =>
    defects.push({ rule, subject, where, hurts })

  await page.setViewportSize(PHONE)
  await signIn(page)

  // R28: the drawer, and the way back out.
  await page.goto('/')
  await page.waitForTimeout(700)
  const toggle = page.getByRole('button', { name: '사이드바 토글' })
  checks++
  if ((await toggle.count()) === 0) {
    note('drawer-open', '사이드바', '/', '좁은 화면에서 사이드바를 열 방법이 없다')
  } else {
    await toggle.click()
    await page.waitForTimeout(500)

    checks++
    // A dismiss that is not a link.
    const scrim = page.getByRole('button', { name: '사이드바 닫기' })
    if ((await scrim.count()) === 0) {
      note('drawer-dismiss', '사이드바', '/', '링크를 누르지 않고는 서랍을 닫을 수 없다')
    } else {
      // Tap the strip beside the drawer, and measure how much of it there is.
      checks++
      const drawer = await boxOf(page, '.absolute.inset-y-0.left-0.z-40')
      const strip = PHONE.width - (drawer?.w ?? 0)
      if (strip < 64) {
        note('drawer-strip', `${strip}px`, '/',
          '서랍 옆에 남는 공간이 좁아 닫으려다 서랍을 누른다')
      }
      await page.mouse.click(PHONE.width - Math.max(16, Math.round(strip / 2)), 400)
      await page.waitForTimeout(400)
      checks++
      if ((await scrim.count()) > 0) {
        note('drawer-close', '사이드바', '/', '바깥을 눌러도 서랍이 남는다')
      }
    }

    // Navigating from it must dismiss it.
    if ((await page.getByRole('button', { name: '사이드바 닫기' }).count()) === 0) {
      await toggle.click()
      await page.waitForTimeout(400)
    }
    await page.getByRole('link', { name: '프로젝트', exact: true }).first().click()
    await page.waitForTimeout(700)
    checks++
    if ((await page.getByRole('button', { name: '사이드바 닫기' }).count()) > 0) {
      note('drawer-after-nav', '사이드바', '/projects', '이동한 뒤에도 서랍이 화면을 덮고 있다')
    }
  }

  // R28b: every screen has the sidebar toggle.
  await page.setViewportSize(PHONE)
  for (const [route, where] of [
    ['/', '홈'],
    ['/new/chat', '새 챗'],
    ['/artifacts', '아티팩트'],
    ['/history', '대화 기록'],
    ['/usage', '내 사용량'],
    ['/agent-setup', '에이전트 설정'],
    ['/connectors', '커넥터'],
    ['/settings', '설정'],
    ['/admin/system', '관리자 · 시스템'],
    ['/admin/users', '관리자 · 사용자'],
  ] as const) {
    await page.goto(route)
    await page.waitForTimeout(700)
    checks++
    if ((await page.getByRole('button', { name: '사이드바 토글' }).count()) === 0) {
      note('no-way-out', where, route, '좁은 화면에서 이 화면을 벗어날 방법이 없다')
    }
  }

  // R29: a dialog taller than the phone still reaches its save button.
  for (const [route, open, where] of [
    ['/agents', '새 에이전트', '에이전트'],
    ['/memory', '새 메모리', '메모리'],
  ] as const) {
    await page.goto(route)
    await page.waitForTimeout(700)
    const trigger = page.getByRole('button', { name: open }).first()
    if ((await trigger.count()) === 0) continue
    await trigger.click()
    await page.getByRole('dialog').waitFor({ timeout: 10_000 }).catch(() => {})
    await page.waitForTimeout(400)

    checks++
    const save = page.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$/ }).last()
    const reachable = await save
      .scrollIntoViewIfNeeded({ timeout: 3_000 })
      .then(() => save.isVisible())
      .catch(() => false)
    if (!reachable) {
      note('dialog-reach', `${where} 만들기`, route, '저장 버튼에 닿을 수 없다')
    }

    checks++
    const over = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    if (over > 1) {
      note('dialog-overflow', `${where} · ${over}px`, route, '대화상자가 화면을 옆으로 밀어낸다')
    }
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  }

  // R30: the artifact panel takes the whole screen; its contents must fit and it must be closable.
  const deck = await page.evaluate(
    async ([fn, body]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    [
      AS_USER,
      {
        kind: 'deck',
        title: `모바일 확인 덱 ${Date.now().toString(36)}`,
        data: {
          kind: 'deck',
          theme: '기본',
          slides: [
            { id: 'm1', layout: 'title', title: '모바일에서 보는 덱', body: '390px', accent: '#5b5bd6' },
            { id: 'm2', layout: 'bullets', title: '두 번째 장', bullets: ['첫 항목', '둘째 항목'] },
          ],
        },
      },
    ] as const,
  )

  if ((deck as { id?: string } | null)?.id) {
    await page.goto('/artifacts')
    await page.locator('button.aspect-video').first().waitFor({ timeout: 20_000 }).catch(() => {})
    await page.getByRole('tab', { name: /^슬라이드/ }).click()
    await page.waitForTimeout(600)
    const card = page.locator('button.aspect-video').first()
    if ((await card.count()) > 0) {
      await card.click()
      await page.getByRole('dialog').waitFor({ timeout: 15_000 }).catch(() => {})
      await page.waitForTimeout(700)

      checks++
      // The stage: the widest visible slide rectangle (thumbnails share the class).
      const stage = await page.evaluate(() => {
        const boxes = Array.from(document.querySelectorAll('[role="dialog"] .aspect-video'))
          .map((el) => el.getBoundingClientRect())
          .filter((r) => r.width > 0)
        if (boxes.length === 0) return null
        const widest = boxes.reduce((a, b) => (a.width >= b.width ? a : b))
        return { w: Math.round(widest.width), h: Math.round(widest.height), x: Math.round(widest.x) }
      })
      if (stage && stage.w < 180) {
        note('panel-stage', `${stage.w}px`, '슬라이드 패널',
          '레일이 자리를 다 먹어 슬라이드가 손톱만 하게 남는다')
      }

      checks++
      const over = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      if (over > 1) {
        note('panel-overflow', `${over}px`, '슬라이드 패널', '패널이 화면을 옆으로 밀어낸다')
      }

      checks++
      // Overflow inside a header row: flex squeezes children instead of scrolling the page.
      const crushed = await page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('[role="dialog"] header'))
        return rows
          .map((el) => ({
            over: el.scrollWidth - el.clientWidth,
            text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').slice(0, 30),
          }))
          .filter((r) => r.over > 1)
      })
      for (const row of crushed) {
        note('row-overflow', `${row.text} · ${row.over}px`, '슬라이드 패널',
          '머리말이 화면보다 넓어 글자가 한 자씩 쪼개진다')
      }

      checks++
      if ((await page.getByRole('dialog').getByRole('button', { name: '닫기' }).count()) === 0) {
        note('panel-exit', '슬라이드 패널', '/artifacts', '전체를 덮은 패널에서 나갈 버튼이 없다')
      }
      await page.keyboard.press('Escape')
      await page.waitForTimeout(400)
    }

    await page.evaluate(
      async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`, { method: 'DELETE' }),
      [AS_USER, (deck as { id: string }).id] as const,
    )
  }

  // R31: the composer.
  await page.goto('/new/chat')
  await page.waitForTimeout(700)
  checks++
  const box = await boxOf(page, 'textarea[aria-label="프롬프트 입력"]')
  if (!box || box.w < 240) {
    note('composer-width', `${box?.w ?? 0}px`, '/new/chat', '입력 칸이 문장을 담기에 좁다')
  }
  checks++
  const send = page.getByLabel('전송')
  if ((await send.count()) === 0 || !(await send.isVisible())) {
    note('composer-send', '전송', '/new/chat', '보내기 버튼이 화면 밖에 있다')
  }
  checks++
  await page.getByLabel('프롬프트 입력').fill('모바일에서 긴 문장을 입력해 봅니다. '.repeat(6))
  await page.waitForTimeout(400)
  const overTyping = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  if (overTyping > 1) {
    note('composer-overflow', `${overTyping}px`, '/new/chat', '길게 쓰면 화면이 옆으로 밀린다')
  }

  // R32: landscape.
  await page.setViewportSize(LANDSCAPE)
  for (const [route, open, where] of [
    ['/agents', '새 에이전트', '에이전트'],
    ['/memory', '새 메모리', '메모리'],
  ] as const) {
    await page.goto(route)
    await page.waitForTimeout(700)
    const trigger = page.getByRole('button', { name: open }).first()
    if ((await trigger.count()) === 0) continue
    await trigger.click()
    await page.getByRole('dialog').waitFor({ timeout: 10_000 }).catch(() => {})
    await page.waitForTimeout(400)

    checks++
    // Fits, or scrolls.
    const fits = await page.evaluate(() => {
      const d = document.querySelector('[role="dialog"]')
      if (!d) return true
      const r = d.getBoundingClientRect()
      const scroller = d.parentElement
      const scrolls = !!scroller && scroller.scrollHeight > scroller.clientHeight + 1
      return r.height <= window.innerHeight + 1 || scrolls
    })
    if (!fits) {
      note('landscape-dialog', where, route, '가로 화면에서 대화상자 아래가 잘린다')
    }

    checks++
    const save = page.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$/ }).last()
    const reachable = await save
      .scrollIntoViewIfNeeded({ timeout: 3_000 })
      .then(() => save.isVisible())
      .catch(() => false)
    if (!reachable) {
      note('landscape-reach', `${where} 만들기`, route, '가로에서 저장 버튼에 닿을 수 없다')
    }
    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
  }

  await page.goto('/new/chat')
  await page.waitForTimeout(700)
  checks++
  const land = await boxOf(page, 'textarea[aria-label="프롬프트 입력"]')
  if (!land || land.h < 24) {
    note('landscape-composer', `${land?.h ?? 0}px`, '/new/chat', '가로에서 입력 칸이 한 줄도 안 남는다')
  }
  checks++
  if (!(await page.getByLabel('전송').isVisible().catch(() => false))) {
    note('landscape-send', '전송', '/new/chat', '가로에서 보내기 버튼이 화면 밖이다')
  }

  const byRule = defects.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})
  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/mobile-audit.json',
    JSON.stringify({ checks, defects: defects.length, byRule, defectList: defects }, null, 2),
  )
  console.log(`checks=${checks} defects=${defects.length}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
