import { expect, test } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'
import { openTool } from './composer-tools'
import { openSidebar } from './helpers'

/**
 * 파일함: the account's files, those the person gave apart from those made in conversations;
 * 「파일함에서 고르기」 attaches one again by id; deleting a conversation asks whether its
 * files go too (they stay in the file box unless ticked).
 */

const at = '2026-10-16T03:00:00.000Z'
const file = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
  id: id.padEnd(32, '0'), name, size: 2048, mime: name.endsWith('.png') ? 'image/png' : 'text/plain',
  tokens: 10, projectId: null, sessionId: null, preview: '', error: null, origin: 'upload', createdAt: at, ...extra,
})
const files = [
  file('a1', '회의녹취_0904.txt', { sessionId: chatId }),
  file('a2', '검색서비스_월별지표.csv'),
  file('b1', 'lru_results.csv', { sessionId: chatId, origin: 'generated' }),
  file('b2', '방법 구조도.png', { sessionId: 'gone'.padEnd(32, '0'), origin: 'generated' }),
]

test('파일함 lists uploaded and made files apart and deletes one after asking', async ({ page }, info) => {
  const { unexpected, deletes } = await composerFixture(page, info, { files })
  await page.goto('/files')
  await expect(page.getByRole('heading', { name: '파일함' })).toBeVisible()
  const rows = page.getByTestId('file-row')
  await expect(page.getByRole('tab', { name: /올린 파일\s*2/ })).toHaveAttribute('aria-selected', 'true')
  await expect(rows).toHaveCount(2)
  // where each came from: its conversation, or none
  await expect(rows.filter({ hasText: '회의녹취_0904.txt' })).toContainText('지원 사업 공고 정리')
  await expect(rows.filter({ hasText: '검색서비스_월별지표.csv' })).toContainText('대화 밖에서 올림')

  await page.getByRole('tab', { name: /대화에서 만든 파일\s*2/ }).click()
  await expect(rows).toHaveCount(2)
  await expect(rows.filter({ hasText: '방법 구조도.png' })).toContainText('지운 대화')
  await page.getByLabel('파일 검색').fill('lru')
  await expect(rows).toHaveCount(1)

  await page.getByRole('button', { name: 'lru_results.csv 삭제' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'lru_results.csv 삭제' })).toBeVisible()
  await dialog.getByRole('button', { name: '삭제', exact: true }).click()
  await expect(rows).toHaveCount(0)
  expect(deletes).toEqual([`DELETE /files/${'b1'.padEnd(32, '0')}`])
  expect(unexpected).toEqual([])
})

test('「파일함에서 고르기」 attaches a file already in the account, nothing uploaded', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info, { files })
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await openTool(page, '파일함에서 고르기')
  const dialog = page.getByRole('dialog', { name: '파일함에서 고르기' })
  await expect(dialog.getByTestId('file-row')).toHaveCount(2)
  await dialog.getByLabel('검색서비스_월별지표.csv 고르기').check()
  await dialog.getByRole('button', { name: '1개 붙이기' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText('검색서비스_월별지표.csv')).toBeVisible()

  const input = page.getByLabel('프롬프트 입력')
  await input.fill('이 지표로 월별 추이를 정리해 줘')
  await input.press('Enter')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.attachments).toEqual(['a2'.padEnd(32, '0')])
  expect(unexpected).toEqual([])
})

test('deleting a conversation keeps its files unless the choice is ticked', async ({ page }, info) => {
  const { unexpected, deletes } = await composerFixture(page, info, { files })
  await page.goto('/')
  await openSidebar(page)
  const row = page.locator('aside').locator('div.group').filter({ hasText: '지원 사업 공고 정리' }).first()
  await row.getByRole('button', { name: '메뉴' }).click()
  await page.getByRole('menuitem', { name: '삭제' }).click()
  const dialog = page.getByRole('dialog')
  // the conversation holds two files (one attached, one made in it)
  const choice = dialog.getByLabel(/대화의 파일 2개도 함께 지우기/)
  await expect(choice).not.toBeChecked()
  await choice.check()
  await dialog.getByRole('button', { name: '삭제', exact: true }).click()
  // sent after the undo window
  await expect.poll(() => deletes, { timeout: 15_000 }).toEqual([`DELETE /sessions/${chatId}?files=true`])
  expect(unexpected).toEqual([])
})
