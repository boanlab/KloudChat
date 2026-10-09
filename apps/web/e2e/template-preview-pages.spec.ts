import { readFile } from 'node:fs/promises'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { openTemplates } from './composer-tools'

test.use({ serviceWorkers: 'block' })

/** A document 서식's gallery preview is laid out as the page view lays out a report: the cover
 *  alone on page 1, the body from page 2. The preview page is the API's own output for
 *  doc-client-proposal (`design_templates.preview`), saved as a fixture. */

const at = '2026-10-04T00:00:00.000Z'
const row = {
  id: 'doc-client-proposal', kind: 'document', surface: 'report', name: '고객 제안서',
  description: 'RFP 목차를 따르는 고객 제안', category: '업무', fills: ['고객사와 사업명'],
  examplePrompt: '고객 제안서를 써 줘.', nameEn: 'Client proposal', descriptionEn: '', categoryEn: 'Work',
  fillsEn: [], examplePromptEn: '', checks: [], formFormat: '',
  hasPreview: true, slideCount: 0,
}

async function fixture(page: Page, testInfo: TestInfo) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const preview = await readFile(new URL('./fixtures/client-proposal-preview.html', import.meta.url), 'utf8')
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && path === '/design-templates/doc-client-proposal/preview') {
      return route.fulfill({ body: preview, contentType: 'text/html; charset=utf-8' })
    }
    if (method === 'GET' && path === '/design-templates') return route.fulfill({ json: [row] })
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Preview fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.abort('blockedbyclient')
  })
  await page.goto('/new/report')
}

test('a document 서식 previews as pages: the cover alone, the body from page 2', async ({ page }, testInfo) => {
  await fixture(page, testInfo)
  await openTemplates(page)
  const dialog = page.getByRole('dialog')

  // The card shows page 1: the cover.
  const card = dialog.locator('[data-page-count]').first()
  await expect(card).toHaveAttribute('data-page-count', /^[2-9]|\d{2,}$/, { timeout: 30_000 })
  const thumb = dialog.frameLocator('iframe').first()
  await expect(thumb.locator('.pagedjs_page').first().locator('header.tb-cover')).toBeAttached()
  await page.screenshot({ path: testInfo.outputPath('card.png') })

  await dialog.getByRole('button', { name: '전체 미리보기' }).first().click()
  const full = page.getByRole('dialog').last()
  const pages = full.locator('[data-page-count]')
  await expect(pages).toHaveAttribute('data-page-count', /^[2-9]|\d{2,}$/, { timeout: 30_000 })
  const total = Number(await pages.getAttribute('data-page-count'))
  await expect(full.getByText(`1 / ${total}쪽`)).toBeVisible()
  const frame = full.frameLocator('iframe')
  const first = frame.locator('.pagedjs_page').first()
  await expect(first.locator('header.tb-cover h1')).toHaveText('민원 상담 챗봇 구축 제안서')
  // Nothing else on the cover page: the first section opens page 2.
  await expect(first.locator('section')).toHaveCount(0)
  await expect(frame.locator('.pagedjs_page').nth(1).locator('section h2').first()).toBeAttached()
  // The title sits on the upper third of the sheet, the head table well under it.
  const place = await first.evaluate((sheet) => {
    const box = sheet.getBoundingClientRect()
    const title = sheet.querySelector('header.tb-cover h1')!.getBoundingClientRect()
    const table = sheet.querySelector('header.tb-cover table')!.getBoundingClientRect()
    return { title: (title.top - box.top) / box.height, gap: table.top - title.bottom, width: table.width / box.width }
  })
  expect(place.title).toBeGreaterThan(0.25)
  expect(place.title).toBeLessThan(0.42)
  expect(place.gap).toBeGreaterThan(40)
  expect(place.width).toBeLessThan(0.7)
  await page.screenshot({ path: testInfo.outputPath('full.png') })

  // Scrolling the box moves the page count on.
  await full.locator('[data-preview-scroll]').evaluate((box) => { box.scrollTop = box.scrollHeight })
  await expect(full.getByText(`${total} / ${total}쪽`)).toBeVisible()
})
