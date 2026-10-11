import { Globe2, Link2, Users } from 'lucide-react'
import { Field, Input } from '@/components/ui'
import type { BotVisibility } from '@/lib/api'
import { CREDITS_PER_DAY_MAX, VISIBILITIES, VISIBILITY_HINT, VISIBILITY_LABEL, creditsCap } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

const ICON: Record<BotVisibility, typeof Users> = { internal: Users, link: Link2, external: Globe2 }

/** Bot audience: members at once; link or everyone by admin request, needing a daily credit cap. */
export function ScopePicker({
  value,
  onChange,
  credits,
  onCredits,
  creditsRemaining,
  current,
}: {
  value: BotVisibility
  onChange: (next: BotVisibility) => void
  /** Daily credit cap as typed. */
  credits: string
  onCredits: (next: string) => void
  creditsRemaining?: number
  /** Audience in force on a published bot; a wider choice is a request. */
  current?: BotVisibility
}) {
  const t = useT()
  const outside = value !== 'internal'
  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label={t('공개 범위')} className="grid gap-2 sm:grid-cols-3" data-testid="scope-picker">
        {VISIBILITIES.map((id) => {
          const Icon = ICON[id]
          const chosen = value === id
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={chosen}
              onClick={() => onChange(id)}
              className={cn(
                'flex flex-col items-start gap-1 rounded-card border px-3 py-2.5 text-left transition-colors',
                chosen ? 'border-accent bg-accent-soft' : 'border-line hover:bg-elevated',
              )}
            >
              <span className="flex items-center gap-1.5 text-base font-medium">
                <Icon size={14} aria-hidden className={chosen ? 'text-accent' : 'text-muted'} />
                {t(VISIBILITY_LABEL[id])}
                {current === id && <span className="text-xs font-normal text-faint">· {t('지금')}</span>}
              </span>
              <span className="text-sm text-muted">{t(VISIBILITY_HINT[id])}</span>
            </button>
          )
        })}
      </div>
      {outside && (
        <div className="space-y-1">
          <Field
            label={t('하루 최대 크레딧')}
            hint={
              t('이 챗봇이 하루에 쓸 수 있는 내 크레딧입니다. 크레딧이 들지 않는 모델이면 질문 하나를 1크레딧으로 셉니다. 다 쓰면 그날은 멈추고 다음 날 다시 열립니다.') +
              (creditsRemaining !== undefined ? ` ${t('남은 크레딧: {n}').replace('{n}', creditsRemaining.toLocaleString())}` : '')
            }
          >
            <Input
              type="number"
              min={1}
              max={CREDITS_PER_DAY_MAX}
              step={1}
              inputMode="numeric"
              required
              value={credits}
              onChange={(e) => onCredits(e.target.value)}
              aria-invalid={credits.trim() !== '' && creditsCap(credits) === undefined}
              data-testid="scope-credits"
              className="block max-w-48"
            />
          </Field>
          {credits.trim() !== '' && creditsCap(credits) === undefined && (
            <p role="alert" className="text-sm text-danger" data-testid="scope-credits-error">
              {t('1부터 {max}까지의 정수로 적어 주세요.').replace('{max}', CREDITS_PER_DAY_MAX.toLocaleString())}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
