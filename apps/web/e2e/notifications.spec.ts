import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'

/**
 * Notifications and the installable app: the long-run question, the hidden-tab notice, 설정 › 알림
 * and 설정 › 앱, and the service worker (whole-site scope, never a stale index.html, offline page
 * only, push shown as a notification).
 *
 * Push runs against a fake browser side (`fakePush`): headless Chromium has no push service.
 */

/** Screenshots for review: `PWA_SHOTS=dir`; otherwise into the test's own output. */
function shot(testInfo: TestInfo, name: string) {
  const file = `${name}-${testInfo.project.name.split('/').pop()}.png`
  const dir = process.env.PWA_SHOTS
  if (!dir) return testInfo.outputPath(file)
  mkdirSync(dir, { recursive: true })
  return join(dir, file)
}

/** For review only: the shown notice drawn as a system notification would draw it. */
async function drawNotice(page: Page, title: string, body: string) {
  await page.evaluate(({ title, body }) => {
    const card = document.createElement('div')
    card.setAttribute('data-testid', 'notice-mock')
    card.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;width:min(360px,calc(100vw - 32px));display:flex;gap:12px;padding:14px 16px;border-radius:14px;background:#202124;color:#fff;font:14px system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.35)'
    card.innerHTML = '<img src="/icon-192.png" width="40" height="40" style="border-radius:10px"><div style="min-width:0"><div style="font-size:12px;opacity:.7">KloudChat · 127.0.0.1</div><div style="font-weight:600;margin-top:2px"></div><div style="opacity:.85;margin-top:2px"></div></div>'
    const lines = card.querySelectorAll('div > div')
    lines[2].textContent = title
    lines[3].textContent = body
    document.body.appendChild(card)
  }, { title, body })
}

/** Notification, service worker and push manager as a browser that grants and subscribes. */
async function fakePush(page: Page, { permission = 'default', subscribed = false } = {}) {
  await page.addInitScript(
    ({ permission, subscribed }) => {
      const w = window as unknown as Record<string, unknown>
      const shown: { title: string; options: NotificationOptions }[] = []
      const asked: string[] = []
      w.__shown = shown
      w.__asked = asked
      w.__hidden = false
      Object.defineProperty(document, 'visibilityState', { get: () => (w.__hidden ? 'hidden' : 'visible') })
      class FakeNotification {
        static permission: NotificationPermission = permission as NotificationPermission
        static async requestPermission() {
          asked.push('notifications')
          FakeNotification.permission = 'granted'
          return 'granted' as NotificationPermission
        }
        onclick: (() => void) | null = null
        constructor(title: string, options: NotificationOptions = {}) {
          shown.push({ title, options })
        }
        close() {}
      }
      w.Notification = FakeNotification
      let sub: unknown = null
      const makeSub = () => ({
        endpoint: 'https://fcm.googleapis.com/fcm/send/fake-device',
        options: { applicationServerKey: null },
        toJSON: () => ({
          endpoint: 'https://fcm.googleapis.com/fcm/send/fake-device',
          keys: { p256dh: 'B'.repeat(87), auth: 'A'.repeat(22) },
        }),
        unsubscribe: async () => {
          sub = null
          return true
        },
      })
      if (subscribed) sub = makeSub()
      const listeners: ((event: MessageEvent) => void)[] = []
      const registration = {
        scope: `${location.origin}/`,
        pushManager: {
          getSubscription: async () => sub,
          subscribe: async () => {
            sub = makeSub()
            return sub
          },
        },
        showNotification: async (title: string, options: NotificationOptions) => {
          shown.push({ title, options })
        },
      }
      Object.defineProperty(navigator, 'serviceWorker', {
        value: {
          register: async () => registration,
          ready: Promise.resolve(registration),
          getRegistration: async () => registration,
          addEventListener: (type: string, fn: (event: MessageEvent) => void) => {
            if (type === 'message') listeners.push(fn)
          },
          removeEventListener: () => {},
        },
      })
      // what the worker's notificationclick posts
      w.__swMessage = (data: unknown) => listeners.forEach((fn) => fn({ data } as MessageEvent))
    },
    { permission, subscribed },
  )
}

type Calls = { subscribes: unknown[]; prefs: unknown[]; removed: string[]; tests: number }

async function pushRoutes(page: Page, { role = 'user', devices = [] as Record<string, unknown>[] } = {}) {
  const calls: Calls = { subscribes: [], prefs: [], removed: [], tests: 0 }
  const listed = [...devices]
  await page.route('**/api/push/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/push/config')) {
      return route.fulfill({ json: { publicKey: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8', events: {} } })
    }
    if (path.endsWith('/push/subscriptions') && request.method() === 'GET') return route.fulfill({ json: listed })
    if (path.endsWith('/push/subscriptions') && request.method() === 'POST') {
      calls.subscribes.push(request.postDataJSON())
      const made = { id: 'd-here', device: 'Chrome · Linux', createdAt: '2026-11-15T07:00:00Z', lastSentAt: null }
      if (!listed.some((d) => d.id === made.id)) listed.push(made)
      return route.fulfill({ status: 201, json: made })
    }
    if (path.endsWith('/push/test')) {
      calls.tests += 1
      return route.fulfill({ json: { sent: 1 } })
    }
    if (path.endsWith('/push/unsubscribe')) return route.fulfill({ status: 204, body: '' })
    const gone = path.match(/\/push\/subscriptions\/([^/]+)$/)
    if (gone && request.method() === 'DELETE') {
      calls.removed.push(gone[1])
      listed.splice(listed.findIndex((d) => d.id === gone[1]), 1)
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fulfill({ status: 501, json: { detail: 'unmocked push call' } })
  })
  let prefs: Record<string, unknown> = { autoMemory: false, showUsage: false, streamResponses: true }
  await page.route('**/api/auth/me', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const patch = route.request().postDataJSON() as { preferences?: Record<string, unknown> }
    calls.prefs.push(patch)
    prefs = { ...prefs, ...(patch.preferences ?? {}) }
    return route.fulfill({
      json: {
        id: 'fixture-user', email: 'fixture@example.test', name: 'UI 검증', role, status: 'active',
        monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: '2026-10-09T07:00:00Z',
        preferences: prefs,
      },
    })
  })
  return calls
}

test.describe('the one-time question and tab notices', () => {
  test('a long run asks once, in the app; its end while hidden is a notice that opens the conversation', { tag: '@phone' }, async ({ page }, testInfo) => {
    await fakePush(page)
    const calls = await pushRoutes(page)
    const { unexpected } = await composerFixture(page, testInfo)
    // the turn's stream answers only when the test says so
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    await page.route(`**/api/sessions/${chatId}/messages`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await gate
      // the fixture answers and stores the turn
      return route.fallback()
    })
    await page.clock.install({ time: new Date('2026-11-15T07:00:00Z') })
    await page.goto(`/s/${chatId}`)
    // not on load
    await page.clock.fastForward(30_000)
    await expect(page.getByTestId('notify-ask')).toHaveCount(0)

    const input = page.getByLabel('프롬프트 입력')
    await input.fill('긴 작업')
    await input.press('Enter')
    await page.clock.fastForward(9_000)
    const ask = page.getByTestId('notify-ask')
    await expect(ask).toContainText('작업이 끝나면 알림으로 알려 드릴까요?')
    await page.waitForTimeout(400)
    await page.screenshot({ path: shot(testInfo, 'permission-prompt') })
    await ask.getByRole('button', { name: '알림 켜기' }).click()
    await expect(ask).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __asked: string[] }).__asked)).toEqual(['notifications'])
    // granted: this device subscribes for push too
    await expect.poll(() => calls.subscribes.length).toBe(1)
    expect(calls.subscribes[0]).toMatchObject({ endpoint: 'https://fcm.googleapis.com/fcm/send/fake-device' })

    // the job runs on while the person is elsewhere
    await page.clock.fastForward(20_000)
    await page.evaluate(() => ((window as unknown as { __hidden: boolean }).__hidden = true))
    release()
    await expect(page.getByText('확인했습니다.').last()).toBeVisible()
    await page.clock.fastForward(2_000)
    await expect.poll(() => page.evaluate(() => (window as unknown as { __shown: unknown[] }).__shown.length)).toBe(1)
    const [notice] = await page.evaluate(() => (window as unknown as { __shown: { title: string; options: NotificationOptions }[] }).__shown)
    expect(notice.title).toBe('답변이 준비됐습니다')
    expect(notice.options).toMatchObject({ body: '지원 사업 공고 정리', tag: `job:${chatId}`, data: { url: `/s/${chatId}` } })
    if (process.env.PWA_SHOTS) {
      await drawNotice(page, notice.title, String(notice.options.body))
      await page.screenshot({ path: shot(testInfo, 'notification-mock') })
    }

    // the click, as the worker relays it, opens the conversation
    await page.goto('/settings')
    await expect(page.getByRole('navigation', { name: '설정 메뉴' })).toBeVisible()
    await page.evaluate((url) => (window as unknown as { __swMessage: (d: unknown) => void }).__swMessage({ type: 'kchat:navigate', url }), `/s/${chatId}`)
    await expect(page).toHaveURL(new RegExp(`/s/${chatId}$`))
    // asked once: a second long run does not ask again
    await page.reload()
    await page.clock.fastForward(30_000)
    await expect(page.getByTestId('notify-ask')).toHaveCount(0)
    expect(unexpected).toEqual([])
  })

  test('a short run, or one that ends while the tab is in view, makes no notice', async ({ page }, testInfo) => {
    await fakePush(page, { permission: 'granted' })
    await pushRoutes(page)
    const { unexpected } = await composerFixture(page, testInfo)
    await page.clock.install({ time: new Date('2026-11-15T07:00:00Z') })
    await page.goto(`/s/${chatId}`)
    await page.evaluate(() => ((window as unknown as { __hidden: boolean }).__hidden = true))
    const input = page.getByLabel('프롬프트 입력')
    await input.fill('짧은 질문')
    await input.press('Enter')
    await expect(page.getByText('확인했습니다.').last()).toBeVisible()
    await page.clock.fastForward(5_000)
    expect(await page.evaluate(() => (window as unknown as { __shown: unknown[] }).__shown.length)).toBe(0)
    await expect(page.getByTestId('notify-ask')).toHaveCount(0)
    expect(unexpected).toEqual([])
  })
})

test.describe('설정 › 알림', () => {
  test('this device turns on, an event switches, devices are listed and removed', async ({ page }, testInfo) => {
    await fakePush(page)
    const calls = await pushRoutes(page, {
      devices: [{ id: 'd-old', device: 'Safari · iOS', createdAt: '2026-11-01T07:00:00Z', lastSentAt: '2026-11-14T07:00:00Z' }],
    })
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings/notifications')
    await expect(page.getByTestId('notify-device-state')).toHaveText('이 기기에서는 알림이 꺼져 있습니다.')
    // an admin-only event is not offered to a member
    await expect(page.getByText('공개 승인 요청')).toHaveCount(0)
    await expect(page.getByRole('switch', { name: '작업 완료' })).toBeChecked()
    await expect(page.getByRole('switch', { name: '문의 답변' })).not.toBeChecked()
    await page.getByRole('switch', { name: '문의 답변' }).click()
    await expect.poll(() => calls.prefs.length).toBe(1)
    expect(calls.prefs[0]).toEqual({ preferences: { pushEvents: { issue: true } } })

    await page.getByRole('button', { name: '이 기기에서 알림 켜기' }).click()
    await expect(page.getByTestId('notify-device-state')).toHaveText('이 기기에서 알림을 받습니다.')
    expect(calls.subscribes).toHaveLength(1)
    const here = page.getByTestId('push-device').filter({ hasText: '이 기기' })
    await expect(here).toContainText('Chrome · Linux')
    await page.getByRole('button', { name: '테스트 알림' }).click()
    await expect(page.getByText('테스트 알림을 보냈습니다.')).toBeVisible()
    expect(calls.tests).toBe(1)
    await page.screenshot({ path: shot(testInfo, 'settings-notifications'), fullPage: true })

    await page.getByRole('button', { name: 'Safari · iOS 삭제' }).click()
    await expect(page.getByTestId('push-device')).toHaveCount(1)
    expect(calls.removed).toEqual(['d-old'])
    expect(unexpected).toEqual([])
  })

  test('at phone width the section reads in one column', { tag: '@phone' }, async ({ page }, testInfo) => {
    await fakePush(page, { permission: 'granted', subscribed: true })
    await pushRoutes(page, { devices: [{ id: 'd-here', device: 'Chrome · Android', createdAt: '2026-11-01T07:00:00Z', lastSentAt: null }] })
    await page.addInitScript(() => localStorage.setItem('kchat.push.device', 'd-here'))
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings/notifications')
    await expect(page.getByTestId('notify-device-state')).toHaveText('이 기기에서 알림을 받습니다.')
    await expect(page.getByTestId('push-device')).toContainText('이 기기')
    const [width, viewport] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
    expect(width).toBeLessThanOrEqual(viewport)
    await page.screenshot({ path: shot(testInfo, 'settings-notifications'), fullPage: true })
    expect(unexpected).toEqual([])
  })

  test('an iPhone in Safari is told push needs the home-screen app', { tag: '@phone' }, async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1',
      })
    })
    await pushRoutes(page)
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings/notifications')
    await expect(page.getByTestId('notify-device-state')).toHaveText('iPhone·iPad는 홈 화면에 추가한 앱에서만 알림을 받습니다(iOS 16.4 이상).')
    await expect(page.getByRole('button', { name: '이 기기에서 알림 켜기' })).toHaveCount(0)
    expect(unexpected).toEqual([])
  })
})

test.describe('설정 › 앱', () => {
  test('Chromium installs through its own prompt', async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      const w = window as unknown as Record<string, unknown>
      w.__prompted = 0
      window.addEventListener('load', () => {
        const event = new Event('beforeinstallprompt') as Event & { prompt: () => Promise<void>; userChoice: Promise<unknown> }
        event.prompt = async () => {
          w.__prompted = Number(w.__prompted) + 1
        }
        event.userChoice = Promise.resolve({ outcome: 'accepted' })
        window.dispatchEvent(event)
      })
    })
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings/app')
    await expect(page.getByTestId('app-install-state')).toHaveText('설치하면 브라우저 탭 없이 따로 열립니다.')
    await page.screenshot({ path: shot(testInfo, 'settings-app'), fullPage: true })
    await page.getByRole('main').getByRole('button', { name: '앱으로 설치' }).click()
    expect(await page.evaluate(() => (window as unknown as { __prompted: number }).__prompted)).toBe(1)
    expect(unexpected).toEqual([])
  })

  test('the installed app says so; an iPhone gets the 홈 화면에 추가 steps', { tag: '@phone' }, async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1',
      })
    })
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings/app')
    await expect(page.getByTestId('app-install-state')).toHaveText('Safari 공유 메뉴의 「홈 화면에 추가」로 설치합니다.')
    await page.getByRole('button', { name: '설치 방법 보기' }).click()
    await expect(page.getByRole('dialog', { name: '홈 화면에 추가하기' })).toBeVisible()
    await page.screenshot({ path: shot(testInfo, 'settings-app-ios') })
    await page.keyboard.press('Escape')
    // opened from the home screen
    await page.addInitScript(() => Object.defineProperty(navigator, 'standalone', { get: () => true }))
    await page.reload()
    await expect(page.getByTestId('app-install-state')).toHaveText('앱으로 설치해 쓰고 있습니다.')
    await expect(page.getByRole('main').getByRole('button', { name: '설치 방법 보기' })).toHaveCount(0)
    expect(unexpected).toEqual([])
  })
})

test.describe('service worker', () => {
  test.use({ serviceWorkers: 'allow' })

  test('registers for the site, never serves a stale index.html, keeps only the offline page', async ({ page, context }, testInfo) => {
    const { unexpected } = await composerFixture(page, testInfo)
    await page.goto('/settings')
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope)
    expect(scope).toBe(`${new URL(page.url()).origin}/`)
    // the next load is under the worker's control
    await page.reload()
    await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
    const cached = await page.evaluate(async () => {
      const out: string[] = []
      for (const name of await caches.keys()) {
        for (const request of await (await caches.open(name)).keys()) out.push(`${name} ${new URL(request.url).pathname}`)
      }
      return out
    })
    expect(cached).toEqual(['kchat-offline-1 /offline.html'])

    // a deploy: the server now serves another index.html; the very next load is it
    await context.route('**/*', async (route) => {
      // a page address (the worker's fetch of a navigation is no 'document' request)
      const path = new URL(route.request().url()).pathname
      if (path.startsWith('/api/') || path.includes('.')) return route.fallback()
      const response = await route.fetch()
      const html = (await response.text()).replace('<head>', '<head><meta name="kchat-build" content="next">')
      return route.fulfill({ response, body: html })
    })
    await page.reload()
    await expect(page.locator('meta[name="kchat-build"]')).toHaveAttribute('content', 'next')
    await context.unroute('**/*')

    // offline: the small page, not an old app
    await context.setOffline(true)
    await page.goto('/projects').catch(() => undefined)
    await expect(page.getByRole('heading', { name: '인터넷에 연결되어 있지 않습니다' })).toBeVisible()
    await context.setOffline(false)
    expect(unexpected.filter((u) => !u.includes('Failed to fetch') && !u.includes('NetworkError'))).toEqual([])
  })
})
