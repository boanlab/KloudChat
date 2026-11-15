/* KloudChat service worker: push notices, notification clicks, an offline page.
 *
 * It caches nothing of the app: no bundle, no index.html. Every request goes to the network as
 * if there were no worker, so a deploy is never hidden behind an old build. Only a page load that
 * fails outright (offline) gets the small offline page, the one file kept here.
 * A new sw.js takes over at once (skipWaiting + clients.claim); the app's own version check
 * offers the reload for the page itself.
 */
const OFFLINE_URL = '/offline.html'
const CACHE = 'kchat-offline-1'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' })))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key)
      await self.clients.claim()
    })(),
  )
})

self.addEventListener('fetch', (event) => {
  // page loads only; scripts, styles, API calls and streams never pass through here
  if (event.request.mode !== 'navigate') return
  event.respondWith(
    (async () => {
      try {
        return await fetch(event.request)
      } catch {
        const cache = await caches.open(CACHE)
        return (await cache.match(OFFLINE_URL)) || Response.error()
      }
    })(),
  )
})

/** A same-origin path, or home. */
function safePath(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') ? url : '/'
}

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  const title = typeof data.title === 'string' && data.title ? data.title : 'KloudChat'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: typeof data.body === 'string' ? data.body : '',
      tag: typeof data.tag === 'string' && data.tag ? data.tag : undefined,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: safePath(data.url) },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = safePath(event.notification.data && event.notification.data.url)
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      const open = windows.find((client) => new URL(client.url).origin === self.location.origin)
      if (open) {
        await open.focus()
        // the app routes it, keeping the open page's state
        open.postMessage({ type: 'kchat:navigate', url })
        return
      }
      await self.clients.openWindow(url)
    })(),
  )
})
