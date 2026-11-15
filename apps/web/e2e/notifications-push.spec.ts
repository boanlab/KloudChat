import { expect, test } from '@playwright/test'
import { composerFixture } from './composer-fixture'

/** The service worker shows a pushed message as a notification that carries the conversation's
 * address, and pulls any other address back to this site. Push arrives through Chromium
 * DevTools; the headless shell refuses notifications whatever is granted, so this file runs in
 * Chromium's new headless mode. */

test.use({ serviceWorkers: 'allow', channel: 'chromium' })

test('a push message shows as a notification with the conversation address', async ({ page, context, browserName }, testInfo) => {
  test.skip(browserName !== 'chromium', 'push delivered through Chromium DevTools')
  await context.grantPermissions(['notifications'])
  const { unexpected } = await composerFixture(page, testInfo)
  await page.goto('/settings')
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready
  })
  const cdp = await context.newCDPSession(page)
  await cdp.send('ServiceWorker.enable')
  const registrationId = await new Promise<string>((resolve) => {
    cdp.on('ServiceWorker.workerRegistrationUpdated', ({ registrations }) => {
      const live = registrations.find((r: { isDeleted: boolean }) => !r.isDeleted)
      if (live) resolve(live.registrationId)
    })
  })
  const origin = new URL(page.url()).origin
  await cdp.send('ServiceWorker.deliverPushMessage', {
    origin,
    registrationId,
    data: JSON.stringify({ title: '보고서가 완성됐습니다', body: '분기 보고', url: '/s/abc?artifact=a1', tag: 'job:abc' }),
  })
  await expect
    .poll(() => page.evaluate(async () => (await (await navigator.serviceWorker.ready).getNotifications()).map((n) => [n.title, n.body, n.tag, (n.data as { url: string }).url])))
    .toEqual([['보고서가 완성됐습니다', '분기 보고', 'job:abc', '/s/abc?artifact=a1']])
  // a payload pointing elsewhere is pulled back to this site
  await cdp.send('ServiceWorker.deliverPushMessage', {
    origin,
    registrationId,
    data: JSON.stringify({ title: 't', url: 'https://evil.example/', tag: 'x' }),
  })
  await expect
    .poll(() => page.evaluate(async () => (await (await navigator.serviceWorker.ready).getNotifications({ tag: 'x' })).map((n) => (n.data as { url: string }).url)))
    .toEqual(['/'])
  expect(unexpected).toEqual([])
})
