import { RefreshCw, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useT } from '@/lib/useT'
import { watchForNewVersion } from '@/lib/version'

/** A deploy happened while this page was open: offers a reload, never forces one (a draft may be open). */
export function NewVersionBar() {
  const t = useT()
  const [newer, setNewer] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  useEffect(() => watchForNewVersion(() => setNewer(true)), [])
  if (!newer || dismissed) return null
  return (
    <div
      role="status"
      data-testid="new-version"
      className="animate-fade-up fixed left-1/2 top-3 z-50 flex max-w-[92vw] -translate-x-1/2 items-center gap-3 rounded-card border border-line bg-panel px-4 py-2.5 text-base shadow-float"
    >
      <span className="min-w-0">{t('새 버전이 있습니다')}</span>
      <button
        onClick={() => window.location.reload()}
        className="inline-flex shrink-0 items-center gap-1 font-medium text-accent hover:underline"
      >
        <RefreshCw size={14} />
        {t('새로고침')}
      </button>
      <button
        onClick={() => setDismissed(true)}
        aria-label={t('닫기')}
        className="shrink-0 rounded-control p-0.5 text-faint hover:bg-elevated hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  )
}
