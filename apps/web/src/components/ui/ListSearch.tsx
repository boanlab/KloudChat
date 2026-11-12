import { Search } from 'lucide-react'
import { Input } from '@/components/ui'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** A list's own filter box, over what the page already loaded. */
export function ListSearch({
  value,
  onChange,
  label,
  className,
}: {
  value: string
  onChange: (value: string) => void
  /** What is searched, e.g. 「프로젝트 검색」. */
  label: string
  className?: string
}) {
  const t = useT()
  return (
    <div className={cn('relative w-72 min-w-0 max-sm:w-full', className)}>
      <Search
        size={14}
        className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint"
      />
      <Input
        type="search"
        aria-label={label}
        placeholder={t('검색')}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pl-9"
      />
    </div>
  )
}

/** Shown when the box filters everything out. */
export function NoMatch() {
  const t = useT()
  return (
    <p className="py-8 text-center text-base text-faint" role="status" data-testid="no-match">
      {t('검색 결과가 없습니다')}
    </p>
  )
}
