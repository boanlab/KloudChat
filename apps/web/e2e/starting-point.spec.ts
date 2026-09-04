import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** A 시작점 is carried by the turn as an id, not typed into the composer. Catalogue stubbed. */

const CATALOGUE = [
  {
    id: 't_e2e_debug',
    kind: 'chat',
    group: '개발',
    title: '장애 원인 좁히기',
    description: '스택 트레이스에서 가설과 확인 방법까지',
    fills: ['에러 로그', '재현 조건'],
    prompt: '이 에러의 원인을 좁혀야 한다. 확신이 없는 것은 확인할 방법을 알려 줘.\n\n에러: ',
  },
]

test('시작점은 입력창을 채우지 않고 요청에 실려 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.route('**/api/prompt-templates', (route) =>
    route.fulfill({ json: CATALOGUE }),
  )

  // Fake stream: the subject is what leaves the composer.
  const sent: { content: string; startingTemplateId?: string }[] = []
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify({ type: 'delta', text: '먼저 로그를 보겠습니다.' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const card = page.getByRole('dialog').locator('.grid > *').filter({ hasText: '장애 원인 좁히기' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).toContainText('이번 요청에만 적용')
  await card.getByRole('button', { name: /장애 원인 좁히기/ }).click()

  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('')
  // The placeholder lists the fills, with the particle on the last one.
  await expect(box).toHaveAttribute('placeholder', '에러 로그, 재현 조건을 적어 주세요')

  const chip = page.getByText('장애 원인 좁히기', { exact: true })
  await expect(chip).toBeVisible()
  await page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' }).click()
  await expect(chip).toHaveCount(0)
  await expect(box).toHaveAttribute('placeholder', '무엇이든 물어보세요')

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await page.getByRole('dialog').getByRole('button', { name: /장애 원인 좁히기/ }).click()

  const typed = 'ConnectionResetError 가 배포 직후에만 납니다'
  await box.fill(typed)
  await box.press('Enter')
  await expect(page.getByText('먼저 로그를 보겠습니다.')).toBeVisible({ timeout: 30_000 })

  // The turn carries the id; the content is only what was typed.
  expect(sent).toHaveLength(1)
  expect(sent[0].content).toBe(typed)
  expect(sent[0].startingTemplateId).toBe('t_e2e_debug')

  // The transcript names the template and never quotes its framing.
  await expect(page.getByText('시작점 장애 원인 좁히기')).toBeVisible()
  await expect(page.getByText('확신이 없는 것은')).toHaveCount(0)

  // Not sticky: one turn only.
  await expect(box).toHaveValue('')
  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toHaveCount(0)
})
