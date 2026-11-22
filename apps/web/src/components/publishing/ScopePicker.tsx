import { Globe2, Link2, Users } from 'lucide-react'
import { Field, Input } from '@/components/ui'
import type { BotVisibility } from '@/lib/api'
import { CREDITS_PER_DAY_MAX, VISIBILITIES, VISIBILITY_HINT, VISIBILITY_LABEL, creditsCap } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

export const SCOPE_ICON: Record<BotVisibility, typeof Users> = { internal: Users, link: Link2, external: Globe2 }

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
      <div role="radiogroup" aria-label={t('공유 범위')} className="grid gap-2 sm:grid-cols-3" data-testid="scope-picker">
        {VISIBILITIES.map((id) => {
          const Icon = SCOPE_ICON[id]
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
      {outside && <CreditsField credits={credits} onCredits={onCredits} creditsRemaining={creditsRemaining} />}
    </div>
  )
}

/** The daily credit cap a scope outside the organization needs, with its reason beside it. */
export function CreditsField({
  credits,
  onCredits,
  creditsRemaining,
}: {
  /** Daily credit cap as typed. */
  credits: string
  onCredits: (next: string) => void
  creditsRemaining?: number
}) {
  const t = useT()
  return (
    <div className="space-y-1">
      <Field
        label={t('하루 최대 크레딧')}
        hint={
          t('방문자가 쓰는 크레딧은 내 크레딧에서 빠집니다. 다 쓰면 다음 날까지 멈춥니다. 나중에 바꿀 수 있습니다.') +
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
          aria-invalid={creditsCap(credits) === undefined}
          data-testid="scope-credits"
          className="block max-w-48"
        />
      </Field>
      {creditsCap(credits) === undefined && (
        <p role="alert" className="text-sm text-danger" data-testid="scope-credits-error">
          {t('1부터 {max}까지의 정수로 적어 주세요.').replace('{max}', CREDITS_PER_DAY_MAX.toLocaleString())}
        </p>
      )}
    </div>
  )
}
