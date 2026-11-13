import { useEffect, useState } from 'react'
import { currentLang, translate } from '@/lib/i18n'

const PAGE_SIZE = 40

/** Caps how many `items` are rendered; `deps` changing resets the page. */
export function usePaged<T>(items: T[], deps: unknown[] = []) {
  const [shown, setShown] = useState(PAGE_SIZE)

  useEffect(() => {
    setShown(PAGE_SIZE)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return {
    visible: items.slice(0, shown),
    hidden: Math.max(0, items.length - shown),
    more: () => setShown((n) => n + PAGE_SIZE),
  }
}

export function ShowMore({ hidden, onMore }: { hidden: number; onMore: () => void }) {
  if (hidden <= 0) return null
  return (
    <button
      onClick={onMore}
      className="mt-2 w-full rounded-control border border-line py-2 text-base text-muted transition-colors hover:bg-elevated hover:text-fg"
    >
      {translate(currentLang(), '{n}개 더 보기').replace('{n}', hidden.toLocaleString())}
    </button>
  )
}

/** The next server page: 「{n}개 더 보기」, its failure said above it and the button retrying. */
export function LoadMore({
  remaining,
  loading,
  failed,
  failedText,
  onMore,
}: {
  remaining: number
  loading: boolean
  failed: boolean
  /** What did not arrive, e.g. 「다음 파일을 불러오지 못했습니다.」 */
  failedText: string
  onMore: () => void
}) {
  const t = (text: string) => translate(currentLang(), text)
  return (
    <div className="mt-2 flex flex-col items-center gap-2">
      {failed && (
        <p role="alert" className="text-base text-danger">
          {failedText}
        </p>
      )}
      <button
        type="button"
        disabled={loading}
        onClick={onMore}
        className="min-h-9 w-full rounded-control border border-line py-2 text-base text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-45"
      >
        {loading
          ? t('불러오는 중…')
          : failed
            ? t('다시 시도')
            : t('{n}개 더 보기').replace('{n}', Math.max(0, remaining).toLocaleString())}
      </button>
    </div>
  )
}
