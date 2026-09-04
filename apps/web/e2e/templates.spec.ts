import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/** A person's own 시작점: created, found, picked, edited, deleted, and with a form file attached. */
test('내가 만든 시작점이 갤러리에 서고, 고르면 요청에 붙고, 고치고 지울 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  const openGallery = async () => {
    await page.getByRole('button', { name: '작업 시작하기' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
  }

  await openGallery()
  // The catalogue arrives from the server after the dialog opens.
  const cards = page.getByRole('dialog').locator('.grid > *')
  await expect(cards.first(), '갤러리가 비어 있다').toBeVisible({ timeout: 20_000 })

  await page.getByRole('button', { name: '내 시작점 만들기' }).click()
  const name = `공문 초안 ${Date.now()}`
  await page.getByLabel('이름').fill(name)
  await page.getByLabel('설명').fill('기관 공문 양식에 맞춘 초안')
  await page.getByLabel('준비물').fill('수신처, 제목')
  await page.getByLabel('문구').fill('아래 양식에 맞춰 공문을 써 줘.\n\n수신: ')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  // The form takes over the dialog; the grid is back once its heading has gone.
  await expect(page.getByRole('heading', { name: '시작점 만들기' })).toBeHidden({ timeout: 20_000 })

  // Searched: the gallery pages.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const card = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card.getByText('수신처')).toBeVisible()

  // Picking attaches to the turn and never sends; the box asks for the 준비물.
  await card.getByRole('button').first().click()
  const composer = page.getByLabel('프롬프트 입력')
  await expect(composer).toHaveValue('')
  await expect(composer).toHaveAttribute('placeholder', /수신처, 제목/)
  await expect(page).not.toHaveURL(/\/s\/[0-9a-f]{32}/)

  // Survives a reload.
  await page.reload()
  await openGallery()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const again = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await expect(again).toBeVisible({ timeout: 15_000 })

  // Editing opens the form on what was written and writes over the same card.
  await again.getByRole('button', { name: `${name} 수정` }).click()
  await expect(page.getByLabel('이름')).toHaveValue(name)
  await expect(page.getByLabel('준비물')).toHaveValue('수신처, 제목')
  const fixed = `${name} 개정`
  await page.getByLabel('이름').fill(fixed)
  await page.getByLabel('문구').fill('아래 양식에 맞춰 공문을 써 줘.\n\n수신자: ')
  await page.getByRole('button', { name: '저장', exact: true }).click()

  const edited = page.getByRole('dialog').locator('.grid > *').filter({ hasText: fixed })
  await expect(edited).toBeVisible({ timeout: 15_000 })
  // One card, not two.
  await expect(page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })).toHaveCount(1)

  await edited.getByRole('button').first().click()
  await expect(page.getByRole('button', { name: `${fixed} 시작점 해제` })).toBeVisible()

  await openGallery()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(fixed)
  const doomed = page.getByRole('dialog').locator('.grid > *').filter({ hasText: fixed })
  await doomed.getByRole('button', { name: `${fixed} 삭제` }).click()
  await expect(doomed).toHaveCount(0, { timeout: 15_000 })

  // Shipped 서식 are not deletable.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill('보고 문서')
  const shipped = page.getByRole('dialog').locator('.grid > *').filter({ hasText: '보고 문서' })
  await expect(shipped.getByRole('button', { name: /삭제/ })).toHaveCount(0)
})

/** A 시작점's form file rides along as an attachment when it is picked. */
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
  // Visible on the card.
  await expect(card.getByText('gongmun-form.txt')).toBeVisible()

  await card.getByRole('button').first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // The form is on the turn.
  await expect(page.getByTitle(/토큰|내용 없음/).filter({ hasText: 'gongmun-form.txt' })).toBeVisible({
    timeout: 15_000,
  })

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(name)
  const again = page.getByRole('dialog').locator('.grid > *').filter({ hasText: name })
  await again.getByRole('button', { name: `${name} 삭제` }).click()
  await expect(again).toHaveCount(0, { timeout: 15_000 })
})
