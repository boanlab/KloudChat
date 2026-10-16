import { useEffect, useState } from 'react'
import { filesApi } from '@/lib/api'
import { useT } from '@/lib/useT'

/** How many files a conversation holds (attached or made in it), read when the dialog opens. */
export function useSessionFileCount(open: boolean, sessionId: string | null | undefined) {
  const [count, setCount] = useState<number | null>(null)
  useEffect(() => {
    if (!open || !sessionId) {
      setCount(null)
      return
    }
    let live = true
    filesApi
      .list({ sessionId })
      // A project's file stays with the project whatever is chosen here.
      .then((rows) => live && setCount(rows.filter((row) => !row.projectId && !row.agentId).length))
      .catch(() => live && setCount(null))
    return () => {
      live = false
    }
  }, [open, sessionId])
  return count
}

/** 「파일도 함께 지우기」 in a conversation's delete dialog; off by default (they stay in the file box). */
export function DeleteFilesChoice({
  checked,
  onChange,
  count,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  /** Known count; null when unknown (many conversations), 0 hides the choice. */
  count: number | null
}) {
  const t = useT()
  if (count === 0) return null
  return (
    <label className="flex cursor-pointer items-start gap-2.5 rounded-card border border-line px-3 py-2.5 text-base">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 size-4 shrink-0 cursor-pointer accent-[var(--color-accent)]"
      />
      <span className="min-w-0">
        <span className="block font-medium">
          {count === null
            ? t('대화의 파일도 함께 지우기')
            : t('대화의 파일 {n}개도 함께 지우기').replace('{n}', String(count))}
        </span>
        <span className="block text-muted">
          {t('올린 파일과 대화에서 만든 파일입니다. 지우지 않으면 파일함에 남습니다. 프로젝트 파일은 지우지 않습니다.')}
        </span>
      </span>
    </label>
  )
}
