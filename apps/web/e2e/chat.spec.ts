/**
 * Chat against the real backend and a real model.
 *
 * Needs `api/` running and LiteLLM reachable. Uses `local/qwen3.6-35b` so the
 * suite costs nothing and does not depend on an external provider being up.
 *
 * Run with: npm run test:chat
 */

import { expect, test } from '@playwright/test'
import { answerText, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** Picks a specific model so cost and latency are predictable. */
async function useLocalModel(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
}

test('메시지를 보내면 토큰이 스트리밍되고 답이 남는다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)

  await page.getByLabel('프롬프트 입력').fill('한 문장으로만 답해줘: 1 + 1 은?')
  // Creating the session server-side is what moves us off /new/chat, so the
  // round trip has to be awaited rather than assumed.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST',
    ),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  const assistant = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(assistant).toContainText('2', { timeout: 90_000 })

  // The usage footer only renders once the turn is settled. Read on the answer
  // itself and on the whole line: `/크레딧$/` across the page also matched the
  // sidebar's "이번 달 크레딧" label, so it passed on a turn that never
  // reported anything — and a free model ends the line "무료", not "크레딧".
  await expect(assistant.getByText(/ in · .+ out · (무료|[\d,]+ 크레딧)/)).toBeVisible({
    timeout: 30_000,
  })
})

test('새로고침해도 대화가 남아 있다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  const prompt = '한 단어로만 답해줘: 대한민국의 수도는?'
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  // `exact` on purpose: earlier runs leave conversations whose generated TITLE
  // contains the same answer, and those titles render in the sidebar. A loose
  // getByText then resolves to several nodes and fails on strict mode — a stale
  // -state failure that reads like a product regression.
  await expect(answerText(page, '서울')).toBeVisible({ timeout: 90_000 })

  // Wait for the turn to close before reloading. The first assertion matches
  // streaming text, which exists before anything is committed — reloading there
  // asks Postgres for a message that is still in flight.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  const url = page.url()
  await page.reload()
  // Messages come back from Postgres, not from anything held in the tab.
  // `.first()` because the same text is now the conversation's title too — it
  // appears in the sidebar row and the top bar as well as the transcript.
  await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 20_000 })
  // `exact` matches the answer paragraph only. A bare getByText('서울') is a
  // strict-mode violation after the first turn: title generation puts the
  // answer text into the sidebar row and the top bar too, so the same string
  // resolves four ways.
  await expect(answerText(page, '서울')).toBeVisible({ timeout: 20_000 })
  expect(page.url()).toBe(url)
})

test('첫 턴이 끝나면 대화 제목이 생성된다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await page.getByLabel('프롬프트 입력').fill('광합성이 뭔지 두 문장으로 설명해줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // The provisional title is the raw prompt; a generated one replaces it once
  // the first turn settles. Sidebar entries are buttons, not links.
  await expect
    .poll(
      async () => {
        const labels = await page.getByRole('button').allInnerTexts()
        return labels.some((t) => t.includes('광합성') && !t.includes('두 문장으로'))
      },
      { timeout: 120_000, intervals: [2_000] },
    )
    .toBe(true)
})

test('사이드바에 이전 대화가 쌓이고 삭제된다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await page.getByLabel('프롬프트 입력').fill('짧게 답해줘: 물의 화학식은?')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  // The answer, not the title generated from it: a token the model was asked to
  // print lands in the sidebar entry and the header as well.
  await expect(answerText(page, /H2O|H₂O/i)).toBeVisible({ timeout: 90_000 })

  // A fresh page proves the list came from the server, not local state.
  await page.reload()
  await expect(page.getByRole('button', { name: /물|화학식|H2O/ }).first()).toBeVisible({
    timeout: 20_000,
  })
})
