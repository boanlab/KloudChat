import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The gallery ships twenty-four starting points and, until now, no way to add
 * a twenty-fifth. The document an organisation actually produces — its 공문,
 * its 발표 양식 — was the one document with no starting point.
 */
test('내가 만든 시작점이 갤러리에 서고, 고르면 요청에 붙고, 고치고 지울 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  // Always on the 업무 시작점 half. 작업 시작하기 opens on 결과 서식 for a
  // document surface, and a 시작점 somebody wrote is not on that side — a
  // search for it there finds nothing and reads as a card that was not saved.
  const openGallery = async () => {
    await page.getByRole('button', { name: '작업 시작하기' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
  }

  await openGallery()
  // Waited for rather than counted on the spot: the catalogue comes from the
  // server, so on a cold screen the grid fills a moment after the dialog opens
  // instead of arriving with the bundle.
  //
  // Any card, not a sentence card. The report surface's built-in 시작점 were
  // withdrawn once 서식 covered the same jobs — with a form file behind each —
  // so what stands here before this test adds its own is 서식.
  const cards = page.getByRole('dialog').locator('.grid > *')
  await expect(cards.first(), '갤러리가 비어 있다').toBeVisible({ timeout: 20_000 })

  // Write one down.
  await page.getByRole('button', { name: '내 시작점 만들기' }).click()
  const name = `공문 초안 ${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('설명').fill('기관 공문 양식에 맞춘 초안')
  await page.getByLabel('준비물').fill('수신처, 제목')
  await page.getByLabel('문구').fill('아래 양식에 맞춰 공문을 써 줘.\n\n수신: ')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  // The form takes over the whole dialog, so the grid is not back until its
  // heading has gone. Reaching for the search box before that resolves it on
  // the dialog the save is still inside.
  await expect(page.getByRole('heading', { name: '시작점 만들기' })).toBeHidden({ timeout: 20_000 })

  // Found by searching for it. The gallery is paged four to a screen now, so
  // a card is on some page rather than on the page — which is also what a
  // person does when they know the name of the thing they want.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  // It is a card now, with its chips.
  // The grid cell, which is the card. `div.group` reaches the same element
  // and then loses it: the wrapper carries `group` for the hover rules, and a
  // filter on it went from one match to none between two lines that changed
  // nothing.
  const card = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.getByText('수신처')).toBeVisible()

  // Picking it attaches to the turn and never sends. What the person brings —
  // 준비물 — is what the empty box now asks for.
  await card.getByRole('button').first().click()
  const composer = page.getByLabel('프롬프트 입력')
  await expect(composer).toHaveValue('')
  await expect(composer).toHaveAttribute('placeholder', /수신처, 제목/)
  await expect(page).not.toHaveURL(/\/s\/[0-9a-f]{32}/)

  // It survives a reload — it is a row, not a tab's memory.
  await page.reload()
  await openGallery()
  // Searched again: a reload starts the gallery on its first page with an
  // empty box, and a card somebody wrote is one of many.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const again = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
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

  const edited = page.getByRole('dialog').locator('.grid > *').filter({ hasText: fixed })
  await expect(edited).toBeVisible({ timeout: 15_000 })
  // One card, not two: the correction replaced the row rather than adding one.
  await expect(page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })).toHaveCount(1)

  // And the corrected card is the one that attaches.
  await edited.getByRole('button').first().click()
  await expect(page.getByRole('button', { name: `${fixed} 시작점 해제` })).toBeVisible()

  // And it can be thrown away, which is what makes adding one safe.
  await openGallery()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(fixed)
  const doomed = page.getByRole('dialog').locator('.grid > *').filter({ hasText: fixed })
  await doomed.getByRole('button', { name: `${fixed} 삭제` }).click()
  await expect(doomed).toHaveCount(0, { timeout: 15_000 })

  // The 서식 the product ships are not deletable — they are not this person's
  // to remove. 업무·기술 보고서 was one of them and is gone: the report
  // surface's built-in 시작점 were withdrawn once 서식 covered the same jobs.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill('보고 문서')
  const shipped = page.getByRole('dialog').locator('.grid > *').filter({ hasText: '보고 문서' })
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
test('양식 파일을 붙인 시작점을 고르면 그 파일이 첨부로 따라온다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await page.getByRole('button', { name: '내 시작점 만들기' }).click()

  const name = `양식 시작점 ${Date.now()}`
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

  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const card = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await expect(card).toBeVisible({ timeout: 15_000 })
  // Visible before it is chosen: this card behaves differently from the others.
  await expect(card.getByText('gongmun-form.txt')).toBeVisible()

  await card.getByRole('button').first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // …and the form is on the turn, where the model will read it.
  await expect(page.getByTitle(/토큰|내용 없음/).filter({ hasText: 'gongmun-form.txt' })).toBeVisible({
    timeout: 15_000,
  })

  // Clean up so the gallery does not fill with test rows.
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const again = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await again.getByRole('button', { name: `${name} 삭제` }).click()
  await expect(again).toHaveCount(0, { timeout: 15_000 })
})
