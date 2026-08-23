import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { Sidebar } from './Sidebar'
import { useT } from '@/lib/useT'

/**
 * The few seconds after a delete, in which it has not happened.
 *
 * Sits in the shell rather than on each screen: the delete that needs undoing
 * most is the one that navigated away from the thing it deleted.
 */
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

export function AppShell() {
  const t = useT()
  const narrow = useNarrowLayout()
  const { sidebarOpen, toggleSidebar } = useStore()
  const location = useLocation()

  // On narrow layouts the sidebar covers the content, so navigating has to
  // dismiss it — otherwise the user taps a link and sees the same panel.
  useEffect(() => {
    if (narrow && sidebarOpen) toggleSidebar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, narrow])

  return (
    <div className="relative flex h-full overflow-hidden bg-bg text-fg">
      {/* The panel stays mounted and moves, rather than appearing and vanishing:
          a drawer that is simply gone on the next frame reads as a glitch. Both
          transitions collapse under the reduced-motion guard in index.css. */}
      {narrow && (
        <button
          aria-label={t('사이드바 닫기')}
          tabIndex={sidebarOpen ? 0 : -1}
          aria-hidden={!sidebarOpen}
          className={cn(
            'absolute inset-0 z-30 bg-black/40 transition-opacity duration-200',
            sidebarOpen ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          onClick={toggleSidebar}
        />
      )}
      <div
        className={
          narrow
            ? cn(
                'absolute inset-y-0 left-0 z-40 shadow-float transition-transform duration-300',
                sidebarOpen ? 'translate-x-0' : '-translate-x-full',
              )
            : cn(
                'shrink-0 overflow-hidden transition-[width] duration-300',
                sidebarOpen ? 'w-[268px]' : 'w-0',
              )
        }
        // Off-screen is out of reach: without this the collapsed panel keeps
        // every one of its rows in the tab order.
        inert={!sidebarOpen ? true : undefined}
      >
        <Sidebar />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </div>
      <UndoBar />
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
