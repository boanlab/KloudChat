import { Check, Cpu, Loader2 } from 'lucide-react'
import { Badge, Button } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Variant } from '@/types'
import { Markdown } from './Markdown'
import { useT } from '@/lib/useT'

/**
 * Puts the chosen models' answers side by side.
 *
 * On a shared proxy the real question is "is the expensive model worth it for
 * this prompt", so each column carries its own cost and choosing one is an
 * explicit action. The conversation continues from the answer picked.
 */
export function CompareView({
  variants,
  sessionId,
  messageId,
}: {
  variants: Variant[]
  sessionId: string
  messageId: string
}) {
  const t = useT()
  const { models, chooseVariant } = useStore()
  const decided = variants.some((v) => v.chosen)
  const cheapest = Math.min(...variants.map((v) => v.usage?.credits ?? Infinity))

  return (
    <div
      className={cn(
        'grid gap-3',
        variants.length >= 3 ? 'md:grid-cols-3' : 'md:grid-cols-2',
      )}
    >
      {variants.map((v) => {
        const info = models.find((m) => m.id === v.model)
        const dimmed = decided && !v.chosen
        return (
          <div
            key={v.model}
            className={cn(
              'flex min-w-0 flex-col rounded-card border transition-colors',
              v.chosen ? 'border-accent bg-accent-soft/30' : 'border-line bg-panel',
              dimmed && 'opacity-55',
            )}
          >
            <header className="flex items-center gap-1.5 border-b border-line px-3 py-2">
              <Cpu size={13} className="shrink-0 text-faint" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {info?.label ?? v.model}
              </span>
              {v.dataBoundary && (
                <Badge tone={v.dataBoundary === 'self_hosted' ? 'success' : 'warn'}>
                  {v.dataBoundary === 'self_hosted'
                    ? 'self-hosted'
                    : v.dataBoundary === 'hybrid'
                      ? t('외부 전환 가능')
                      : v.dataBoundary === 'external'
                        ? t('외부 제공')
                        : t('경계 미확인')}
                </Badge>
              )}
              {v.actualModel && v.actualModel !== (v.routedModel ?? v.model) && (
                <Badge className="max-w-32 truncate" title={v.actualModel}>
                  {t('실행')}: {v.actualModel}
                </Badge>
              )}
              {v.status === 'streaming' && (
                <Loader2 size={12} className="shrink-0 animate-spin text-accent" />
              )}
              {v.chosen && (
                <Badge tone="accent">
                  <Check size={10} />
                  {t('선택됨')}
                </Badge>
              )}
            </header>

            <div className="min-w-0 flex-1 px-3 py-2.5">
              {v.content ? (
                <Markdown className="text-base">{v.content}</Markdown>
              ) : (
                <p className="animate-blink text-base text-faint">{t('응답 대기 중…')}</p>
              )}
            </div>

            <footer className="flex items-center gap-2 border-t border-line px-3 py-2">
              {v.usage ? (
                <span className="text-xs text-faint">
                  {t('{n} 크레딧').replace('{n}', String(v.usage.credits))}
                  {v.usage.credits === cheapest && variants.length > 1 && (
                    <span className="ml-1 text-success">· {t('최저')}</span>
                  )}
                </span>
              ) : (
                <span className="text-xs text-faint">{t('집계 중')}</span>
              )}
              <Button
                size="sm"
                variant={v.chosen ? 'primary' : 'secondary'}
                className="ml-auto"
                disabled={v.status !== 'done'}
                onClick={() => chooseVariant(sessionId, messageId, v.model)}
              >
                {v.chosen ? t('이어가는 중') : t('이 답변으로 계속')}
              </Button>
            </footer>
          </div>
        )
      })}
    </div>
  )
}
