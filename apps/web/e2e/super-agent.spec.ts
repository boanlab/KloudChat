import { expect, test } from '@playwright/test'
import { setWebSearch } from './composer-tools'
import { mockApp, now, sessionId } from './super-agent-fixture'

test('모델 선택기는 Auto 두 항목 대신 Super Agent 두 항목을 보여 주고 고르면 대화에 저장한다', async ({ page }) => {
  const state = await mockApp(page, 'manual')
  state.sessions = [{ ...state.sessions[0], id: sessionId, kind: 'chat', title: '', projectId: null, agentId: null, model: 'local/flash', routingMode: 'manual', artifactId: null, pinned: false, createdAt: now, updatedAt: now, messages: [], preview: null, messageCount: 0 }]
  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: /Mock · Flash/ }).click()
  const superEntry = page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ })
  const strictEntry = page.getByRole('menuitemradio', { name: /Super Agent \(strict-local\)/ })
  await expect(superEntry).toContainText('질문마다 알맞은 모델과 추론 강도를 골라')
  await expect(superEntry).toContainText('답 하나에 보통 0~440 크레딧')
  await expect(strictEntry).toContainText('질문과 자료가 밖으로 나가지 않습니다')
  await expect(strictEntry).toContainText('무료(내부 모델)')
  await expect(page.getByRole('menuitemradio', { name: /Auto · 비용 절약|Auto · 품질 우선/ })).toHaveCount(0)
  await strictEntry.click()
  await expect.poll(() => state.patches.at(-1)).toMatchObject({ routingMode: 'super_strict' })
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)' })).toBeVisible()
})

test('새 대화는 Super Agent로 시작하고 그 모드로 한 번만 만든다', async ({ page }) => {
  const state = await mockApp(page)
  await page.goto('/new/chat')
  // no model picked on the home screen: a new conversation starts on Super Agent
  await expect(page.getByRole('button', { name: 'Super Agent', exact: true })).toBeVisible()
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ kind: 'chat', routingMode: 'super' })
})

test('홈에서 고른 모델은 새 대화에서도 지키고, Super Agent를 고르면 그 모드로 만든다', async ({ page }) => {
  const state = await mockApp(page, 'manual', undefined, { chat: 'local/flash' })
  await page.goto('/new/chat')
  await page.getByRole('button', { name: /Mock · Flash/ }).click()
  await page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
  await expect(page.getByRole('button', { name: 'Super Agent', exact: true })).toBeVisible()
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ kind: 'chat', routingMode: 'super' })
})

test('답 아래에 이번 답의 모델과 이유를 보여 준다 @phone', async ({ page }) => {
  await mockApp(page, 'super', {
    mode: 'super', taskClass: 'simple', source: 'rule', reasonCode: 'greeting',
    selectedModel: 'local/flash', effort: 'none', pick: 'rule', tools: 'none', routerVersion: 'v',
  })
  await page.goto(`/s/${sessionId}`)
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  const badge = page.getByText('이번 답: Mock · Flash · 간단한 질문')
  await expect(badge).toBeVisible()
  await expect(badge).toHaveAttribute('title', /규칙.*추론 끔.*도구 없이/)
  await page.reload()
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  await expect(badge).toBeVisible()
})

test('개인정보로 내부 모델로 옮긴 답은 그 이유를 말한다', async ({ page }) => {
  await mockApp(page, 'super', {
    mode: 'super', taskClass: 'reasoning', source: 'rule', reasonCode: 'reasoning_markers',
    selectedModel: 'strict-local/flash', effort: 'high', pick: 'rule', tools: 'full', routerVersion: 'v',
    strictReason: 'personal_data',
  })
  await page.goto(`/s/${sessionId}`)
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('고객 010-1234-5678 사과문')
  await page.getByLabel('프롬프트 입력').press('Enter')
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  await expect(page.getByText('이번 답: Mock · Strict Flash · 복잡한 추론 · 개인정보 감지로 내부 모델')).toBeVisible()
})

test('관리자는 작업 종류별 표를 고치고, 거부는 성공으로 보이지 않는다', async ({ page }) => {
  const state = await mockApp(page)
  await page.goto('/admin/system/routing')
  await page.getByLabel('코드 · Super Agent 모델').selectOption('local/flash')
  await page.getByLabel('코드 · 추론 강도').selectOption('low')
  state.failGovernance = true
  await page.getByRole('button', { name: 'Super Agent 설정 저장' }).click()
  await expect(page.getByRole('alert')).toContainText('strict-local 열에는 strict-local 모델만')
  await expect(page.getByText('저장했습니다.')).toHaveCount(0)
  state.failGovernance = false
  await page.getByRole('button', { name: 'Super Agent 설정 저장' }).click()
  await expect(page.getByText('저장했습니다.')).toBeVisible()
  expect(state.governancePuts.at(-1)).toMatchObject({
    superAgentEnabled: true,
    superAgentRules: { code: { model: 'local/flash', effort: 'low' } },
  })
  await page.getByRole('button', { name: '표를 기본값으로' }).click()
  await expect(page.getByLabel('코드 · Super Agent 모델')).toHaveValue('external/deep')
})
