import { expect, test, type Page } from '@playwright/test'
import { hubFixture, myAgents, mySkills, storeSkills } from './customize-fixture'

/** 「바로 쓰기」 opens a conversation with the item in it, whatever kind of item it is; the first send creates it. */

const UNSENT = /\/s\/[0-9a-f]{32}$/
const sessionWrites = (calls: { method: string; path: string }[]) =>
  calls.filter((c) => c.path.startsWith('/sessions') && c.method !== 'GET')

test.use({ serviceWorkers: 'block' })

async function tryCard(page: Page, name: string) {
  const card = page.getByTestId('item-card').filter({ hasText: name }).first()
  await expect(card).toBeVisible({ timeout: 15_000 })
  await card.getByRole('button', { name: `${name} 바로 쓰기` }).click()
}

test('agent: a conversation with the agent', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=mine')
  await tryCard(page, myAgents[0].name)
  await expect(page).toHaveURL(UNSENT)
  await expect(page.getByTitle('이 대화의 에이전트')).toContainText(myAgents[0].name)
  expect(sessionWrites(state.calls)).toEqual([])
  expect(state.problems).toEqual([])
})

test('own skill: a chat with the skill switched on', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=skills&view=mine')
  await tryCard(page, mySkills[1].name)
  await expect(page).toHaveURL(UNSENT)
  await expect(page.getByText(mySkills[1].name).first()).toBeVisible()
  expect(sessionWrites(state.calls)).toEqual([])
  expect(state.problems).toEqual([])
})

test('store skill: taken as a copy first, said so, with 빼기', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=skills&view=explore')
  await tryCard(page, storeSkills[0].name)
  await expect(page).toHaveURL(UNSENT)
  const paths = state.calls.map((c) => `${c.method} ${c.path}`)
  expect(paths.filter((path) => !path.startsWith('GET '))).toEqual([`POST /skills/${storeSkills[0].id}/install`])
  const toast = page.getByTestId('toast')
  await expect(toast).toContainText('바로 쓰려고 내 스킬에 추가했어요')
  await expect(toast.getByRole('button', { name: '빼기' })).toBeVisible()
  expect(state.problems).toEqual([])
})

test('a shipped agent from 탐색 starts a conversation with it', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=explore')
  await tryCard(page, '비교 보고서')
  await expect(page).toHaveURL(UNSENT)
  await expect(page.getByTitle('이 대화의 에이전트')).toContainText('비교 보고서')
  expect(sessionWrites(state.calls)).toEqual([])
  expect(state.problems).toEqual([])
})
