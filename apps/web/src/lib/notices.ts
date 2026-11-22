/** Tab notices: a long job that ends while this tab is hidden shows a browser notification. The
 * permission is asked once, in the app, the first time a job runs long — never on page load. */
import { useSyncExternalStore } from 'react'
import { currentLang, translate } from './i18n'
import { askPermission, notificationPermission, pushSupport, subscribeThisDevice, wantsEvent } from './push'
import { useStore } from '@/store/useStore'
import type { Job, PushEvent, Session } from '@/types'

const tr = (text: string) => translate(currentLang(), text)

/** A turn shorter than this is no job worth a notice. */
export const LONG_RUN_MS = 20_000
/** The in-app question shows once a turn has run this long. */
export const ASK_AFTER_MS = 8_000
const ASKED_KEY = 'kchat.notify.asked'
const TERMINAL = new Set(['succeeded', 'failed', 'canceled'])

// ── the one-time question ─────────────────────────────────────────────────

let asking = false
const askers = new Set<() => void>()

function setAsking(value: boolean) {
  if (asking === value) return
  asking = value
  askers.forEach((fn) => fn())
}

function alreadyAsked(): boolean {
  try {
    return localStorage.getItem(ASKED_KEY) === '1'
  } catch {
    return true
  }
}

function markAsked() {
  try {
    localStorage.setItem(ASKED_KEY, '1')
  } catch {
    /* storage unavailable: the question may come back next visit */
  }
}

function shouldAsk(): boolean {
  return notificationPermission() === 'default' && window.isSecureContext && !alreadyAsked()
}

/** Whether the in-app question is showing (for `NotifyPrompt`). */
export function useNotifyAsk(): boolean {
  return useSyncExternalStore(
    (fn) => {
      askers.add(fn)
      return () => askers.delete(fn)
    },
    () => asking,
  )
}

/** 「알림 켜기」: the browser's permission prompt, then web push for this device where it works. */
export async function acceptNotices(): Promise<NotificationPermission | 'unsupported'> {
  markAsked()
  setAsking(false)
  const answer = await askPermission()
  if (answer === 'granted' && pushSupport() === 'ok') await subscribeThisDevice().catch(() => false)
  return answer
}

export function declineNotices() {
  markAsked()
  setAsking(false)
}

// ── showing one ───────────────────────────────────────────────────────────

/** Notification clicks land here; `NoticeLinks` routes them. */
export const NAVIGATE_EVENT = 'kchat:navigate'

export function openNoticeUrl(url: string) {
  const safe = url.startsWith('/') && !url.startsWith('//') ? url : '/'
  window.dispatchEvent(new CustomEvent(NAVIGATE_EVENT, { detail: safe }))
}

export interface TabNotice {
  title: string
  body: string
  url: string
  tag: string
}

/** Shows `notice` when this tab is hidden, permission is granted and the event is switched on. */
export async function showTabNotice(notice: TabNotice, event: PushEvent): Promise<boolean> {
  if (document.visibilityState === 'visible') return false
  if (notificationPermission() !== 'granted') return false
  if (!wantsEvent(useStore.getState().user?.preferences, event)) return false
  const options: NotificationOptions = {
    body: notice.body,
    tag: notice.tag,
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: notice.url },
  }
  try {
    // via the worker: same tag replaces a push notice of the same job; Android Chrome disallows page-made notifications
    const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : undefined
    if (reg) {
      await reg.showNotification(notice.title, options)
      return true
    }
    const made = new Notification(notice.title, options)
    made.onclick = () => {
      window.focus()
      openNoticeUrl(notice.url)
      made.close()
    }
    return true
  } catch {
    return false
  }
}

// Finished-run notices

const DONE: Record<string, [string, string]> = {
  report: ['보고서가 완성됐습니다', '보고서를 만들지 못했습니다'],
  slides: ['슬라이드가 완성됐습니다', '슬라이드를 만들지 못했습니다'],
  document: ['문서가 완성됐습니다', '문서를 만들지 못했습니다'],
  chat: ['답변이 준비됐습니다', '답변을 받지 못했습니다'],
  av: ['영상이 완성됐습니다', '영상을 만들지 못했습니다'],
}

/** The notice for a session whose run just ended; null when there is nothing to tell (stopped,
 * waiting on the person). */
export function runNotice(session: Session): { notice: TabNotice; event: PushEvent } | null {
  if (session.pending) return null
  const last = session.messages[session.messages.length - 1]
  if (last?.failure === 'stopped') return null
  const failed = !last || last.role !== 'assistant' || Boolean(last.error) || Boolean(last.failure)
  const made = last?.artifactIds?.[0] ?? null
  const kind = session.kind === 'report' || session.kind === 'slides' ? session.kind : made ? 'document' : 'chat'
  const [done, fail] = DONE[kind]
  return {
    event: failed ? 'job_failed' : 'job_done',
    notice: {
      title: tr(failed ? fail : done),
      body: session.title || 'KloudChat',
      url: `/s/${session.id}${made && !failed ? `?artifact=${made}` : ''}`,
      tag: `job:${session.id}`,
    },
  }
}

export function jobNotice(job: Job, session: Session | undefined): { notice: TabNotice; event: PushEvent } | null {
  if (job.status === 'canceled') return null
  const ok = job.status === 'succeeded'
  const [done, fail] = DONE.av
  return {
    event: ok ? 'job_done' : 'job_failed',
    notice: { title: tr(ok ? done : fail), body: session?.title || 'KloudChat', url: `/s/${job.sessionId}`, tag: `job:${job.sessionId}` },
  }
}

// ── watching the store ────────────────────────────────────────────────────

/** Starts watching runs and jobs; returns the stop. Settling waits a moment so the stored
 * transcript (artifact, failure) has replaced the streamed one. */
export function watchLongJobs({ settleMs = 1500 }: { settleMs?: number } = {}): () => void {
  const started = new Map<string, number>()
  const timers = new Set<number>()
  const later = (fn: () => void, ms: number) => {
    const id = window.setTimeout(() => {
      timers.delete(id)
      fn()
    }, ms)
    timers.add(id)
  }
  const unsubscribe = useStore.subscribe((state, prev) => {
    for (const id of Object.keys(state.running)) {
      if (id in prev.running) continue
      started.set(id, Date.now())
      later(() => {
        if (id in useStore.getState().running && shouldAsk()) setAsking(true)
      }, ASK_AFTER_MS)
    }
    for (const id of Object.keys(prev.running)) {
      if (id in state.running) continue
      const at = started.get(id)
      started.delete(id)
      if (at === undefined || Date.now() - at < LONG_RUN_MS) continue
      later(() => {
        const session = useStore.getState().sessions.find((s) => s.id === id)
        const told = session ? runNotice(session) : null
        if (told) void showTabNotice(told.notice, told.event)
      }, settleMs)
    }
    if (state.jobs !== prev.jobs) {
      const before = new Map(prev.jobs.map((j) => [j.id, j.status]))
      for (const job of state.jobs) {
        const was = before.get(job.id)
        if (was === undefined || TERMINAL.has(was) || !TERMINAL.has(job.status)) continue
        const told = jobNotice(job, state.sessions.find((s) => s.id === job.sessionId))
        if (told) void showTabNotice(told.notice, told.event)
      }
      // a video job starting is a long job starting
      if (state.jobs.some((j) => !TERMINAL.has(j.status) && !before.has(j.id)) && shouldAsk()) setAsking(true)
    }
  })
  return () => {
    unsubscribe()
    timers.forEach((id) => window.clearTimeout(id))
    timers.clear()
  }
}
