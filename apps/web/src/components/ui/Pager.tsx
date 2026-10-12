import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui'
import { pageCount, pageWindow } from '@/lib/paging'
import { useT } from '@/lib/useT'

/** 「first–last / 전체 total건」 and 이전 · numbers · 다음; `children` after the range. */
export function Pager({
  page,
  size,
  total,
  onPage,
  label,
  rangeTestId,
  className = 'mt-3',
  children,
}: {
  page: number
  size: number
  total: number
  onPage: (page: number) => void
  /** Nav landmark name, e.g. 「감사 로그 페이지」. */
  label: string
  rangeTestId?: string
  className?: string
  children?: ReactNode
}) {
  const t = useT()
  const pages = pageCount(total, size)
  const first = total ? (page - 1) * size + 1 : 0
  const last = Math.min(total, page * size)
  return (
    <nav
      aria-label={label}
      className={`${className} flex flex-wrap items-center justify-between gap-x-4 gap-y-2`}
    >
      <div className="flex items-center gap-2 text-sm text-faint">
        <span className="tabular-nums" data-testid={rangeTestId}>
          {t('{first}–{last} / 전체 {total}건')
            .replace('{first}', first.toLocaleString())
            .replace('{last}', last.toLocaleString())
            .replace('{total}', total.toLocaleString())}
        </span>
        {children}
      </div>
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          variant="ghost"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label={t('이전 페이지')}
        >
          <ChevronLeft size={14} />
          <span className="hidden sm:inline">{t('이전')}</span>
        </Button>
        {pageWindow(page, pages).map((p, i) =>
          p === null ? (
            <span key={`gap-${i}`} className="px-1 text-faint">
              …
            </span>
          ) : (
            <Button
              key={p}
              size="sm"
              variant={p === page ? 'primary' : 'ghost'}
              aria-current={p === page ? 'page' : undefined}
              aria-label={t('{n}쪽').replace('{n}', String(p))}
              onClick={() => onPage(p)}
              className="min-w-8 tabular-nums"
            >
              {p}
            </Button>
          ),
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label={t('다음 페이지')}
        >
          <span className="hidden sm:inline">{t('다음')}</span>
          <ChevronRight size={14} />
        </Button>
      </div>
    </nav>
  )
}
