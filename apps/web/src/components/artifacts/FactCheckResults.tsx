import {
  BadgeCheck,
  CircleHelp,
  ExternalLink,
  ShieldQuestion,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { Badge } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { FactCheck } from '@/types'
import { useT } from '@/lib/useT'

export const verdictMeta = {
  supported: { icon: BadgeCheck, tone: 'success' as const, label: '근거 있음', color: 'text-success' },
  unsupported: {
    icon: TriangleAlert,
    tone: 'danger' as const,
    label: '근거 없음',
    color: 'text-danger',
  },
  uncertain: { icon: CircleHelp, tone: 'warn' as const, label: '확인 필요', color: 'text-warn' },
}

/**
 * Per-claim verdicts with the source behind each one.
 *
 * Shared by the deck and the report because it is one call behind both — the
 * server checks a title and a body, and a claim does not care what shape it was
 * printed in. Lifted out of `DeckPanel` when the report got the same check: two
 * copies of this would be two answers to "what does 확인 필요 look like", and
 * the whole value of a verdict is that it reads the same everywhere.
 *
 * The server refuses to issue a confident verdict without a source; this is
 * where that source is shown, and why `근거 열기` is not optional decoration.
 */
export function FactCheckResults({
  check,
  onFix,
}: {
  check: FactCheck
  /**
   * Hands one weak claim back as an instruction to fix it.
   *
   * A verdict that only says 확인 필요 puts the work back on the reader: they
   * have to find the sentence, decide what it should say, and type it. The
   * check already knows which claim and why — passing that to the revision
   * path is the difference between a report that flags problems and one that
   * fixes them.
   *
   * Absent on surfaces with no revision path of their own, where the verdicts
   * are still worth reading.
   */
  onFix?: (claim: FactCheck['claims'][number]) => void
}) {
  const t = useT()
  if (check.claims.length === 0) {
    return (
      <p className="mt-3 rounded-card border border-line bg-panel p-3 text-sm text-muted">
        {t('검색으로 확인할 수 있는 주장이 여기에는 없습니다. 의견과 정의는 판정하지 않습니다.')}
      </p>
    )
  }
  const weak = check.claims.filter((c) => c.verdict !== 'supported').length
  return (
    <div className="mt-3 rounded-card border border-line bg-panel p-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldQuestion size={13} className="shrink-0 text-accent" />
        <span className="text-xs font-semibold tracking-wide text-faint uppercase">
          {t('팩트체크')}
        </span>
        <Badge tone={weak > 0 ? 'warn' : 'success'}>
          {weak > 0 ? t('확인 필요 {n}').replace('{n}', String(weak)) : t('전부 근거 있음')}
        </Badge>
      </div>
      <div className="space-y-2.5">
        {check.claims.map((c) => {
          const meta = verdictMeta[c.verdict]
          const Icon = meta.icon
          return (
            <div key={c.id} className="flex items-start gap-2 text-sm">
              <Icon size={13} className={cn('mt-0.5 shrink-0', meta.color)} />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{c.text}</p>
                <p className="mt-0.5 text-muted">{c.note}</p>
                <div className="mt-1 flex flex-wrap items-center gap-3">
                  {c.sourceUrl && (
                    <a
                      href={c.sourceUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      <ExternalLink size={10} />
                      {t('근거 열기')}
                    </a>
                  )}
                  {/* 근거가 있는 주장은 고칠 것이 없다. 버튼을 늘 두면 무엇이
                      문제인지가 아니라 버튼이 눈에 남는다. */}
                  {onFix && c.verdict !== 'supported' && (
                    <button
                      onClick={() => onFix(c)}
                      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      <Wrench size={10} />
                      {t('이 대목 고치기')}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
