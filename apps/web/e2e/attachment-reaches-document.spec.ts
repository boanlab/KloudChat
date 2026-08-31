/**
 * 첨부한 파일이 보고서·슬라이드 생성까지 실제로 실려 가는지.
 *
 * This exists because it once did not. The composer uploaded the file, the
 * server was ready to read it, the transcript had a column to record it — and
 * the store's three document runners simply left `attachments` out of the
 * request body. Only chat sent it. So a person who attached a 계획서 and asked
 * for 보고 자료 got an outline about nothing they had given it, with no error
 * and no empty state to explain the gap: the request sentence was all the
 * writer ever saw.
 *
 * Nothing on screen could have caught that, which is why this asserts on what
 * left the browser, on both halves of the turn — the pass that plans and the
 * pass that writes what was approved are two requests, and the server builds
 * its context fresh for each.
 *
 * The planning pass runs for real: a proposal the server did not store is a
 * card that vanishes on the next read, and the approval this test is about
 * would have nothing to press. Only the approved write is answered here, so
 * the suite costs one outline rather than a whole document.
 */

import { expect, test } from '@playwright/test'
import { approveOnce, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)

/** Every `/messages` body this turn sent, oldest first. */
type Sent = { attachments?: string[]; approve?: boolean }

for (const surface of ['report', 'slides'] as const) {
  const label = surface === 'report' ? '보고서' : '슬라이드'

  test(`${label} 생성 요청은 첨부 파일을 계획과 작성 양쪽에 모두 싣는다`, async ({ page }) => {
    await signIn(page)

    const sent: Sent[] = []
    await page.route('**/sessions/*/messages', async (route) => {
      sent.push(JSON.parse(route.request().postData() ?? '{}') as Sent)
      // Everything up to the approval runs for real: a proposal the server did
      // not store is a card that vanishes on the next read, and a clarifying
      // question answered by a stub never reaches the outline behind it. Only
      // the approved write is cut short, which is the expensive half and the
      // one this test has already learnt what it needs from.
      if (sent[sent.length - 1].approve !== true) {
        await route.fulfill({ response: await route.fetch({ timeout: 240_000 }) })
        return
      }
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: 'data: [DONE]\n\n',
      })
    })

    await page.goto(`/new/${surface}`)
    await page.getByLabel('파일 선택').setInputFiles({
      name: '전교생-AI기초-교육-계획.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(
        '2-3. 전교생 AI기초 교육 의무화\n' +
          '가. 개편방향 및 세부 추진전략\n' +
          'SW디딤돌 2개 교과목을 AI기초 교육 이수체계로 개편한다.\n',
      ),
    })
    await expect(page.getByText('전교생-AI기초-교육-계획.txt')).toBeVisible()
    await page.getByLabel('프롬프트 입력').fill('첨부한 파일 내용을 바탕으로 보고 자료 만들어줘')
    await page.getByLabel('프롬프트 입력').press('Enter')

    // The planning pass.
    await expect.poll(() => sent.length, { timeout: 240_000 }).toBeGreaterThan(0)
    expect(sent[0].attachments, '계획 요청이 첨부 없이 나갔다').toHaveLength(1)
    const fileId = sent[0].attachments?.[0]
    expect(fileId).toBeTruthy()

    // The writing pass. The server may ask something before it proposes, so
    // press through whatever cards it puts up until one of them is the approval
    // — that is the request this half is about.
    let approved: Sent | undefined
    for (let press = 0; press < 4 && !approved; press++) {
      const before = sent.length
      expect(await approveOnce(page, 240_000), '제안 카드가 오르지 않았다').toBe(true)
      await expect.poll(() => sent.length, { timeout: 120_000 }).toBeGreaterThan(before)
      approved = sent.slice(before).find((body) => body.approve === true)
    }
    expect(approved, '승인 요청이 네 번을 눌러도 나가지 않았다').toBeTruthy()
    expect(approved?.attachments, '승인 요청이 첨부를 잃었다').toEqual([fileId])
  })
}
