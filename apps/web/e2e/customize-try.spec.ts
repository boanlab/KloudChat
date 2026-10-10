import { expect, test, type Page } from '@playwright/test'
import { hubFixture, myAgents, mySkills, newSessionId, storeSkills } from './customize-fixture'

/** 「체험」 opens a conversation with the item in it, whatever kind of item it is. */

test.use({ serviceWorkers: 'block' })

async function tryCard(page: Page, name: string) {
  const card = page.getByTestId('item-card').filter({ hasText: name }).first()
  await expect(card).toBeVisible({ timeout: 15_000 })
  await card.getByRole('button', { name: `${name} 체험` }).click()
}

test('agent: a conversation with the agent', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=mine')
  await tryCard(page, myAgents[0].name)
  await expect(page).toHaveURL(new RegExp(`/s/${newSessionId}$`))
  expect(state.calls.find((c) => c.path === '/sessions')?.body).toMatchObject({ agentId: myAgents[0].id, kind: 'chat' })
  expect(state.problems).toEqual([])
})

test('own skill: a chat with the skill switched on', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=skills&view=mine')
  await tryCard(page, mySkills[1].name)
  await expect(page).toHaveURL(new RegExp(`/s/${newSessionId}$`))
  expect(state.calls.find((c) => c.path === '/sessions')?.body).toMatchObject({ kind: 'chat' })
  expect(state.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ skillIds: [mySkills[1].id] })
  expect(state.problems).toEqual([])
})

test('store skill: taken as a copy first, said so, with 빼기', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=skills&view=explore')
  await tryCard(page, storeSkills[0].name)
  await expect(page).toHaveURL(new RegExp(`/s/${newSessionId}$`))
  const paths = state.calls.map((c) => `${c.method} ${c.path}`)
  expect(paths.slice(0, 3)).toEqual([`POST /skills/${storeSkills[0].id}/install`, 'POST /sessions', `PATCH /sessions/${newSessionId}`])
  const toast = page.getByTestId('toast')
  await expect(toast).toContainText('체험하려고 내 스킬에 추가했어요')
  await expect(toast.getByRole('button', { name: '빼기' })).toBeVisible()
  expect(state.problems).toEqual([])
})

test('a shipped agent from 탐색 starts a conversation with it', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/customize?tab=agents&view=explore')
  await tryCard(page, '비교 보고서')
  await expect(page).toHaveURL(new RegExp(`/s/${newSessionId}$`))
  expect(state.calls.filter((c) => c.path === '/sessions').at(-1)?.body).toMatchObject({ agentId: 'a3333333333333333333333333333333', kind: 'report' })
  expect(state.problems).toEqual([])
})
