import { TriangleAlert } from 'lucide-react'
import { Badge, Dropdown, MenuLabel } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { LintFinding } from '@/types'
import { useT } from '@/lib/useT'

/**
 * What the linter found, on the artifact that carries it.
 *
 * Deliberately a badge and a list rather than an inline marker on the text:
 * nothing here is corrected automatically, so what the reader needs is a count
 * they can ignore and a list they can act on — not decoration threaded through
 * a document they are still reading.
 *
 * The count shown is the `P0` one when there are any, because "two things are
 * wrong" and "five things could read better" are different sentences and only
 * the first should look urgent.
 */
export function LintFindings({ findings }: { findings?: LintFinding[] }) {
  const t = useT()
  const all = findings ?? []
  if (all.length === 0) return null

  const wrong = all.filter((f) => f.severity === 'P0')
  const tone = wrong.length > 0 ? 'warn' : undefined

  return (
    <Dropdown
      align="right"
      trigger={() => (
        <button type="button" aria-label={t('검사 결과')}>
          <Badge tone={tone}>
            <TriangleAlert size={10} />
            {wrong.length > 0
              ? t('고칠 곳 {n}').replace('{n}', String(wrong.length))
              : t('볼 곳 {n}').replace('{n}', String(all.length))}
          </Badge>
        </button>
      )}
    >
      <MenuLabel>{t('검사 결과')}</MenuLabel>
      <ul className="max-h-72 w-80 overflow-auto px-1 pb-1">
        {all.map((finding, index) => (
          <li
            key={`${finding.rule}-${finding.where}-${index}`}
            className="flex gap-2 rounded-control px-1.5 py-1.5 text-sm"
          >
            <span
              className={cn(
                'mt-0.5 h-fit shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium',
                finding.severity === 'P0'
                  ? 'bg-warn/10 text-warn'
                  : 'bg-elevated text-faint',
              )}
            >
              {finding.severity}
            </span>
            <span className="min-w-0">
              {finding.where && (
                <span className="block text-xs text-faint">{finding.where}</span>
              )}
              {finding.message}
            </span>
          </li>
        ))}
      </ul>
    </Dropdown>
  )
}
