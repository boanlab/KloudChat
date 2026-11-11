import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'

/**
 * What the edge adds around the app: the production headers (served by `vite preview` from
 * security-headers.conf) leave the main screens free of CSP violations, a newer build on the
 * server is offered as a reload, a file over the upload limit is refused with the limit named
 * (before sending, and when a proxy answers 413), and an uncaught error reaches the server log.
 */

test.use({ serviceWorkers: 'block' })

/** Violations the page reports, gathered from before its first script runs. */
async function watchViolations(page: Page) {
  await page.addInitScript(() => {
    const seen: string[] = []
    ;(window as unknown as { __violations: string[] }).__violations = seen
    document.addEventListener('securitypolicyviolation', (e) => {
      seen.push(`${e.violatedDirective} ${e.blockedURI}`)
    })
  })
  return () => page.evaluate(() => (window as unknown as { __violations: string[] }).__violations)
}

test('the main screens load under the production headers with no CSP violation', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  const violations = await watchViolations(page)
  const response = await page.goto('/')
  const headers = response!.headers()
  expect(headers['content-security-policy']).toContain("script-src 'self'")
  expect(headers['content-security-policy']).toContain("object-src 'none'")
  expect(headers['x-content-type-options']).toBe('nosniff')
  expect(headers['referrer-policy']).toBe('strict-origin-when-cross-origin')
  expect(headers['permissions-policy']).toContain('microphone=(self)')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.goto(`/s/${chatId}`)
  await expect(page.getByText('이번 분기 지원 사업 공고를 세 줄로 정리해 줘.').first()).toBeVisible()
  await page.goto('/files')
  await expect(page.getByRole('heading').first()).toBeVisible()
  expect(await violations()).toEqual([])
  expect(unexpected).toEqual([])
})

test('a newer build on the server is offered as a reload, not forced', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  let served: string | null = null
  await page.route('**/version.json', (route) =>
    served ? route.fulfill({ json: { version: served } }) : route.continue(),
  )
  await page.goto('/')
  const notice = page.getByTestId('new-version')
  // The same build: back in view, nothing to say.
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await page.waitForTimeout(300)
  await expect(notice).toHaveCount(0)

  served = 'a-newer-build'
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(notice).toContainText('새 버전이 있습니다')
  await expect(notice.getByRole('button', { name: '새로고침' })).toBeVisible()
  // The page keeps working underneath; the notice can be put away.
  await expect(page.getByLabel('프롬프트 입력')).toBeEditable()
  await notice.getByRole('button', { name: '닫기' }).click()
  await expect(notice).toHaveCount(0)
  expect(unexpected).toEqual([])
})

test('a file over the limit is refused before it is sent, naming the limit', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  let uploads = 0
  await page.route('**/api/auth/config', (route) => route.fulfill({ json: {
    brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'], privacy: { externalDataGuard: false },
    passwordResetEnabled: false, dictationEnabled: false, upload: { maxMb: 1 },
  } }))
  await page.route('**/api/files', (route) => {
    uploads += 1
    return route.fallback()
  })
  await page.goto('/')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'big.txt', mimeType: 'text/plain', buffer: Buffer.alloc(1024 * 1024 + 1, 97),
  })
  await expect(page.getByRole('alert')).toContainText('big.txt — 파일이 너무 큽니다. 한 파일에 1MB까지 올릴 수 있습니다.')
  expect(uploads).toBe(0)
  expect(unexpected).toEqual([])
})

test('a proxy refusing the body with 413 reads as the same limit, not a code', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.route('**/api/files', (route) => route.fulfill({
    status: 413, contentType: 'text/html', body: '<html><body>413 Request Entity Too Large</body></html>',
  }))
  await page.goto('/')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.getByLabel('파일 선택').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('메모') })
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('notes.txt — 파일이 너무 큽니다. 한 파일에 200MB까지 올릴 수 있습니다.')
  await expect(alert).not.toContainText('http_413')
  expect(unexpected).toEqual([])
})

test('an uncaught error is reported once, with no token or page content', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  const reports: Record<string, unknown>[] = []
  await page.route('**/api/client-errors', (route) => {
    reports.push(route.request().postDataJSON())
    return route.fulfill({ status: 204, body: '' })
  })
  await page.goto(`/s/${chatId}?t=secret-share`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.evaluate(() => {
    const fail = () => {
      const error = new Error('render blew up near eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl')
      window.dispatchEvent(new ErrorEvent('error', { error, message: error.message }))
    }
    fail()
    fail()
  })
  await expect.poll(() => reports.length).toBe(1)
  await page.waitForTimeout(300)
  expect(reports).toHaveLength(1)
  const [report] = reports
  expect(report.kind).toBe('error')
  expect(report.message).toBe('render blew up near [token]')
  expect(report.route).toBe(`/s/${chatId}`)
  expect(typeof report.version).toBe('string')
  expect(JSON.stringify(report)).not.toContain('secret-share')
  expect(unexpected).toEqual([])
})
