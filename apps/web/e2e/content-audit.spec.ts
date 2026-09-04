import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { gotoWorkspace, signIn } from './helpers'

/** Content sweep: hostile pasted text, double submits, expired sessions, stale replies, Back.
 *  Writes `audit/content-audit.json`; never fails. */

interface Defect {
  rule: string
  subject: string
  where: string
  hurts: string
}

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

/** Things somebody will paste. */
const HOSTILE = {
  unbroken: `자료링크${'x'.repeat(240)}끝`,
  markup: '<script>window.__audit_ran = true</script><b>굵게</b> & "따옴표"',
  long: `${'아주 긴 제목입니다. '.repeat(30)}`,
}

function post(page: Page, body: unknown) {
  return page.evaluate(
    async ([fn, payload]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    [AS_USER, body] as const,
  )
}

test('내용 감사 — 붙여넣은 것, 두 번 누른 것, 만료된 것', async ({ page }) => {
  test.setTimeout(900_000)
  const defects: Defect[] = []
  let checks = 0
  const note = (rule: string, subject: string, where: string, hurts: string) =>
    defects.push({ rule, subject, where, hurts })

  await signIn(page)

  // R23: content the layout did not choose.
  const made: string[] = []
  for (const [kind, title] of Object.entries(HOSTILE)) {
    const row = await post(page, {
      kind: 'report',
      title,
      data: {
        kind: 'report',
        sections: [
          { id: 's1', heading: title, level: 1, status: 'done', content: `${title}\n\n${title}` },
        ],
        sources: [],
        citationStyle: 'APA',
        wordCount: 10,
      },
    })
    if ((row as { id?: string } | null)?.id) made.push((row as { id: string }).id)
    void kind
  }

  await page.goto('/artifacts')
  await page.locator('button.aspect-video').first().waitFor({ timeout: 20_000 }).catch(() => {})
  await page.waitForTimeout(800)

  for (const [width, size] of [
    ['desktop', { width: 1440, height: 900 }],
    ['phone', { width: 390, height: 844 }],
  ] as const) {
    await page.setViewportSize(size)
    await page.reload()
    await page.waitForTimeout(1200)
    checks++
    const overflow = await page.evaluate(() => {
      const de = document.documentElement
      return de.scrollWidth - de.clientWidth
    })
    if (overflow > 1) {
      note('content-overflow', `${overflow}px`, `아티팩트 · ${width}`,
        '붙여넣은 제목 하나가 화면을 옆으로 밀어낸다')
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  // Markup is text, checked through the prose renderer (thumbnails are `<pre>`, escaped by construction).
  await page.getByRole('tab', { name: /^보고서/ }).click()
  const markupCard = page
    .locator('div')
    .filter({ has: page.getByText(HOSTILE.markup, { exact: true }) })
    .filter({ has: page.locator('button.aspect-video') })
    .last()
  if ((await markupCard.count()) > 0) {
    await markupCard.locator('button.aspect-video').first().click()
    await page.getByRole('dialog').waitFor({ timeout: 15_000 }).catch(() => {})
    await page.waitForTimeout(1200)

    checks++
    const ran = await page.evaluate(
      () => (window as unknown as { __audit_ran?: boolean }).__audit_ran === true,
    )
    if (ran) note('content-escaping', '<script>', '보고서 패널', '붙여넣은 스크립트가 실행된다')

    checks++
    // `<b>` arrives as four characters.
    if ((await page.getByRole('dialog').locator('b').count()) > 0) {
      note('content-html', '<b>', '보고서 패널', '붙여넣은 HTML 이 서식으로 해석된다')
    }

    checks++
    if ((await page.getByRole('dialog').getByText('굵게', { exact: false }).count()) === 0) {
      note('content-visible', '굵게', '보고서 패널', '붙여넣은 내용이 화면에서 사라진다')
    }
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  }

  for (const id of made) {
    await page.evaluate(
      async ([fn, artifactId]) =>
        await eval(fn as string)(`/api/artifacts/${artifactId}`, { method: 'DELETE' }),
      [AS_USER, id] as const,
    )
  }

  // R23b: the same content in the narrow places (sidebar, agent grid, memory list).
  // The longest unbroken name the API takes: `name` is capped at 120 characters.
  const junk = `링크${'x'.repeat(112)}끝`
  const seeded: [string, string][] = []
  for (const [resource, body] of [
    ['skills', { name: junk, description: junk, whenToUse: junk, body: junk, kinds: [] }],
    ['memory', { name: junk, type: 'project', description: junk, body: junk }],
    ['agents', { name: junk, description: junk, model: '', systemPrompt: junk, kinds: ['chat'] }],
    ['projects', { name: junk, emoji: '📚', description: junk, instructions: junk }],
  ] as const) {
    const row = await page.evaluate(
      async ([fn, path, payload]) =>
        await eval(fn as string)(`/api/${path}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      [AS_USER, resource, body] as const,
    )
    const id = (row as { id?: string } | null)?.id
    if (id) seeded.push([resource, id])
    else {
      // Nothing planted, so this screen was not checked.
      note('seed-rejected', resource, `/${resource}`, '감사가 확인하지 못한 화면이 있다')
    }
  }

  for (const [route, where] of [
    ['/skills', '스킬'],
    ['/memory', '메모리'],
    ['/agents', '에이전트'],
    ['/projects', '프로젝트'],
  ] as const) {
    for (const [width, size] of [
      ['desktop', { width: 1440, height: 900 }],
      ['phone', { width: 390, height: 844 }],
    ] as const) {
      await page.setViewportSize(size)
      await page.goto(route)
      await page.waitForTimeout(1100)
      checks++
      const over = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      if (over > 1) {
        note('content-overflow', `${over}px`, `${where} · ${width}`,
          '이름 하나가 화면을 옆으로 밀어낸다')
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  for (const [resource, id] of seeded) {
    await page.evaluate(
      async ([fn, path]) => await eval(fn as string)(path, { method: 'DELETE' }),
      [AS_USER, `/api/${resource}/${id}`] as const,
    )
  }

  // R24: a double press must not create two rows.
  const name = `중복확인 ${Date.now().toString(36)}`
  await page.goto('/skills')
  await page.waitForTimeout(600)
  await page.getByRole('button', { name: '새 스킬' }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  const save = page.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$/ }).last()
  await save.click()
  await save.click({ timeout: 2_000 }).catch(() => {})
  await page.waitForTimeout(2500)

  checks++
  const copies = await page.evaluate(
    async ([fn, needle]) => {
      const rows = await eval(fn as string)('/api/skills')
      return (rows as { name: string }[] | null)?.filter((r) => r.name === needle).length ?? 0
    },
    [AS_USER, name] as const,
  )
  if (copies > 1) {
    note('double-submit', `스킬 ${copies}개`, '/skills', '한 번 만들려다 두 개가 생긴다')
  }
  await page.evaluate(
    async ([fn, needle]) => {
      const rows = (await eval(fn as string)('/api/skills')) as { id: string; name: string }[] | null
      for (const r of rows ?? []) {
        if (r.name === needle) {
          await eval(fn as string)(`/api/skills/${r.id}`, { method: 'DELETE' })
        }
      }
    },
    [AS_USER, name] as const,
  )

  // R25: an expired session must be said, not go quietly inert.
  await page.goto('/skills')
  await page.waitForTimeout(600)
  await page.route(/\/api\/auth\/refresh$/, (r) =>
    r.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"expired"}' }),
  )
  await page.route(/\/api\/skills(\?|$)/, (r) =>
    r.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"expired"}' }),
  )
  await page.reload()
  await page.waitForTimeout(3000)

  checks++
  const text = await page.evaluate(() => document.body.innerText)
  const saysSo = /로그인|세션|만료|다시/.test(text)
  if (!saysSo) {
    note('auth-expiry', '만료된 세션', '/skills', '아무 설명 없이 화면이 멈춘다')
  }
  await page.unroute(/\/api\/auth\/refresh$/)
  await page.unroute(/\/api\/skills(\?|$)/)

  // R26: a late reply for a screen already left must not overwrite the current list.
  await page.goto('/')
  await page.waitForTimeout(600)
  let firstSessions = true
  await page.route(/\/api\/sessions(\?|$)/, async (r) => {
    if (r.request().method() !== 'GET') return r.continue()
    // The first reply is held so a later one overtakes it.
    if (firstSessions) {
      firstSessions = false
      await new Promise((done) => setTimeout(done, 2500))
    }
    await r.continue().catch(() => {})
  })
  await gotoWorkspace(page, '대화 기록')
  await page.waitForTimeout(300)
  await page.getByRole('link', { name: '프로젝트', exact: true }).first().click()
  await page.waitForTimeout(300)
  await gotoWorkspace(page, '대화 기록')
  await page.waitForTimeout(3500)
  checks++
  // Scoped to main: the sidebar prints the same empty line.
  const historyText = await page.getByRole('main').innerText()
  if (/아직 대화가 없습니다/.test(historyText)) {
    note('stale-navigation', '대화 기록', '/history',
      '빠르게 오가면 늦게 온 응답이 화면을 비운다')
  }
  await page.unroute(/\/api\/sessions(\?|$)/)

  // R27: Back must not leave the app.
  await page.goto('/artifacts')
  await page.locator('button.aspect-video').first().waitFor({ timeout: 20_000 }).catch(() => {})
  if ((await page.locator('button.aspect-video').count()) > 0) {
    await page.locator('button.aspect-video').first().click()
    await page.getByRole('dialog').waitFor({ timeout: 15_000 }).catch(() => {})
    await page.goBack()
    await page.waitForTimeout(900)
    checks++
    // Dialog closed, or navigated within the workspace.
    if (!/\/artifacts|\/$/.test(new URL(page.url()).pathname + '/')) {
      note('back-button', '아티팩트 미리보기', '/artifacts',
        '뒤로가기가 화면 밖으로 데려간다')
    }
  }

  const byRule = defects.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})
  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/content-audit.json',
    JSON.stringify({ checks, defects: defects.length, byRule, defectList: defects }, null, 2),
  )
  console.log(`checks=${checks} defects=${defects.length}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
