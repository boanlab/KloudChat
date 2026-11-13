import { WifiOff, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { IssueReportDialog } from '@/components/issues/IssueReportDialog'
import { useIssueBadge, useIssues } from '@/lib/issues'
import { routeTitle, setBrandTitle, setPageTitle, setUnreadTitle } from '@/lib/pageTitle'
import { useBotAttentionBadge } from '@/lib/publishing'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { KeyboardShortcuts } from './KeyboardShortcuts'
import { SearchDialog } from './SearchDialog'
import { TopBar } from './TopBar'
import { Sidebar } from './Sidebar'
import { useT } from '@/lib/useT'

/** Undo toast for a pending delete; in the shell to survive navigation. */
function UndoBar() {
  const t = useT()
  const pending = useStore((s) => s.pendingDelete)
  if (!pending) return null
  return (
    <div
      role="status"
      className="animate-fade-up absolute bottom-4 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <span className="min-w-0 truncate">
        {t('{name} 삭제됨').replace('{name}', pending.label)}
      </span>
      <button
        onClick={pending.undo}
        className="shrink-0 font-medium text-accent hover:underline"
      >
        {t('실행 취소')}
      </button>
    </div>
  )
}

/** Passing neutral message (「바로 쓰려고 내 스킬에 추가했어요 · 빼기」); outlives navigation. */
function ToastBar() {
  const t = useT()
  const toast = useStore((s) => s.toast)
  const setToast = useStore((s) => s.setToast)
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 8_000)
    return () => clearTimeout(timer)
  }, [toast, setToast])
  if (!toast) return null
  return (
    <div
      role="status"
      data-testid="toast"
      className="animate-fade-up absolute bottom-16 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <span className="min-w-0">{toast.text}</span>
      {toast.action && (
        <button
          onClick={() => {
            toast.action?.run()
            setToast(null)
          }}
          className="shrink-0 font-medium text-accent hover:underline"
        >
          {toast.action.label}
        </button>
      )}
      <button
        onClick={() => setToast(null)}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}

/** Said while the browser has no network; requests fail until it is back. */
function OfflineBar() {
  const t = useT()
  const [offline, setOffline] = useState(() => !navigator.onLine)
  useEffect(() => {
    const down = () => setOffline(true)
    const up = () => setOffline(false)
    window.addEventListener('offline', down)
    window.addEventListener('online', up)
    return () => {
      window.removeEventListener('offline', down)
      window.removeEventListener('online', up)
    }
  }, [])
  if (!offline) return null
  return (
    <div
      role="status"
      data-testid="offline-bar"
      className="pointer-events-none absolute top-2 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-2 rounded-full border border-line bg-panel px-3 py-1 text-sm text-muted shadow-float"
    >
      <WifiOff size={14} aria-hidden className="shrink-0" />
      <span className="min-w-0">{t('인터넷 연결이 끊겼습니다. 연결되면 다시 시도하세요.')}</span>
    </div>
  )
}

/** Error toast for failures with no screen of their own. */
function NoticeBar() {
  const t = useT()
  const notice = useStore((s) => s.notice)
  const setNotice = useStore((s) => s.setNotice)
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 10_000)
    return () => clearTimeout(timer)
  }, [notice, setNotice])
  if (!notice) return null
  return (
    <div
      role="alert"
      className="animate-fade-up absolute top-4 left-1/2 z-50 flex max-w-[92vw] -translate-x-1/2 items-start gap-3 rounded-card border border-danger/30 bg-panel px-4 py-2.5 text-base text-danger shadow-float"
    >
      <span className="min-w-0">{notice}</span>
      <button
        onClick={() => setNotice(null)}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}

/** Workspace config (enabled kinds, brand) re-read on return to the tab and every 5 minutes. */
function useConfigWatch() {
  const refreshConfig = useStore((s) => s.refreshConfig)
  useEffect(() => {
    let last = 0
    const refresh = () => {
      if (document.visibilityState !== 'visible' || Date.now() - last < 15_000) return
      last = Date.now()
      void refreshConfig()
    }
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    const timer = setInterval(refresh, 5 * 60_000)
    return () => {
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
      clearInterval(timer)
    }
  }, [refreshConfig])
}

/** The tab says which screen this is (and how many replies wait), not only the brand. */
function useTabTitle(pathname: string) {
  const t = useT()
  const brand = useStore((s) => s.brand.name)
  const sessionId = pathname.match(/^\/s\/([^/]+)/)?.[1]
  const projectId = pathname.match(/^\/projects\/([^/]+)/)?.[1]
  const sessionTitle = useStore((s) => (sessionId ? s.sessions.find((row) => row.id === sessionId)?.title : undefined))
  const projectName = useStore((s) => (projectId ? s.projects.find((row) => row.id === projectId)?.name : undefined))
  const unread = useIssues((s) => s.unread)
  useEffect(() => {
    setBrandTitle(brand)
  }, [brand])
  useEffect(() => {
    const label = routeTitle(pathname)
    setPageTitle(sessionTitle ?? projectName ?? (label ? t(label) : ''))
  }, [pathname, sessionTitle, projectName, t])
  useEffect(() => {
    setUnreadTitle(unread)
    return () => setUnreadTitle(0)
  }, [unread])
}

export function AppShell() {
  const t = useT()
  const narrow = useNarrowLayout()
  const { sidebar, cycleSidebar } = useStore()
  const open = sidebar !== 'hidden'
  const location = useLocation()
  useIssueBadge()
  useBotAttentionBadge()
  useConfigWatch()
  useTabTitle(location.pathname)

  // Narrow layout: sidebar overlays, so navigation closes it.
  useEffect(() => {
    if (narrow && open) cycleSidebar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, narrow])

  // Phone drawer is modal: focus in, Escape closes, focus restored; page behind `inert`.
  const drawer = useRef<HTMLDivElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const drawerOpen = narrow && open
  useEffect(() => {
    if (!drawerOpen) return
    const opener = document.activeElement as HTMLElement | null
    drawer.current?.querySelector<HTMLElement>('a[href], button:not([disabled]), input:not([disabled])')?.focus()
    const onKey = (e: KeyboardEvent) => {
      const overlaid = () =>
        [...document.querySelectorAll('[role="menu"], [aria-modal="true"]')].some((el) => el !== drawer.current)
      if (e.key === 'Tab' && !e.defaultPrevented && drawer.current) {
        // A menu or dialog opened from the drawer keeps its own Tab order.
        if (overlaid()) return
        // Tab wraps inside the drawer: nothing behind it (the skip link included) takes focus.
        const stops = [...drawer.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )].filter((el) => el.tabIndex >= 0 && el.getClientRects().length > 0 && !el.closest('[inert]'))
        const [first, last] = [stops[0], stops.at(-1)]
        const here = document.activeElement
        const outside = !drawer.current.contains(here)
        if (first && last && (outside || here === (e.shiftKey ? first : last))) {
          e.preventDefault()
          ;(e.shiftKey ? last : first).focus()
        }
        return
      }
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // A menu or dialog from the drawer owns this Escape.
      if (overlaid()) return
      e.preventDefault()
      cycleSidebar()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [drawerOpen, cycleSidebar])

  return (
    <div className="relative flex h-full overflow-hidden bg-bg text-fg">
      {/* Skip link past the sidebar. */}
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault()
          mainRef.current?.focus()
        }}
        // While the drawer is a modal, the page it skips to is inert: out of the Tab order.
        tabIndex={drawerOpen ? -1 : undefined}
        aria-hidden={drawerOpen || undefined}
        // Full size and above the screen until focused, not a 1px box: still a 24px target.
        className="fixed top-2 left-2 z-[200] -translate-y-[calc(100%+1rem)] rounded-control bg-accent px-3 py-2.5 text-base font-medium text-accent-fg focus:translate-y-0"
      >
        {t('본문으로 건너뛰기')}
      </a>
      {/* Drawer stays mounted and slides; reduced-motion guard in index.css. */}
      {narrow && (
        <button
          aria-label={t('사이드바 닫기')}
          // Out of Tab order (Escape closes); screen-reader swipe still reaches it while open.
          tabIndex={-1}
          aria-hidden={!open}
          className={cn(
            'absolute inset-0 z-30 bg-black/40 transition-opacity duration-200',
            open ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          onClick={cycleSidebar}
        />
      )}
      <div
        ref={drawer}
        role={drawerOpen ? 'dialog' : undefined}
        aria-modal={drawerOpen || undefined}
        aria-label={drawerOpen ? t('탐색') : undefined}
        className={
          narrow
            ? cn(
                'absolute inset-y-0 left-0 z-40 shadow-float transition-transform duration-300',
                open ? 'translate-x-0' : '-translate-x-full',
              )
            : cn(
                'shrink-0 transition-[width] duration-300',
                // `overflow-hidden` only when hidden; would clip the rail's menus.
                sidebar === 'hidden'
                  ? 'w-0 overflow-hidden'
                  : sidebar === 'rail'
                    ? 'w-16'
                    : 'w-[268px]',
              )
        }
        // Collapsed panel out of tab order.
        inert={!open ? true : undefined}
      >
        <Sidebar />
      </div>
      <main
        id="main"
        ref={mainRef}
        tabIndex={-1}
        className="flex min-w-0 flex-1 flex-col outline-none"
        inert={drawerOpen ? true : undefined}
      >
        <Outlet />
      </main>
      <UndoBar />
      <OfflineBar />
      <NoticeBar />
      <ToastBar />
      <KeyboardShortcuts />
      <SearchDialog />
      <IssueReportDialog />
    </div>
  )
}

/** Standard scroll container for the non-chat management pages. */
export function PageBody({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className={cn('mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 sm:py-8', className)}>
        {children}
      </div>
    </div>
  )
}

/** Management page top bar and scroll body; neither inside the 사용자 지정 hub. */
export function PageFrame({
  title,
  embedded = false,
  children,
}: {
  title: string
  embedded?: boolean
  children: React.ReactNode
}) {
  if (embedded) return <>{children}</>
  return (
    <>
      <TopBar left={<span className="text-base font-medium">{title}</span>} />
      <PageBody>{children}</PageBody>
    </>
  )
}
