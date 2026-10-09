import { expect, test, type Page, type Request } from '@playwright/test'
import { hubFixture, mySkills } from './customize-fixture'

/**
 * 「스킬 업로드」: 추가 ▾ offers it on the 스킬 tab; dropped files get a dry run whose findings
 * show per file; a blocked file keeps 업로드 off until it is taken out; the upload posts the
 * files once more without `dryRun` and opens the new skill. The skill's ⋯ menu exports SKILL.md.
 * Fixture data only; nothing reaches a server.
 */

test.use({ serviceWorkers: 'block' })

const shots = process.env.SKILL_UPLOAD_SHOTS ?? ''
const made = mySkills[1]

const good = {
  filename: 'meeting.skill', ok: true, name: '회의록 정리', description: '회의 메모를 결정·할 일·미정으로 나눕니다.',
  whenToUse: '회의 메모를 결정·할 일·미정으로 나눕니다.', kinds: ['chat'], requiredTools: ['web_search'], version: '1.2.0',
  bodyChars: 420, estimatedTokens: 180, kept: ['SKILL.md', 'resources/guide.md'],
  excluded: [{ path: 'scripts/run.py', reason: 'script' }, { path: 'logo.png', reason: 'binary' }],
  findings: [
    { severity: 'warn', code: 'scripts_excluded', message: '스크립트는 실행하지 않아 제외했어요.', file: 'meeting.skill', line: null },
    { severity: 'warn', code: 'instruction_override', message: '이전 지시를 무시하라는 식의 문장이 있습니다.', file: 'meeting.skill', line: 7 },
  ],
}
const bad = {
  filename: 'notes.zip', ok: false, name: '', description: '', whenToUse: '', kinds: ['chat'], requiredTools: [], version: '1.0.0',
  bodyChars: 0, estimatedTokens: 0, kept: [], excluded: [],
  findings: [{ severity: 'block', code: 'no_skill_md', message: '압축 안에 SKILL.md 가 없습니다.', file: 'notes.zip', line: null }],
}

async function mockImport(page: Page) {
  const posts: { url: URL; body: string }[] = []
  await page.route('**/api/skills/import**', async (route) => {
    const request: Request = route.request()
    const url = new URL(request.url())
    const body = request.postDataBuffer()?.toString('latin1') ?? ''
    posts.push({ url, body })
    const names = [...body.matchAll(/filename="([^"]+)"/g)].map((m) => m[1])
    const items = names.map((name) => (name === 'notes.zip' ? bad : { ...good, filename: name }))
    if (url.searchParams.get('dryRun')) return route.fulfill({ json: { dryRun: true, items, created: [] } })
    return route.fulfill({ json: { dryRun: false, items, created: [made] } })
  })
  await page.route('**/api/skills/*/export', (route) =>
    route.fulfill({ status: 200, headers: { 'Content-Type': 'text/markdown; charset=utf-8' }, body: '---\nname: x\ndescription: y\n---\nbody\n' }),
  )
  return posts
}

async function drop(page: Page, name: string) {
  const transfer = await page.evaluateHandle((fileName) => {
    const data = new DataTransfer()
    data.items.add(new File(['PK fixture'], fileName, { type: 'application/zip' }))
    return data
  }, name)
  await page.getByTestId('skill-drop').dispatchEvent('dragover', { dataTransfer: transfer })
  await page.getByTestId('skill-drop').dispatchEvent('drop', { dataTransfer: transfer })
}

test('upload: menu, dry-run findings, a blocked file holds the upload, commit opens the skill', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const posts = await mockImport(page)
  const phone = testInfo.project.name === 'phone'

  await page.goto('/customize?tab=skills&view=mine')
  await expect(page.getByTestId('item-card').first()).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '추가', exact: true }).filter({ visible: true }).first().click()
  await expect(page.getByRole('menuitem', { name: '스킬 만들기' })).toBeVisible()
  await page.getByRole('menuitem', { name: '스킬 업로드' }).click()
  await expect(page).toHaveURL(/\/customize\/skills\/upload$/)
  await expect(page.getByRole('heading', { level: 1, name: '스킬 업로드' })).toBeVisible()
  await expect(page.getByText('.zip 또는 .skill 파일에는 SKILL.md 가 들어 있어야 합니다.')).toBeVisible()
  await expect(page.getByText('업로드할 때 실행')).toBeVisible()
  const upload = page.getByRole('button', { name: '업로드', exact: true })
  await expect(upload).toBeDisabled()

  await drop(page, 'meeting.skill')
  const card = page.getByTestId('skill-preview').first()
  await expect(card.getByText('회의록 정리')).toBeVisible()
  await expect(card.getByText('스크립트는 실행하지 않아 제외했어요.')).toBeVisible()
  await expect(card.getByText('7번째 줄')).toBeVisible()
  await expect(card.locator('[data-severity="warn"]')).toHaveCount(2)
  await expect(card.getByText('scripts/run.py')).toBeVisible()
  await expect(card.getByText('스크립트 · 실행하지 않음')).toBeVisible()
  await expect(upload).toBeEnabled()
  expect(posts[0].url.searchParams.get('dryRun')).toBe('true')

  // A second file, picked: the whole list is checked again and the blocked one holds the upload.
  await page.getByLabel('스킬 파일 고르기').setInputFiles({ name: 'notes.zip', mimeType: 'application/zip', buffer: Buffer.from('PK') })
  await expect(page.getByTestId('skill-preview')).toHaveCount(2)
  const blocked = page.getByTestId('skill-preview').nth(1)
  await expect(blocked.locator('[data-severity="block"]')).toContainText('압축 안에 SKILL.md 가 없습니다.')
  await expect(blocked.getByText('올릴 수 없음')).toBeVisible()
  await expect(upload).toBeDisabled()
  await expect(page.getByText('막힌 파일을 빼야 올릴 수 있습니다.')).toBeVisible()
  expect(posts).toHaveLength(2)
  expect(posts[1].body).toContain('filename="meeting.skill"')
  expect(posts[1].body).toContain('filename="notes.zip"')

  if (shots) await page.screenshot({ path: `${shots}/${testInfo.project.name}-upload.png`, fullPage: true })

  await blocked.getByRole('button', { name: 'notes.zip 빼기' }).click()
  await expect(page.getByTestId('skill-preview')).toHaveCount(1)
  await expect(upload).toBeEnabled()
  // Taking a file out asks nothing more of the server.
  expect(posts).toHaveLength(2)

  await page.getByRole('checkbox', { name: /초안으로 올리기/ }).check()
  await upload.click()
  await expect(page).toHaveURL(new RegExp(`/customize/skills/${made.id}$`))
  await expect(page.getByText(`「${made.name}」 스킬을 올렸습니다.`)).toBeVisible()
  const commit = posts[2]
  expect(commit.url.searchParams.get('dryRun')).toBeNull()
  expect(commit.url.searchParams.get('draft')).toBe('true')
  expect(commit.body).toContain('filename="meeting.skill"')
  expect(commit.body).not.toContain('filename="notes.zip"')

  // The skill's ⋯ menu exports it as SKILL.md.
  await page.getByRole('button', { name: '더 보기' }).filter({ visible: true }).first().click()
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: '내보내기 (SKILL.md)' }).click()
  expect((await download).suggestedFilename()).toBe(`${made.name}-SKILL.md`)

  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
  if (phone) {
    const width = await page.evaluate(() => document.documentElement.scrollWidth)
    expect(width).toBeLessThanOrEqual(390)
  }
})

test('hub header: filter and sort stay on the tab row; the search box wraps first', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'phone', 'the phone header is its own layout')
  await hubFixture(page, testInfo)
  for (const width of [1440, 1180, 1000]) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/customize?tab=skills&view=explore')
    await expect(page.getByRole('tab', { name: '스킬' })).toBeVisible({ timeout: 15_000 })
    const tabTop = (await page.getByRole('tab', { name: '스킬' }).boundingBox())!.y
    const sortBox = (await page.getByRole('button', { name: /^정렬/ }).filter({ visible: true }).first().boundingBox())!
    expect(Math.abs(sortBox.y - tabTop), `sort on the tab row at ${width}px`).toBeLessThan(12)
    if (shots) await page.screenshot({ path: `${shots}/header-${width}.png`, clip: { x: 0, y: 0, width, height: 260 } })
  }
})
