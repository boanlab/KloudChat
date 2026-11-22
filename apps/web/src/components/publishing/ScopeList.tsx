import { CheckCircle2, Clock, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Card, ConfirmDialog } from '@/components/ui'
import type { BotVisibility, Publication } from '@/lib/api'
import { creditsCap, DEFAULT_CREDITS_PER_DAY, scopeState, VISIBILITIES, VISIBILITY_HINT, VISIBILITY_LABEL } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { CreditsField, SCOPE_ICON } from './ScopePicker'

/**
 * The three scopes as three rows, each in its own state: open now ✓, asked of an administrator
 * (with 요청 취소), or not asked (요청하기). A narrower scope is taken at once; a wider one is a request.
 */
export function ScopeList({
  pub,
  busy,
  onRequest,
  onNarrow,
  onCancel,
}: {
  pub: Publication
  busy: boolean
  onRequest: (visibility: BotVisibility, credits: number) => Promise<void>
  onNarrow: (visibility: BotVisibility) => Promise<void>
  onCancel: () => Promise<void>
}) {
  const t = useT()
  const [asking, setAsking] = useState<BotVisibility | null>(null)
  const [narrowing, setNarrowing] = useState<BotVisibility | null>(null)
  const [credits, setCredits] = useState(String(pub.limits.creditsPerDay ?? DEFAULT_CREDITS_PER_DAY))
  const cap = creditsCap(credits)
  const rank = (v: BotVisibility) => VISIBILITIES.indexOf(v)
  const rejected = pub.reviewState === 'rejected' ? pub.requestedVisibility : ''
  return (
    <Card className="divide-y divide-line" data-testid="publication-scope">
      <p className="px-4 py-3 text-base font-medium">{t('누구와 공유하고 있나요?')}</p>
      {VISIBILITIES.map((id) => {
        const Icon = SCOPE_ICON[id]
        const state = scopeState(pub, id)
        const current = id === pub.visibility
        const asked = state === 'pending' && id === pub.requestedVisibility
        const narrower = rank(id) < rank(pub.visibility)
        return (
          <div key={id} className="space-y-2 px-4 py-3" data-testid={`scope-row-${id}`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              {state === 'open' ? (
                <CheckCircle2 size={16} className="shrink-0 text-success" aria-hidden />
              ) : state === 'pending' ? (
                <Clock size={16} className="shrink-0 text-warn" aria-hidden />
              ) : (
                <Icon size={16} className="shrink-0 text-muted" aria-hidden />
              )}
              <div className="min-w-0 flex-1 max-sm:basis-40">
                <p className="text-base font-medium">{t(VISIBILITY_LABEL[id])}</p>
                <p className="text-sm text-muted">
                  {state === 'pending'
                    ? asked
                      ? t.n('관리자가 확인하고 있습니다. 승인되면 알려 드립니다. 하루 {n}크레딧까지 씁니다.', pub.limits.creditsPerDay ?? 0) +
                        (pub.pendingVersionId ? ` ${t('새 판도 함께 확인합니다.')}` : '')
                      : t('더 넓은 범위의 요청에 포함되어 있습니다.')
                    : t(VISIBILITY_HINT[id])}
                </p>
              </div>
              {current && <Badge tone="success">{t('열림')}</Badge>}
              {state === 'pending' && <Badge tone="warn">{t('승인 대기')}</Badge>}
              {asked && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void onCancel()} data-testid="scope-cancel">
                  {t('요청 취소')}
                </Button>
              )}
              {state === 'open' && narrower && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setNarrowing(id)}>
                  {t('여기까지만 열기')}
                </Button>
              )}
              {state === 'none' && (
                <Button size="sm" disabled={busy || asking === id} onClick={() => setAsking(id)}>
                  {t('요청하기')}
                </Button>
              )}
            </div>
            {rejected === id && (
              <div
                role="status"
                data-testid="publication-review"
                className="flex flex-wrap items-center gap-2 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger"
              >
                <XCircle size={14} aria-hidden />
                <span className="min-w-0 flex-1 max-sm:basis-40">
                  {t('요청이 반려되었습니다.')}
                  {pub.reviewNote && ` ${t('사유: {reason}').replace('{reason}', pub.reviewNote)}`}
                </span>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void onCancel()}>
                  {t('닫기')}
                </Button>
              </div>
            )}
            {asking === id && state === 'none' && (
              <div className="space-y-3 rounded-control bg-elevated/60 p-3" data-testid="scope-request">
                <CreditsField credits={credits} onCredits={setCredits} />
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <Button size="sm" onClick={() => setAsking(null)}>
                    {t('취소')}
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={busy || cap === undefined}
                    onClick={() => cap !== undefined && void onRequest(id, cap).then(() => setAsking(null))}
                  >
                    {t('승인 요청하기')}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      <ConfirmDialog
        open={narrowing !== null}
        onClose={() => setNarrowing(null)}
        onConfirm={() => {
          const target = narrowing
          setNarrowing(null)
          if (target) void onNarrow(target)
        }}
        title={t('공유 범위를 줄입니다')}
        confirmLabel={t('줄이기')}
        description={t('바로 적용되고, 줄어든 범위에서 쓸 수 없는 연동은 함께 닫힙니다. 다시 넓히려면 승인 요청이 필요할 수 있습니다.')}
      />
    </Card>
  )
}
