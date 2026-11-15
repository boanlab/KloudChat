import { expect, test } from '@playwright/test'
import { setWebSearch } from './composer-tools'
import { mockApp, model, models, now, sessionId } from './super-agent-fixture'

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
  await expect(page.getByLabel('코드 · Super Agent 모델')).toBeVisible()
  // Super Agent is always there: no switch to turn it on or off
  await expect(page.getByRole('switch', { name: /Super Agent/ })).toHaveCount(0)
  await expect(page.getByText(/Super Agent (켜기|끄기|보이기)/)).toHaveCount(0)
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
    superAgentRules: { code: { model: 'local/flash', effort: 'low' } },
  })
  expect(state.governancePuts.at(-1)).not.toHaveProperty('superAgentEnabled')
  await page.getByRole('button', { name: '표를 기본값으로' }).click()
  await expect(page.getByLabel('코드 · Super Agent 모델')).toHaveValue('external/deep')
})

const KIND_LABEL = { chat: '챗은', report: '보고서는', slides: '슬라이드는' } as const

for (const kind of ['chat', 'report', 'slides'] as const) {
  test(`새 ${KIND_LABEL[kind]} Super Agent가 고른 채로 시작하고 선택기 맨 위에 둔다 @phone`, async ({ page }) => {
    const state = await mockApp(page)
    await page.goto(`/new/${kind}`)
    const picker = page.getByRole('button', { name: 'Super Agent', exact: true })
    await expect(picker).toBeVisible()
    await picker.click()
    const entries = page.getByRole('menuitemradio')
    await expect(entries.nth(0)).toContainText('Super Agent')
    await expect(entries.nth(0)).not.toContainText('strict-local')
    await expect(entries.nth(0)).toHaveAttribute('aria-checked', 'true')
    await expect(entries.nth(1)).toContainText('Super Agent (strict-local)')
    await page.keyboard.press('Escape')
    await setWebSearch(page, '끔')
    await page.getByLabel('프롬프트 입력').fill('분기 보고')
    await page.getByLabel('프롬프트 입력').press('Enter')
    await expect.poll(() => state.creates.length).toBe(1)
    expect(state.creates[0]).toMatchObject({ kind, routingMode: 'super' })
  })
}

test('관리자가 고른 기본값대로 새 대화가 Super Agent (strict-local)이나 고정 모델로 시작한다', async ({ page }) => {
  await mockApp(page, 'manual', undefined, undefined, { defaultRouting: { chat: 'super_strict', report: 'manual' } })
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true })).toBeVisible()
  await page.goto('/new/report')
  await expect(page.getByRole('button', { name: /Mock · Flash/ })).toBeVisible()
})

test('쓸 수 있는 모델이 표에 없으면 새 대화는 모델로 시작하고 Super Agent 항목을 빈 채로 두지 않는다', async ({ page }) => {
  const state = await mockApp(page)
  await page.unroute(/\/api\/models$/)
  await page.route(/\/api\/models$/, (r) =>
    r.fulfill({
      json: {
        models, litellmAvailable: true, defaultChatModel: 'local/flash',
        autoRouting: { enabled: false, available: false, reason: 'disabled', classifierModelId: null, economyModelIds: [],
          qualityAvailable: false, qualityReason: 'disabled', qualityModelIds: [] },
        superAgent: {
          default: { available: false, reason: 'no_models', estimate: null },
          strict: { available: false, reason: 'no_models', estimate: null },
        },
      },
    }),
  )
  await page.goto('/new/chat')
  const picker = page.getByRole('button', { name: /Mock · Flash/ })
  await expect(picker).toBeVisible()
  await picker.click()
  await expect(page.getByRole('menuitemradio', { name: /Super Agent/ })).toHaveCount(0)
  expect(state.creates).toHaveLength(0)
})

for (const [label, kind] of [['챗', 'chat'], ['보고서', 'report'], ['슬라이드', 'slides']] as const) {
  test(`${label} 개인 기본값으로 Super Agent 두 항목과 모델을 고를 수 있다 @phone`, async ({ page }) => {
    const state = await mockApp(page)
    await page.goto('/settings/preferences')
    const field = page.getByRole('button', { name: new RegExp(`^${label}: `) })
    // nothing picked: the installation's default, Super Agent
    await expect(field).toHaveAttribute('aria-label', `${label}: Super Agent`)
    await field.click()
    const entries = page.getByRole('menuitemradio')
    await expect(entries.nth(0)).toContainText('Super Agent')
    await expect(entries.nth(0)).toHaveAttribute('aria-checked', 'true')
    await expect(entries.nth(1)).toContainText('Super Agent (strict-local)')
    await entries.nth(1).click()
    await expect(field).toHaveAttribute('aria-label', `${label}: Super Agent (strict-local)`)
    await expect.poll(() => state.profilePatches.at(-1)).toMatchObject({ preferences: { modelByKind: { [kind]: 'super_strict' } } })
    await field.click()
    await page.getByRole('menuitemradio', { name: /Mock · Deep/ }).click()
    await expect(field).toHaveAttribute('aria-label', `${label}: Mock · Deep`)
    await field.click()
    await page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ }).click()
    await expect(field).toHaveAttribute('aria-label', `${label}: Super Agent`)
    await expect.poll(() => state.profilePatches.at(-1)).toMatchObject({ preferences: { modelByKind: { [kind]: 'super' } } })
  })
}

test('개인 기본값이 Super Agent (strict-local)이면 새 대화가 그 항목으로 시작한다', async ({ page }) => {
  const state = await mockApp(page, 'manual', undefined, { report: 'super_strict' })
  await page.goto('/new/report')
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true })).toBeVisible()
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('분기 보고')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ kind: 'report', routingMode: 'super_strict' })
})

test('개인 기본 모델은 계정에 저장한 선택을 보여 준다 @phone', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('kchat-models', JSON.stringify({ chat: 'local/flash' })))
  await mockApp(page, 'manual', undefined, { chat: 'external/deep', report: 'local/flash' })
  await page.goto('/settings/preferences')
  await expect(page.getByRole('button', { name: /^챗: / })).toHaveAttribute('aria-label', '챗: Mock · Deep')
  await expect(page.getByRole('button', { name: /^보고서: / })).toHaveAttribute('aria-label', '보고서: Mock · Flash')
  await expect(page.getByRole('button', { name: /^슬라이드: / })).toHaveAttribute('aria-label', '슬라이드: Super Agent')
})

const project = {
  id: 'pppppppppppppppppppppppppppppppp', name: '내부 감사 프로젝트', description: '', emoji: '', instructions: '', skillIds: [],
  designSystemId: null, renderTemplates: {}, strictLocalWebSearch: null, files: [], sessionIds: [],
  createdAt: now, updatedAt: now,
}

for (const strict of [true, false]) {
  test(`프로젝트의 새 보고서는 보내기 전부터 ${strict ? 'Super Agent (strict-local)' : 'Super Agent'}로 보인다 @phone`, async ({ page }) => {
    const state = await mockApp(page, 'manual', undefined, { report: 'super' }, { projectStrict: strict })
    await page.route(/\/api\/projects(?:\?.*)?$/, (r) => r.fulfill({ json: [project] }))
    await page.route(/\/api\/projects\/p+$/, (r) => r.fulfill({ json: project }))
    await page.goto(`/projects/${project.id}`)
    await page.getByRole('button', { name: '새로 시작', exact: true }).click()
    await page.getByRole('menuitem', { name: '보고서' }).click()
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
    const title = strict ? 'Super Agent (strict-local)' : 'Super Agent'
    // before anything is sent: the chip already says where the material goes
    await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible()
    expect(state.creates).toHaveLength(0)
    await setWebSearch(page, '끔')
    await page.getByLabel('프롬프트 입력').fill('감사 결과 보고')
    await page.getByLabel('프롬프트 입력').press('Enter')
    await expect.poll(() => state.creates.length).toBe(1)
    expect(state.creates[0]).toMatchObject({ kind: 'report', projectId: project.id, routingMode: strict ? 'super_strict' : 'super' })
  })
}

test('「내부 자료 작업」 줄은 빈 열을 작업 종류의 모델 그대로 두고, 채우면 그 모델을 저장한다', async ({ page }) => {
  const state = await mockApp(page)
  await page.goto('/admin/system/routing')
  const superColumn = page.getByLabel('내부 자료 작업 · Super Agent 모델')
  await expect(superColumn).toHaveValue('')
  await expect(superColumn.locator('option:checked')).toHaveText('작업 종류의 모델 그대로')
  await expect(page.getByLabel('내부 자료 작업 · strict-local 모델')).toHaveValue('')
  await superColumn.selectOption('external/deep')
  await page.getByRole('button', { name: 'Super Agent 설정 저장' }).click()
  await expect(page.getByText('저장했습니다.')).toBeVisible()
  expect(state.governancePuts.at(-1)).toMatchObject({
    superAgentRules: { material: { model: 'external/deep' } },
  })
})

for (const [entry, stored, shown] of [
  [/Super Agent \(strict-local\)/, 'super_strict', 'Super Agent (strict-local)'],
  [/Mock · Deep/, 'external/deep', 'Mock · Deep'],
] as const) {
  test(`홈에서 ${shown}을 고른 뒤 「기본값으로 저장」하면 서버에 남은 값을 다른 기기의 설정도 보여 준다 @phone`, async ({ page, browser }) => {
    const state = await mockApp(page, 'manual', undefined, { chat: 'local/flash' })
    await page.goto('/')
    // Super Agent first: the home screen keeps an unsent conversation for it
    await page.getByRole('button', { name: /Mock · Flash/ }).click()
    await page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ }).click()
    await expect(page.getByRole('button', { name: 'Super Agent', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Super Agent', exact: true }).click()
    await page.getByRole('menuitemradio', { name: entry }).first().click()
    await expect(page.getByRole('button', { name: shown === 'Mock · Deep' ? /Mock · Deep/ : shown, exact: shown !== 'Mock · Deep' }).first()).toBeVisible()
    // a pick in one conversation is that conversation's only
    expect(state.profilePatches).toEqual([])
    await page.getByRole('button', { name: shown === 'Mock · Deep' ? /Mock · Deep/ : shown, exact: shown !== 'Mock · Deep' }).first().click()
    await page.getByRole('menuitem', { name: /기본값으로 저장/ }).click()
    await expect.poll(() => state.profilePatches.at(-1)).toMatchObject({ preferences: { modelByKind: { chat: stored } } })
    expect(state.profilePatches).toHaveLength(1)
    const kept = (state.profilePatches.at(-1) as { preferences: { modelByKind: Record<string, string> } }).preferences.modelByKind
    // this browser's settings, after a reload
    await page.goto('/settings/preferences')
    await expect(page.getByRole('button', { name: /^챗: / })).toHaveAttribute('aria-label', `챗: ${shown}`)
    // another device: a fresh browser that has only what the server stored
    const other = await browser.newContext({ baseURL: test.info().project.use.baseURL, viewport: page.viewportSize() ?? undefined })
    const elsewhere = await other.newPage()
    await mockApp(elsewhere, 'manual', undefined, kept)
    await elsewhere.goto('/settings/preferences')
    await expect(elsewhere.getByRole('button', { name: /^챗: / })).toHaveAttribute('aria-label', `챗: ${shown}`)
    await elsewhere.goto('/new/chat')
    await expect(elsewhere.getByRole('button', { name: shown === 'Mock · Deep' ? /Mock · Deep/ : shown, exact: shown !== 'Mock · Deep' }).first()).toBeVisible()
    await other.close()
  })
}

test('프로젝트 새 대화에서 고른 strict-local은 계정 기본값이 되지 않고, 다음 새 대화는 저장한 기본값으로 시작한다', async ({ page }) => {
  const state = await mockApp(page, 'manual', undefined, { chat: 'local/flash' }, { projectStrict: false })
  await page.route(/\/api\/projects(?:\?.*)?$/, (r) => r.fulfill({ json: [project] }))
  await page.route(/\/api\/projects\/p+$/, (r) => r.fulfill({ json: project }))
  await page.goto(`/projects/${project.id}`)
  await page.getByRole('button', { name: '새로 시작', exact: true }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
  await page.getByRole('button', { name: /Mock · Flash/ }).first().click()
  await page.getByRole('menuitemradio', { name: /Super Agent \(strict-local\)/ }).click()
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true }).first()).toBeVisible()
  // picked here, so no reason line: the person chose it for this conversation
  await expect(page.getByTestId('strict-start-hint')).toHaveCount(0)
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('감사 자료 정리')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ kind: 'chat', projectId: project.id, routingMode: 'super_strict' })
  expect(state.profilePatches).toEqual([])
  // another task: a new chat starts on the saved default, not the one-off pick
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: /Mock · Flash/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true })).toHaveCount(0)
  await expect(page.getByTestId('strict-start-hint')).toHaveCount(0)
})

test('strict-local로 시작하는 새 대화는 그 이유를 모델 선택기 옆에 밝힌다 @phone', async ({ page }) => {
  await mockApp(page, 'manual', undefined, { chat: 'super' }, { projectStrict: true })
  await page.route(/\/api\/projects(?:\?.*)?$/, (r) => r.fulfill({ json: [project] }))
  await page.route(/\/api\/projects\/p+$/, (r) => r.fulfill({ json: project }))
  await page.goto(`/projects/${project.id}`)
  await page.getByRole('button', { name: '새로 시작', exact: true }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
  const hint = page.getByTestId('strict-start-hint')
  await expect(hint).toHaveText('이 프로젝트는 기관 정책상 strict-local로 시작합니다')
  const trigger = page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true }).first()
  await expect(trigger).toHaveAccessibleDescription('이 프로젝트는 기관 정책상 strict-local로 시작합니다')
  // outside the project the same account starts on Super Agent, with no line
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: 'Super Agent', exact: true }).first()).toBeVisible()
  await expect(hint).toHaveCount(0)
})

test('개인 기본값이 strict-local이면 새 대화가 그 설정 때문이라고 밝힌다', async ({ page }) => {
  await mockApp(page, 'manual', undefined, { chat: 'super_strict' })
  await page.goto('/new/chat')
  await expect(page.getByTestId('strict-start-hint')).toHaveText('기본 모델 설정(strict-local)에 따라 시작합니다')
  // a model picked for this conversation: the line goes, and the account keeps its default
  await page.getByRole('button', { name: 'Super Agent (strict-local)', exact: true }).first().click()
  await page.getByRole('menuitemradio', { name: /Mock · Deep/ }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
  await expect(page.getByRole('button', { name: /Mock · Deep/ }).first()).toBeVisible()
  await expect(page.getByTestId('strict-start-hint')).toHaveCount(0)
})

test('in a narrow column the composer shortens the model name inside its row', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) < 640, 'desktop layout')
  const long = model('local/long', 'Qwen · Qwen3.8 27b (local) with a long deployment name', 'hybrid')
  await mockApp(page, 'manual', undefined, { chat: 'local/long' }, { catalogue: [long, ...models] })
  // as narrow as the chat column beside an open document
  await page.setViewportSize({ width: 720, height: 900 })
  await page.goto('/new/chat')
  const picker = page.getByRole('button', { name: /Qwen · Qwen3\.8 27b/ })
  await expect(picker).toBeVisible()
  const send = page.getByRole('button', { name: '전송', exact: true })
  const [pick, sent, box] = [await picker.boundingBox(), await send.boundingBox(),
    await page.getByLabel('프롬프트 입력').evaluate((el) => {
      let node: HTMLElement | null = el as HTMLElement
      while (node && !node.className.toString().includes('border')) node = node.parentElement
      const r = (node ?? el).getBoundingClientRect()
      return { x: r.x, width: r.width }
    })]
  expect(pick!.x + pick!.width).toBeLessThanOrEqual(sent!.x + 0.5)
  expect(sent!.x + sent!.width).toBeLessThanOrEqual(box.x + box.width + 0.5)
})
