import { Button } from '@/components/ui'
import type { SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

/** The last settings fetch: under way, failed (our API), and when one last succeeded. */
export interface SettingsFetch {
  loading: boolean
  error: string | null
  checkedAt: string | null
}

/** A failed settings fetch, with what is on screen and a retry. */
export function SettingsFetchNotice({
  status,
  settings,
  onRetry,
}: {
  status: SettingsFetch
  settings: SystemSettings | null
  onRetry: () => void
}) {
  const t = useT()
  return (
    <div
      role="alert"
      data-testid="settings-fetch-failed"
      className="mb-4 flex flex-wrap items-center gap-2 rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger"
    >
      <span className="min-w-0 flex-1 max-sm:basis-40">
        {settings
          ? t('설정을 새로 불러오지 못했습니다. 화면에 보이는 것은 마지막으로 받은 내용입니다.')
          : t('설정을 불러오지 못해 이 화면을 채울 수 없습니다.')}{' '}
        <span className="text-muted">{status.error}</span>
      </span>
      <Button size="sm" disabled={status.loading} onClick={onRetry}>
        {status.loading ? t('불러오는 중…') : t('다시 시도')}
      </Button>
    </div>
  )
}
