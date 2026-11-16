import { Pager } from '@/components/ui/Pager'
import type { usePaging } from '@/lib/usePaging'

/** The pager under a long list; nothing when the list fits one page. */
export function ListPager({
  paging,
  total,
  label,
  className,
}: {
  paging: ReturnType<typeof usePaging>
  total: number
  /** Nav landmark name, e.g. 「작업 페이지」. */
  label: string
  className?: string
}) {
  if (!paging.paged) return null
  return <Pager page={paging.page} size={paging.size} total={total} onPage={paging.setPage} label={label} className={className} />
}
