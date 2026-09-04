import { X } from 'lucide-react'
import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { KeyboardShortcuts } from './KeyboardShortcuts'
import { Sidebar } from './Sidebar'
import { useT } from '@/lib/useT'

/** Undo toast for a pending delete; lives in the shell so it survives navigation. */
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

/** Transient error toast for failures with no screen of their own. */
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

export function AppShell() {
  const t = useT()
  const narrow = useNarrowLayout()
  const { sidebar, cycleSidebar } = useStore()
  const open = sidebar !== 'hidden'
  const location = useLocation()

  // On narrow layouts the sidebar overlays the content, so navigation closes it.
  useEffect(() => {
    if (narrow && open) cycleSidebar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, narrow])

  return (
    <div className="relative flex h-full overflow-hidden bg-bg text-fg">
      {/* The drawer stays mounted and slides; transitions collapse under the reduced-motion guard in index.css. */}
      {narrow && (
        <button
          aria-label={t('사이드바 닫기')}
          tabIndex={open ? 0 : -1}
          aria-hidden={!open}
          className={cn(
            'absolute inset-0 z-30 bg-black/40 transition-opacity duration-200',
            open ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          onClick={cycleSidebar}
        />
      )}
      <div
        className={
          narrow
            ? cn(
                'absolute inset-y-0 left-0 z-40 shadow-float transition-transform duration-300',
                open ? 'translate-x-0' : '-translate-x-full',
              )
            : cn(
                'shrink-0 transition-[width] duration-300',
                // `overflow-hidden` only when hidden; it would clip the rail's menus.
                sidebar === 'hidden'
                  ? 'w-0 overflow-hidden'
                  : sidebar === 'rail'
                    ? 'w-16'
                    : 'w-[268px]',
              )
        }
        // Keeps the collapsed panel out of the tab order.
        inert={!open ? true : undefined}
      >
        <Sidebar />
      </div>
      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
      <UndoBar />
      <NoticeBar />
      <KeyboardShortcuts />
    </div>
  )
}

/** Standard scroll container for the non-chat management pages. */
export function PageBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 sm:py-8">{children}</div>
    </div>
  )
}
