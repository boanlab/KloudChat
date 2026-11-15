/** Service worker, notification permission and this device's web push subscription. */
import { pushApi } from './api'
import type { Preferences, PushEvent } from '@/types'

/** Settings order; on by default for job results and shared conversations. */
export const PUSH_EVENTS: PushEvent[] = ['job_done', 'job_failed', 'shared', 'review', 'decided', 'issue', 'credits']
export const PUSH_DEFAULTS: Record<PushEvent, boolean> = {
  job_done: true,
  job_failed: true,
  shared: true,
  review: false,
  decided: false,
  issue: false,
  credits: false,
}

export function wantsEvent(prefs: Preferences | undefined, event: PushEvent): boolean {
  return prefs?.pushEvents?.[event] ?? PUSH_DEFAULTS[event]
}

/** The device id the server gave this browser's subscription; marks 「이 기기」 in the list. */
const DEVICE_KEY = 'kchat.push.device'

export function isIOS(): boolean {
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    // iPadOS reports as Mac; touch points give it away.
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  )
}

export function isStandalone(): boolean {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  )
}

/** What this browser can do: `ios-install` = iOS Safari, push only from the home-screen app (16.4+). */
export type PushSupport = 'ok' | 'insecure' | 'ios-install' | 'unsupported'

export function pushSupport(): PushSupport {
  if (!window.isSecureContext) return 'insecure'
  const capable = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  if (isIOS() && !isStandalone()) return 'ios-install'
  return capable ? 'ok' : 'unsupported'
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return 'Notification' in window ? Notification.permission : 'unsupported'
}

let registering: Promise<ServiceWorkerRegistration | null> | null = null

/** Registers `/sw.js` once; null where service workers are unavailable. The worker caches nothing but the offline page. */
export function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (registering) return registering
  registering = (async () => {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return null
    try {
      // updateViaCache 'none': the browser checks sw.js itself on every navigation, past any HTTP cache
      return await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' })
    } catch {
      return null
    }
  })()
  return registering
}

async function readyRegistration(): Promise<ServiceWorkerRegistration | null> {
  const reg = await registerServiceWorker()
  if (!reg) return null
  // `ready` waits for an active worker; bounded so a stuck install never hangs the settings screen
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<ServiceWorkerRegistration>((resolve) => window.setTimeout(() => resolve(reg), 4000)),
  ])
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (base64url.length % 4)) % 4)
  const raw = atob((base64url + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

function sameKey(a: ArrayBuffer | null, b: Uint8Array): boolean {
  if (!a) return false
  const x = new Uint8Array(a)
  return x.length === b.length && x.every((v, i) => v === b[i])
}

export function currentDeviceId(): string | null {
  try {
    return localStorage.getItem(DEVICE_KEY)
  } catch {
    return null
  }
}

function rememberDevice(id: string | null) {
  try {
    if (id) localStorage.setItem(DEVICE_KEY, id)
    else localStorage.removeItem(DEVICE_KEY)
  } catch {
    /* storage unavailable: the list just won't mark this device */
  }
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== 'ok') return null
  const reg = await readyRegistration()
  return (await reg?.pushManager.getSubscription().catch(() => null)) ?? null
}

/** Asks for permission (only from a click) and resolves to the answer. */
export async function askPermission(): Promise<NotificationPermission | 'unsupported'> {
  if (!('Notification' in window)) return 'unsupported'
  if (Notification.permission !== 'default') return Notification.permission
  try {
    return await Notification.requestPermission()
  } catch {
    return Notification.permission
  }
}

/** Subscribes this browser and tells the server; false when push is unavailable or refused. */
export async function subscribeThisDevice(): Promise<boolean> {
  if (pushSupport() !== 'ok') return false
  if ((await askPermission()) !== 'granted') return false
  const reg = await readyRegistration()
  if (!reg) return false
  const { publicKey } = await pushApi.config()
  const key = keyBytes(publicKey)
  let sub = await reg.pushManager.getSubscription()
  // made under another server key (keys re-made, other install): replace it
  if (sub && !sameKey(sub.options.applicationServerKey, key)) {
    await sub.unsubscribe().catch(() => false)
    sub = null
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
  const json = sub.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) return false
  const device = await pushApi.subscribe({ endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } })
  rememberDevice(device.id)
  return true
}

/** Stops push on this browser, here and on the server. */
export async function unsubscribeThisDevice(): Promise<void> {
  const sub = await currentSubscription()
  if (sub) {
    await pushApi.unsubscribe(sub.endpoint).catch(() => undefined)
    await sub.unsubscribe().catch(() => false)
  }
  rememberDevice(null)
}

/** On start: a granted, subscribed browser re-registers (the account may have changed); never asks. */
export async function syncThisDevice(): Promise<void> {
  if (notificationPermission() !== 'granted' || pushSupport() !== 'ok') return
  const sub = await currentSubscription()
  if (!sub) return
  const json = sub.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) return
  const device = await pushApi
    .subscribe({ endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } })
    .catch(() => null)
  if (device) rememberDevice(device.id)
}

/** Before sign-out: this browser stops receiving the account's notices, and the next person here
 * starts unsubscribed (best effort, bounded). */
export async function leaveOnSignOut(): Promise<void> {
  const sub = await Promise.race([currentSubscription(), new Promise<null>((r) => window.setTimeout(() => r(null), 1500))])
  if (!sub) return
  await Promise.race([pushApi.unsubscribe(sub.endpoint).catch(() => undefined), new Promise((r) => window.setTimeout(r, 1500))])
  await sub.unsubscribe().catch(() => false)
  rememberDevice(null)
}
