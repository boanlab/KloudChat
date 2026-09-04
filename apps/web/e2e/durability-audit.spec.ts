import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/** Durability sweep: offline navigation, concurrent edits, a 400-row list. Writes `audit/durability-audit.json`; never fails. */

interface Defect {
  rule: string
  subject: string
  where: string
  hurts: string
}

const SAYS_EMPTY = /없습니다|비어 있습니다/
/** Wording that blames the connection, not the workspace. */
const SAYS_OFFLINE = /연결|네트워크|오프라인|불러오지 못|다시 시도/

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

test('내구성 감사 — 끊길 때, 겹칠 때, 길어질 때', async ({ page, browser }) => {
  test.setTimeout(1_200_000)
  const defects: Defect[] = []
  let checks = 0
  const note = (rule: string, subject: string, where: string, hurts: string) =>
    defects.push({ rule, subject, where, hurts })

  await signIn(page)

  // R18: offline, a screen must not report an empty workspace.
  for (const [route, where, link, via] of [
    ['/artifacts', '아티팩트', '아티팩트', 'sidebar'],
    ['/memory', '메모리', '메모리', 'account'],
    ['/projects', '프로젝트', '프로젝트', 'sidebar'],
  ] as const) {
    // Loaded online, then cut off, then navigated inside the app: a cold offline `goto` measures Chrome.
    await page.goto('/')
    await page.waitForTimeout(600)
    await openSidebar(page)
    await page.context().setOffline(true)
    if (via === 'sidebar') {
      await page.getByRole('link', { name: link, exact: true }).first().click()
    } else {
      await page.getByRole('button', { name: '계정 메뉴' }).first().click()
      await page.getByRole('menuitem', { name: link, exact: true }).first().click()
    }
    await page.waitForTimeout(1800)
    const text = await page.evaluate(() => document.body.innerText).catch(() => '')
    checks++
    if (SAYS_EMPTY.test(text) && !SAYS_OFFLINE.test(text)) {
      note('offline-honesty', where, route, '연결이 끊긴 것을 "내용이 없다"고 말한다')
    }
    checks++
    if (!/다시 시도|새로고침/.test(text)) {
      note('offline-retry', where, route, '다시 시도할 방법이 화면에 없다')
    }
    await page.context().setOffline(false)
    await page.waitForTimeout(300)
  }
  await page.goto('/artifacts')
  await page.waitForTimeout(600)

  // R19: two tabs, one document; the second save must not silently overwrite.
  const report = await page.evaluate(
    async ([fn, body]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    [
      AS_USER,
      {
        kind: 'report',
        title: `동시편집 확인 ${Date.now().toString(36)}`,
        data: {
          kind: 'report',
          sections: [
            { id: 'c1', heading: '한 절', level: 1, status: 'done', content: '처음 내용입니다.' },
          ],
          sources: [],
          citationStyle: 'APA',
          wordCount: 10,
        },
      },
    ] as const,
  )

  if (report?.id) {
    const second = await browser.newPage()
    await signIn(second)

    const openReport = async (p: typeof page) => {
      await p.goto('/artifacts')
      await p.getByRole('tab', { name: /^보고서/ }).click()
      await p
        .locator('div')
        .filter({ has: p.getByText(report.title, { exact: true }) })
        .filter({ has: p.locator('button.aspect-video') })
        .last()
        .locator('button.aspect-video')
        .first()
        .click()
      await p.getByRole('dialog').waitFor({ timeout: 15_000 })
    }

    const rewrite = async (p: typeof page, body: string) => {
      await p.getByRole('button', { name: '원문 편집' }).click()
      await p.getByLabel('문서 원본').fill(`# ${report.title}\n\n## 한 절\n\n${body}\n`)
      await p.getByRole('button', { name: '저장', exact: true }).click()
      await p.waitForTimeout(1200)
    }

    await openReport(page)
    await openReport(second)
    await rewrite(page, '첫 번째 사람이 쓴 문장.')
    await rewrite(second, '두 번째 사람이 쓴 문장.')

    const text = await second.evaluate(() => document.body.innerText)
    checks++
    if (!/다른 곳에서|바뀌었|충돌|최신|다시 불러/.test(text)) {
      note(
        'concurrent-edit',
        '보고서 문서 편집',
        '/artifacts',
        '먼저 저장한 사람의 글이 아무 경고 없이 사라진다',
      )
    }
    await second.close()
    await page.evaluate(
      async ([fn, id]) =>
        await eval(fn as string)(`/api/artifacts/${id}`, { method: 'DELETE' }),
      [AS_USER, report.id] as const,
    )
  }

  // R20: a 400-row list, served synthetically.
  const many = Array.from({ length: 400 }, (_, i) => ({
    id: `${i.toString(16).padStart(32, '0')}`,
    kind: 'chat',
    title: `부하 확인 대화 ${i}`,
    projectId: null,
    agentId: null,
    model: 'local/glm-4.7-flash',
    artifactId: null,
    pinned: false,
    createdAt: new Date(2026, 0, 1).toISOString(),
    updatedAt: new Date(2026, 0, 1).toISOString(),
    messages: [],
    preview: '미리보기 문장입니다.',
    messageCount: 2,
  }))
  await page.route(/\/api\/sessions(\?|$)/, async (r) => {
    if (r.request().method() !== 'GET') return r.continue()
    await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(many) })
  })
  const started = Date.now()
  await page.goto('/history')
  await page.getByText('부하 확인 대화 0').first().waitFor({ timeout: 30_000 }).catch(() => {})
  const drawn = Date.now() - started
  const nodes = await page.evaluate(() => document.querySelectorAll('*').length)

  checks++
  if (drawn > 6_000) {
    note('long-list-speed', `${drawn}ms`, '/history', '목록이 길어지면 화면이 늦게 뜬다')
  }
  checks++
  // Paging keeps the DOM from growing with the workspace.
  if (nodes > 6_000) {
    note('long-list-dom', `${nodes} nodes`, '/history', '보이지 않는 행까지 전부 그린다')
  }
  await page.unroute(/\/api\/sessions(\?|$)/)

  const byRule = defects.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})
  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/durability-audit.json',
    JSON.stringify({ checks, defects: defects.length, byRule, defectList: defects }, null, 2),
  )
  console.log(`checks=${checks} defects=${defects.length} drawn=${drawn}ms nodes=${nodes}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
