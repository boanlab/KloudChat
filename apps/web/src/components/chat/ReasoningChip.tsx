import { Brain, ChevronDown } from 'lucide-react'
import { Dropdown, MenuItem } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { REASONING_HINT, REASONING_LABEL, REASONING_MODES } from '@/lib/reasoning'
import type { ReasoningMode } from '@/types'

// The model picker's look: borderless text with an icon and a chevron, a pale ground on hover.
const CHIP =
  'flex h-9 shrink-0 items-center gap-1.5 rounded-control px-2.5 text-base font-medium transition-colors'

/** Composer chip for reasoning effort (자동 / 빠르게 / 깊게); disabled with a tooltip reason when the model cannot be steered. */
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
    // the reason shows on hover, keyboard focus and a tap, and the button names it to a screen reader
    return (
      <span className="group relative inline-flex shrink-0">
        <button
          type="button"
          aria-disabled="true"
          aria-describedby="reasoning-chip-tip"
          data-testid="reasoning-chip"
          data-reasoning="unsupported"
          aria-label={t('추론: 이 모델은 조절할 수 없음')}
          className={cn(CHIP, 'cursor-not-allowed text-faint opacity-60')}
        >
          <Brain size={14} aria-hidden />
          <span className="phone:hidden">{t('추론')}</span>
          <span>{t('모델 기본')}</span>
        </button>
        <span
          id="reasoning-chip-tip"
          role="tooltip"
          data-testid="reasoning-chip-tip"
          className="pointer-events-none absolute bottom-full right-0 z-30 mb-1 hidden w-max max-w-[16rem] rounded-md border border-line bg-elevated px-2.5 py-1.5 text-xs font-normal text-fg shadow-lg group-focus-within:block group-hover:block"
        >
          {t('이 모델은 추론 강도를 바꿀 수 없습니다.')}
        </span>
      </span>
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
            value !== 'auto' && !open && 'text-fg',
            'disabled:cursor-not-allowed disabled:opacity-60',
          )}
        >
          <Brain size={14} aria-hidden className={value === 'deep' ? 'text-accent' : undefined} />
          <span className="text-faint phone:hidden">{t('추론')}</span>
          <span>{t(REASONING_LABEL[value])}</span>
          <ChevronDown size={14} aria-hidden className="shrink-0 text-faint" />
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
