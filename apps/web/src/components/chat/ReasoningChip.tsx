import { Brain } from 'lucide-react'
import { Dropdown, MenuItem } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { REASONING_HINT, REASONING_LABEL, REASONING_MODES } from '@/lib/reasoning'
import type { ReasoningMode } from '@/types'

const CHIP =
  'flex h-8 shrink-0 items-center gap-1.5 rounded-control border border-line px-2.5 text-sm transition-colors'

/**
 * The composer's reasoning chip (자동 / 빠르게 / 깊게), in the style of the composer's other
 * chips. A model whose reasoning cannot be steered shows it switched off with the reason in its
 * tooltip, rather than offering a choice that would change nothing.
 */
export function ReasoningChip({
  value,
  onChange,
  lane,
  steerable,
  disabled,
}: {
  value: ReasoningMode
  onChange: (mode: ReasoningMode) => void
  /** Super Agent picks the model too; a hand-picked model only changes its effort. */
  lane: 'super' | 'manual'
  /** False: the picked model has no reasoning control. */
  steerable: boolean
  /** Editing a past question: the turn's options are not changed from here. */
  disabled?: boolean
}) {
  const t = useT()
  if (!steerable) {
    return (
      <button
        type="button"
        aria-disabled="true"
        data-testid="reasoning-chip"
        data-reasoning="unsupported"
        title={t('이 모델은 추론 강도를 바꿀 수 없어 모델에 설정된 대로 답합니다. 추론을 고르려면 Super Agent나 추론을 조절할 수 있는 모델을 고르세요.')}
        aria-label={t('추론: 이 모델은 조절할 수 없음')}
        className={cn(CHIP, 'cursor-not-allowed text-faint opacity-60')}
      >
        <Brain size={13} aria-hidden />
        <span className="phone:hidden">{t('추론')}</span>
        <span className="font-medium">{t('모델 기본')}</span>
      </button>
    )
  }
  return (
    <Dropdown
      align="right"
      label={t('추론')}
      trigger={({ open }) => (
        <button
          type="button"
          disabled={disabled}
          data-testid="reasoning-chip"
          data-reasoning={value}
          aria-label={`${t('추론')}: ${t(REASONING_LABEL[value])}`}
          title={t(REASONING_HINT[lane][value])}
          className={cn(
            CHIP,
            open ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
            value !== 'auto' && 'border-accent/50 text-fg',
            'disabled:cursor-not-allowed disabled:opacity-60',
          )}
        >
          <Brain size={13} aria-hidden className={value === 'deep' ? 'text-accent' : undefined} />
          <span className="text-faint phone:hidden">{t('추론')}</span>
          <span className="font-medium">{t(REASONING_LABEL[value])}</span>
        </button>
      )}
    >
      {REASONING_MODES.map((mode) => (
        <MenuItem
          key={mode}
          checked={mode === value}
          onClick={() => onChange(mode)}
          label={t(REASONING_LABEL[mode])}
        >
          <span className="block text-base text-fg">{t(REASONING_LABEL[mode])}</span>
          <span className="block text-xs text-faint">{t(REASONING_HINT[lane][mode])}</span>
        </MenuItem>
      ))}
    </Dropdown>
  )
}
