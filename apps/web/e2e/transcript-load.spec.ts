import { expect, test } from '@playwright/test'
import { answer, at, chatId, composerFixture } from './composer-fixture'

/**
 * A conversation opened by link whose transcript does not arrive at once never poses as an
 * empty chat: it says it is loading, says when loading failed, and tries again on its own.
 */

test('a transcript that fails to load shows the failure and loads on its own retry', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  let detailCalls = 0
  await page.route(`**/api/sessions/${chatId}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    detailCalls += 1
    if (detailCalls === 1) return route.fulfill({ status: 503, json: { detail: 'busy' } })
    await route.fallback()
  })
  // The list carries no transcripts, as the server's does: only the count says there is one.
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: [{
      id: chatId, kind: 'chat', title: '지원 사업 공고 정리', model: 'fixture/model', routingMode: 'manual',
      projectId: null, agentId: null, artifactId: null, pending: null, skillIds: [], renderTemplateId: null,
      pinned: false, messageCount: 2, createdAt: at, updatedAt: at, preview: null, made: null,
    }] })
  })
  await page.goto(`/s/${chatId}`)
  await expect(page.getByText('최신 내용을 불러오지 못했습니다.', { exact: true })).toBeVisible()
  await expect(page.getByText('잠시 후 다시 불러옵니다.')).toBeVisible()
  await expect(page.getByText('무엇을 도와드릴까요?')).toHaveCount(0)
  await expect(page.getByLabel('프롬프트 입력')).toHaveCount(0)
  // The automatic retry brings the conversation in.
  await expect(page.getByText(answer.split('\n')[0])).toBeVisible({ timeout: 10_000 })
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  expect(detailCalls).toBeGreaterThanOrEqual(2)
  expect(unexpected).toEqual([])
})
