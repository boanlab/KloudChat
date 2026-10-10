import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'
import { openTool, setWebSearch, toolNames } from './composer-tools'

/**
 * The composer keeps 「+」, dictation, the model and send outside; every other tool is a row of
 * 「+」 (a bottom sheet on a phone), and what is switched on shows as a removable chip.
 */

const phoneProject = () => test.info().project.name === 'phone'

async function send(page: Page, text: string) {
  const input = page.getByLabel('프롬프트 입력')
  await input.fill(text)
  await input.press('Enter')
}

test('「+」 holds the tools; nothing else sits beside the input', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()

  expect(await toolNames(page)).toEqual(['파일·사진 첨부', '스킬', '웹 검색: 자동', '커넥터 1개', '에이전트'])
  // The old toolbar buttons are gone, and model comparison with them.
  for (const gone of ['템플릿 고르기', '스킬', '모델 비교', '도구 더보기', '첨부', '@']) {
    await expect(page.getByRole('button', { name: gone, exact: true })).toHaveCount(0)
  }
  await expect(page.getByRole('button', { name: /^웹 검색/ })).toHaveCount(0)
  // 자동 is the default: no chip.
  await expect(page.getByText(/^웹 검색: /)).toHaveCount(0)
  // The model picker and send stay outside.
  await expect(page.getByRole('button', { name: /검증 모델/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '전송' })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('템플릿 고르기 is offered on report and slides only', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  for (const [kind, offered] of [['chat', false], ['report', true], ['slides', true], ['image', false]] as const) {
    await page.goto(`/new/${kind}`)
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    expect((await toolNames(page)).includes('템플릿 고르기'), kind).toBe(offered)
  }
  await page.goto('/new/report')
  await openTool(page, '템플릿 고르기')
  const dialog = page.getByRole('dialog', { name: '템플릿 고르기' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '이 템플릿으로 시작' }).first().click()
  // The pick shows as a chip with its own ×.
  const remove = page.getByRole('button', { name: '비교 보고서 템플릿 해제' })
  await expect(remove).toBeVisible()
  await remove.click()
  await expect(remove).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('web search is a 자동·항상·끔 choice, sent as auto, true and false', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()

  await openTool(page, /^웹 검색/)
  const radios = page.getByRole('menuitemradio')
  await expect(radios).toHaveCount(3)
  await expect(page.getByRole('menuitemradio', { name: /^자동/ })).toHaveAttribute('aria-checked', 'true')
  // Each choice says what it does in one line.
  await expect(page.getByText('최신 사실이나 날씨를 물으면 찾아봅니다')).toBeVisible()
  await expect(page.getByText('매번 웹에서 찾아봅니다')).toBeVisible()
  await expect(page.getByText('웹을 찾아보지 않습니다')).toBeVisible()
  await page.keyboard.press('Escape')

  await send(page, '자동으로 보내기')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.webSearch).toBe('auto')

  await setWebSearch(page, '항상')
  await expect(page.getByText('웹 검색: 항상', { exact: true })).toBeVisible()
  await send(page, '항상 검색')
  await expect.poll(() => sends.length).toBe(2)
  expect(sends[1].body.webSearch).toBe(true)

  await setWebSearch(page, '끔')
  await expect(page.getByText('웹 검색: 끔', { exact: true })).toBeVisible()
  await send(page, '검색 없이')
  await expect.poll(() => sends.length).toBe(3)
  expect(sends[2].body.webSearch).toBe(false)

  // The chip's × goes back to 자동.
  await page.getByRole('button', { name: '웹 검색 자동으로 되돌리기' }).click()
  await expect(page.getByText(/^웹 검색: /)).toHaveCount(0)
  await send(page, '다시 자동')
  await expect.poll(() => sends.length).toBe(4)
  expect(sends[3].body.webSearch).toBe('auto')
  expect(unexpected).toEqual([])
})

test('skills are found by name, and chips show and remove what is on', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info)
  await page.goto(`/s/${chatId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()

  await openTool(page, '스킬')
  const find = page.getByLabel('스킬 찾기')
  await expect(find).toBeFocused()
  await find.fill('데이터')
  await expect(page.getByRole('menuitemcheckbox')).toHaveCount(1)
  await page.getByRole('menuitemcheckbox', { name: /데이터 분석/ }).click()
  await find.fill('없는 이름')
  await expect(page.getByText('맞는 스킬이 없습니다.')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '데이터 분석 제거' })).toBeVisible()
  // The menu row counts it.
  await page.getByRole('button', { name: '도구', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: '스킬' })).toContainText('1')
  await page.keyboard.press('Escape')

  await page.getByLabel('파일 선택').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('메모') })
  await expect(page.getByRole('button', { name: 'notes.txt 제거' })).toBeVisible()
  await send(page, '스킬과 파일')
  await expect.poll(() => sends.length).toBe(1)
  expect(sends[0].body.activatedSkillIds).toHaveLength(1)
  expect(sends[0].body.attachments).toHaveLength(1)

  // Skills stay on for the conversation until their chip is removed.
  await page.getByRole('button', { name: '데이터 분석 제거' }).click()
  await expect(page.getByRole('button', { name: '데이터 분석 제거' })).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('「@」 typed into an empty box picks an agent, which then shows as a chip', async ({ page }, info) => {
  const { unexpected, created } = await composerFixture(page, info)
  await page.goto('/new/chat')
  const input = page.getByLabel('프롬프트 입력')
  await expect(input).toBeVisible()
  await input.pressSequentially('@')
  const agent = page.getByRole('menuitem', { name: /예산 검토/ })
  await expect(agent).toBeVisible()
  await expect(input).toHaveValue('')
  await agent.click()
  await expect(page).toHaveURL(/\/s\/c0+\d+$/)
  expect(created).toEqual([expect.objectContaining({ agentId: expect.any(String) })])
  await expect(page.getByTitle('이 대화의 에이전트')).toContainText('예산 검토')
  // 「@」 after text is just a character.
  await input.fill('메일 주소는 a@')
  await expect(page.getByRole('menu')).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('home: example requests per surface fill the box and send nothing', async ({ page }, info) => {
  const { unexpected, sends, created } = await composerFixture(page, info)
  await page.goto('/')
  const input = page.getByLabel('프롬프트 입력')
  await expect(input).toBeVisible()
  await expect(page.getByText('에이전트에게 맡기기')).toHaveCount(0)
  const examples = page.getByRole('region', { name: '예시' }).getByRole('button')
  await expect(examples.first()).toBeVisible()
  await examples.first().click()
  await expect(input).toHaveValue('첨부한 문서를 세 줄로 요약해 줘')
  await expect(input).toBeFocused()

  await page.getByRole('group', { name: '만들 것' }).getByRole('button', { name: '슬라이드' }).click()
  await expect(examples.first()).toHaveText('신입 사원 교육용 회사 소개 10분 발표')
  await examples.nth(1).click()
  await expect(input).toHaveValue('프로젝트 결과 보고 15분 발표')
  expect(sends).toEqual([])
  expect(created).toEqual([])
  expect(unexpected).toEqual([])
})

test('phone: one line, tools in a bottom sheet, answers full width, no sideways scroll at 360', async ({ page }, info) => {
  test.skip(!phoneProject(), 'phone layout')
  const { unexpected } = await composerFixture(page, info)
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto(`/s/${chatId}`)
    const input = page.getByLabel('프롬프트 입력')
    await expect(input).toBeVisible()
    const plus = page.getByRole('button', { name: '도구', exact: true })
    const sendButton = page.getByRole('button', { name: '전송' })
    // 「+」, the text and send share one line.
    const [p, i, s] = await Promise.all([plus.boundingBox(), input.boundingBox(), sendButton.boundingBox()])
    for (const box of [p, i, s]) expect(box).not.toBeNull()
    expect(Math.abs(p!.y + p!.height / 2 - (s!.y + s!.height / 2))).toBeLessThan(4)
    expect(i!.x).toBeGreaterThan(p!.x + p!.width - 1)
    expect(i!.x + i!.width).toBeLessThanOrEqual(s!.x + 1)
    await expect(page.getByRole('button', { name: '도구 더보기' })).toHaveCount(0)

    // The answer starts at the column's edge: no mark beside it.
    const answer = page.getByText('이번 분기 공고는 세 가지로 정리됩니다.')
    const box = await answer.boundingBox()
    expect(box!.x).toBeLessThan(width * 0.12)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)

    await plus.click()
    const sheet = page.getByRole('menu', { name: '도구' })
    await expect(sheet).toBeVisible()
    // Measured once its rise-in animation has settled.
    await expect.poll(async () => {
      const box = await sheet.boundingBox()
      return box && [Math.round(box.x), Math.round(box.width), Math.round(box.y + box.height)]
    }).toEqual([0, width, 800])
    // A submenu opens in the sheet, in place.
    await page.getByRole('menuitem', { name: /^웹 검색/ }).click()
    await expect(page.getByRole('menuitemradio')).toHaveCount(3)
    await page.getByRole('menuitem', { name: '뒤로' }).click()
    await expect(page.getByRole('menuitem', { name: '파일·사진 첨부' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
    await page.keyboard.press('Escape')
    await expect(sheet).toHaveCount(0)

    // Grows with its text (the draft from the wider pass is cleared first).
    await input.fill('')
    const before = (await input.boundingBox())!.height
    await input.fill('여러 줄로 길게 쓰는 질문입니다. '.repeat(6))
    expect((await input.boundingBox())!.height).toBeGreaterThan(before)

    await page.goto('/')
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
  }
  expect(unexpected).toEqual([])
})

test('N6: 「+」 › 에이전트 lists every agent, each on its own screen, and offers 에이전트 만들기', async ({ page }, info) => {
  const { unexpected, created } = await composerFixture(page, info)
  await page.goto('/new/chat')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await openTool(page, '에이전트')
  const menu = page.getByRole('menu', { name: '도구' })
  for (const name of ['논문 리뷰어', '예산 검토', '회의록 정리', '에이전트 만들기']) {
    await expect(menu.getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  // Not this screen's: it says where it opens.
  await expect(menu.getByRole('menuitem', { name: /회의록 정리/ })).toContainText('보고서')
  if (info.project.name === 'desktop') await page.screenshot({ path: shotPath('composer-agents-desktop') })
  else await page.screenshot({ path: shotPath('composer-agents-phone') })
  // A report agent opens a report conversation, not a chat one.
  await menu.getByRole('menuitem', { name: /회의록 정리/ }).click()
  await expect(page).toHaveURL(/\/s\/c0+\d+$/)
  expect(created).toEqual([expect.objectContaining({ kind: 'report', agentId: expect.stringMatching(/^a9+$/) })])

  await page.goto('/new/chat')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await openTool(page, '에이전트')
  await page.getByRole('menuitem', { name: '에이전트 만들기' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/new$/)
  // The 「@」 list stays this screen's.
  expect(unexpected.filter((u) => !u.startsWith('GET '))).toEqual([])
})

const shotPath = (name: string) =>
  `${process.env.SHOT_DIR ?? 'test-results/shots'}/${name}.png`
