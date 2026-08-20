import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The gallery ships twenty-four starting points and, until now, no way to add
 * a twenty-fifth. The document an organisation actually produces — its 공문,
 * its 발표 양식 — was the one document with no starting point.
 */
test('내가 만든 템플릿이 갤러리에 서고, 고르면 입력창에 들어가고, 고치고 지울 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  const openGallery = async () => {
    await page.getByRole('button', { name: '템플릿에서 시작' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
  }

  await openGallery()
  const builtIn = await page.getByRole('dialog').locator('button:has(p)').count()
  expect(builtIn, '기본 템플릿이 없다').toBeGreaterThan(0)

  // Write one down.
  await page.getByRole('button', { name: '템플릿 추가' }).click()
  const name = `공문 초안 ${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('설명').fill('기관 공문 양식에 맞춘 초안')
  await page.getByLabel('준비물').fill('수신처, 제목')
  await page.getByLabel('문구').fill('아래 양식에 맞춰 공문을 써 줘.\n\n수신: ')
  await page.getByRole('button', { name: '저장', exact: true }).click()

  // It is a card now, with its chips.
  const card = page.getByRole('dialog').locator('div.group', { hasText: name })
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.getByText('수신처')).toBeVisible()

  // Picking it fills the composer and never sends.
  await card.getByRole('button').first().click()
  const composer = page.getByLabel('프롬프트 입력')
  await expect(composer).toHaveValue(/수신:/)
  await expect(page).not.toHaveURL(/\/s\/[0-9a-f]{32}/)

  // It survives a reload — it is a row, not a tab's memory.
  await page.reload()
  await openGallery()
  const again = page.getByRole('dialog').locator('div.group', { hasText: name })
  await expect(again).toBeVisible({ timeout: 15_000 })

  // A typo in it is a typo, not a reason to start over. The form opens on what
  // was written, and saving writes over the same card.
  await again.getByRole('button', { name: `${name} 수정` }).click()
  await expect(page.getByLabel('이름')).toHaveValue(name)
  await expect(page.getByLabel('준비물')).toHaveValue('수신처, 제목')
  const fixed = `${name} 개정`
  await page.getByLabel('이름').fill(fixed)
  await page.getByLabel('문구').fill('아래 양식에 맞춰 공문을 써 줘.\n\n수신자: ')
  await page.getByRole('button', { name: '저장', exact: true }).click()

  const edited = page.getByRole('dialog').locator('div.group', { hasText: fixed })
  await expect(edited).toBeVisible({ timeout: 15_000 })
  // One card, not two: the correction replaced the row rather than adding one.
  await expect(page.getByRole('dialog').locator('div.group', { hasText: name })).toHaveCount(1)

  // And the corrected wording is what the composer gets.
  await edited.getByRole('button').first().click()
  await expect(composer).toHaveValue(/수신자:/)

  // And it can be thrown away, which is what makes adding one safe.
  await openGallery()
  const doomed = page.getByRole('dialog').locator('div.group', { hasText: fixed })
  await doomed.getByRole('button', { name: `${fixed} 삭제` }).click()
  await expect(doomed).toHaveCount(0, { timeout: 15_000 })

  // The built-ins are not deletable — they are not this person's to remove.
  const shipped = page.getByRole('dialog').locator('div.group', { hasText: '업무·기술 보고서' })
  await expect(shipped.getByRole('button', { name: /삭제/ })).toHaveCount(0)
})

/**
 * The half that makes a template worth attaching a file to.
 *
 * A 공문 has a shape — a header block, a fixed order, a closing. Describing
 * that shape in prose and hoping is not the same as handing the model the
 * document, so the form rides along as an attachment and the draft is written
 * against the real thing.
 */
test('양식 파일을 붙인 템플릿을 고르면 그 파일이 첨부로 따라온다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  await page.getByRole('button', { name: '템플릿 추가' }).click()

  const name = `양식 템플릿 ${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('문구').fill('이 양식에 맞춰 써 줘.\n\n수신: ')
  await page.getByLabel('양식 파일').setInputFiles({
    name: 'gongmun-form.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('수신: (수신처)\n제목: (제목)\n\n1. 관련 근거\n2. 요청 사항\n\n끝.'),
  })
  // The chip replaces the picker once the upload lands.
  await expect(page.getByText('gongmun-form.txt')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '저장', exact: true }).click()

  const card = page.getByRole('dialog').locator('div.group', { hasText: name })
  await expect(card).toBeVisible({ timeout: 15_000 })
  // Visible before it is chosen: this card behaves differently from the others.
  await expect(card.getByText('gongmun-form.txt')).toBeVisible()

  await card.getByRole('button').first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/수신:/)
  // …and the form is on the turn, where the model will read it.
  await expect(page.getByTitle(/토큰|내용 없음/).filter({ hasText: 'gongmun-form.txt' })).toBeVisible({
    timeout: 15_000,
  })

  // Clean up so the gallery does not fill with test rows.
  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  const again = page.getByRole('dialog').locator('div.group', { hasText: name })
  await again.getByRole('button', { name: `${name} 삭제` }).click()
  await expect(again).toHaveCount(0, { timeout: 15_000 })
})
