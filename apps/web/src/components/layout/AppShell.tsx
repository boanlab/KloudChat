import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { useStore } from '@/store/useStore'
import { Sidebar } from './Sidebar'
import { useT } from '@/lib/useT'

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
      {narrow && sidebarOpen && (
        <button
          aria-label={t('사이드바 닫기')}
          className="absolute inset-0 z-30 bg-black/40"
          onClick={toggleSidebar}
        />
      )}
      <div className={narrow ? 'absolute inset-y-0 left-0 z-40 shadow-2xl' : 'contents'}>
        <Sidebar />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </div>
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
