import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Reading a design system out of a document somebody already has.
 *
 * The four colours and the paragraph of style rules are the part nobody types
 * from scratch, so this is the path that decides whether design systems get
 * used at all. What it must not do is save on somebody's behalf: the draft is
 * one model's reading of a document, and the person who owns that document is
 * the one who can say whether it read it right.
 *
 * Costs one call on the cheapest chat model.
 */

const 공문 = [
  '○○대학교 정보시스템팀',
  '',
  '수신  각 연구실장',
  '제목  연구실 장비 관리 지침 개정 알림',
  '',
  '1. 관련: 학사운영규정 제12조',
  '2. 위 호와 관련하여 장비 관리 지침을 붙임과 같이 개정하였음을 알려드립니다.',
  '3. 각 연구실에서는 2026년 3월 31일까지 점검 결과를 회신하여 주시기 바랍니다.',
  '',
  '붙임  1. 개정 지침 1부.  끝.',
].join('\n')

test('올린 공문에서 디자인 시스템 초안을 읽고, 확인한 뒤에 저장한다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  await page.goto('/settings/preferences')

  const designs = page.getByRole('region', { name: '디자인 시스템' })
  // Counted after the list has arrived: it loads from its own request, and a
  // count taken before that is zero for reasons that have nothing to do with
  // what this test is about.
  await expect(designs.locator('li').first()).toBeVisible({ timeout: 20_000 })
  const before = await designs.locator('li').count()
  await designs.getByRole('button', { name: '문서에서 가져오기' }).click()

  const form = page.getByRole('region', { name: '문서에서 가져오기' })
  await expect(form).toBeVisible()
  await form.getByLabel('문서 올리기').setInputFiles({
    name: '연구실-장비-관리-공문.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(공문, 'utf8'),
  })

  // The editor opens on the draft — with a note naming what it was read from,
  // so whoever edits it knows which fields a document is answerable for.
  const editor = page.getByRole('region', { name: '디자인 시스템' })
  await expect(editor.getByLabel('이름', { exact: true })).not.toHaveValue('', {
    timeout: 240_000,
  })
  await expect(page.getByText(/연구실-장비-관리-공문\.txt.*에서 읽었습니다/)).toBeVisible()

  // Colours arrive drawable rather than as whatever the model typed.
  await expect(editor.getByLabel('강조색 색상 코드')).toHaveValue(/^#[0-9a-f]{6}$/)
  await expect(editor.getByLabel('본문색 색상 코드')).toHaveValue(/^#[0-9a-f]{6}$/)
  // An official document is a printed one; the face it proposes should say so.
  await expect(editor.getByLabel('서체')).toHaveValue('serif')
  await page.screenshot({ path: 'test-results/shots/11-design-extract.png' })

  // Nothing was stored on the way here: the list is unchanged until Save.
  const name = `읽어온 공문 ${Date.now()}`
  await editor.getByLabel('이름', { exact: true }).fill(name)
  await editor.getByRole('button', { name: '저장', exact: true }).click()

  const row = designs.locator('li', { hasText: name })
  await expect(row).toBeVisible({ timeout: 20_000 })
  expect(await designs.locator('li').count()).toBe(before + 1)

  // And it is usable where design systems are used.
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).click()
  await page.getByLabel('이름', { exact: true }).fill(`읽어온 디자인 확인 ${Date.now()}`)
  await page.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
  await page.getByLabel('디자인', { exact: true }).selectOption({ label: name })
  await expect(page.getByText('검증용', { exact: false })).toHaveCount(0)
})

test('읽을 수 없는 파일은 이유를 말하고 아무것도 저장하지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/settings/preferences')

  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await expect(designs.locator('li').first()).toBeVisible({ timeout: 20_000 })
  const before = await designs.locator('li').count()
  await designs.getByRole('button', { name: '문서에서 가져오기' }).click()

  const form = page.getByRole('region', { name: '문서에서 가져오기' })
  await form.getByLabel('문서 올리기').setInputFiles({
    name: '빈-문서.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('   ', 'utf8'),
  })

  // Said, not swallowed: a silent failure here reads as a slow model.
  await expect(form.getByText(/못했습니다|짧습니다/)).toBeVisible({ timeout: 60_000 })
  await form.getByRole('button', { name: '취소' }).click()
  expect(await designs.locator('li').count()).toBe(before)
})
