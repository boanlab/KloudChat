import { Bell, X } from 'lucide-react'
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { acceptNotices, declineNotices, NAVIGATE_EVENT, openNoticeUrl, useNotifyAsk, watchLongJobs } from '@/lib/notices'
import { syncThisDevice } from '@/lib/push'
import { useT } from '@/lib/useT'

/** Signed in: long jobs watched for tab notices, notification clicks routed, this device's push
 * subscription re-registered for the account, and the one-time question when a job runs long. */
export function Notices() {
  const t = useT()
  const navigate = useNavigate()
  const asking = useNotifyAsk()

  useEffect(() => watchLongJobs(), [])
  useEffect(() => {
    void syncThisDevice()
  }, [])
  useEffect(() => {
    const onNavigate = (event: Event) => {
      const url = (event as CustomEvent<string>).detail
      if (typeof url === 'string') navigate(url)
    }
    // the service worker's notificationclick posts the address to the focused window
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; url?: unknown } | null
      if (data?.type === NAVIGATE_EVENT && typeof data.url === 'string') openNoticeUrl(data.url)
    }
    window.addEventListener(NAVIGATE_EVENT, onNavigate)
    navigator.serviceWorker?.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener(NAVIGATE_EVENT, onNavigate)
      navigator.serviceWorker?.removeEventListener('message', onMessage)
    }
  }, [navigate])

  if (!asking) return null
  return (
    <div
      role="dialog"
      aria-label={t('알림')}
      data-testid="notify-ask"
      className="animate-fade-up fixed inset-x-0 top-3 z-50 mx-auto flex w-fit max-w-[calc(100vw-2rem)] items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <Bell size={16} className="shrink-0 text-accent" />
      <span className="min-w-0">{t('작업이 끝나면 알림으로 알려 드릴까요?')}</span>
      <button onClick={() => void acceptNotices()} className="shrink-0 font-medium text-accent hover:underline">
        {t('알림 켜기')}
      </button>
      <button
        onClick={declineNotices}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}
