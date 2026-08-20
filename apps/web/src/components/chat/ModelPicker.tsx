import {
  Check,
  ChevronDown,
  Cpu,
  Eye,
  Gauge,
  Plug,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { useState } from 'react'
import { Badge, Dropdown, useMenuClose } from '@/components/ui'
import { cn, formatTokens } from '@/lib/utils'
import { effectiveModelId, useStore } from '@/store/useStore'
import type { ModelInfo, SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Both directions are billed, so both are quoted. Showing only the output rate
 * reads as the whole price and is wrong by a wide margin on long conversations.
 */
function rateLabel(m: ModelInfo, t: (s: string) => string): string {
  // Each modality sells in a different unit, and the unit is what makes the
  // number mean anything. Label everything "per image" and a video model's
  // per-second rate reads as the price of one picture.
  if (m.modality === 'image') return t('{n} 크레딧 / 장').replace('{n}', (m.creditPerImage ?? m.creditCost).toLocaleString())
  if (m.modality === 'video') {
    // Resolution and audio spread the rate by up to 2.7×. Showing only the
    // minimum would set that expectation for somebody about to render 1080p
    // with sound, so the range is printed as it is.
    const rates = Object.values(m.creditPerSecond ?? {})
    if (!rates.length) return t('가격 미상')
    const low = Math.min(...rates)
    const high = Math.max(...rates)
    return low === high
      ? t('{n} 크레딧 / 초').replace('{n}', low.toLocaleString())
      : t('{low}~{high} 크레딧 / 초')
          .replace('{low}', low.toLocaleString())
          .replace('{high}', high.toLocaleString())
  }
  if (m.modality === 'audio') {
    return m.creditPerCall
      ? t('{n} 크레딧 / 회').replace('{n}', m.creditPerCall.toLocaleString())
      : t('{n} 크레딧').replace('{n}', m.creditCost.toLocaleString())
  }
  if (m.creditCost === 0 && m.inputCreditCost === 0) {
    return m.dataBoundary === 'self_hosted' ? t('자체 운영 · 무료') : t('외부 제공 · 무료')
  }
  return t('1k당 입력 {in} · 출력 {out}')
    .replace('{in}', m.inputCreditCost.toLocaleString())
    .replace('{out}', m.creditCost.toLocaleString())
}

export function ModelPicker({
  kind,
  sessionId,
  compact = false,
  variant = 'toolbar',
  label,
  modality,
  onEnableAuto,
  onBusyChange,
}: {
  kind: SessionKind
  /** When set, the picker reads and writes *this conversation's* model. */
  sessionId?: string | null
  compact?: boolean
  /**
   * `field` dresses the trigger as a form control so settings can pick the
   * default for a surface out of this same menu. What a model costs and where
   * its text goes decide the choice, and they were only ever on screen for the
   * one-turn pick.
   */
  variant?: 'toolbar' | 'field'
  /**
   * The surface this picker sets the default for. A `<select>` carries its own
   * label; a button names only the model, so the field variant has to say what
   * it is a default for out loud.
   */
  label?: string
    /**
     * Narrows the list once more. Audio and video share the `av` surface, so
     * `kinds` alone would offer speech models where a video is being made.
     */
  modality?: ModelInfo['modality']
  /** On a fresh `/new/chat`, the composer creates a real session first. */
  onEnableAuto?: () => void | Promise<void>
  /** Prevents a turn from racing the session PATCH/creation behind a choice. */
  onBusyChange?: (busy: boolean) => void
}) {
  const t = useT()
  const field = variant === 'field'
  const [selectionPending, setSelectionPending] = useState(false)
  const {
    models,
    modelByKind,
    agents,
    setModel,
    setSessionModel,
    setSessionRoutingMode,
    sessions,
    modelsLoading,
    litellmAvailable,
    autoRouting,
  } = useStore()
  const session = sessionId ? sessions.find((s) => s.id === sessionId) : undefined
  const usable = models.filter(
    (m) => m.kinds.includes(kind) && (!modality || m.modality === modality),
  )

    // Inside a conversation, whatever that conversation will actually run on —
    // the surface default would name the wrong one on an old thread, and the
    // wrong one again on a thread that is deferring to its agent.
  const currentId = effectiveModelId(session, kind, agents, modelByKind)
  const active = usable.find((m) => m.id === currentId) ?? usable[0]
  const autoActive = kind === 'chat' && session?.routingMode === 'auto'
  // Auto belongs to a conversation, so it is offered only where there is one to
  // write it to — or a caller standing by to make one. Settings has neither,
  // and an Auto row there would be a button that quietly does nothing.
  const canRouteAuto = kind === 'chat' && (Boolean(sessionId) || Boolean(onEnableAuto))
  const persistSelection = async (action: () => void | Promise<void>) => {
    if (selectionPending) return
    setSelectionPending(true)
    onBusyChange?.(true)
    try {
      await action()
    } finally {
      setSelectionPending(false)
      onBusyChange?.(false)
    }
  }

  if (!active) {
    // An empty picker and a broken proxy look identical otherwise.
    return (
      <span
        className={cn(
          'flex items-center gap-1.5 text-base text-faint',
          field ? 'h-9 w-full rounded-control border border-line bg-panel px-3' : 'px-2 py-1.5',
        )}
      >
        <Cpu size={14} />
        {modelsLoading ? t('모델 불러오는 중…') : t('사용 가능한 모델 없음')}
      </span>
    )
  }

  return (
    <Dropdown
      align={field ? 'left' : 'right'}
      className={
        field
          ? 'w-full min-w-0'
          : 'w-[calc(100vw-2rem)] max-w-[340px] min-w-0 sm:w-auto sm:min-w-[340px]'
      }
      trigger={({ open }) => (
        <button
          type="button"
          disabled={selectionPending}
          aria-busy={selectionPending}
          aria-label={label ? `${label}: ${active.label}` : undefined}
          className={cn(
            'flex h-9 items-center gap-1.5 rounded-control text-base font-medium transition-colors',
            field
              ? cn(
                  'w-full justify-between border bg-panel px-3',
                  open ? 'border-accent' : 'border-line hover:border-line-strong',
                )
              : cn(
                  'shrink-0 px-2.5',
                  open ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
                ),
          )}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            {autoActive ? <Gauge size={14} /> : <Cpu size={14} />}
            <span className={cn('truncate', !field && 'max-w-[220px]')}>
              {autoActive ? `${t('Auto · 비용 절약')} · ${active.label}` : active.label}
            </span>
          </span>
          {!compact && <ChevronDown size={14} className="shrink-0 text-faint" />}
        </button>
      )}
    >
      <ModelMenu
        usable={usable}
        active={active}
        autoActive={autoActive}
        autoRouting={autoRouting}
        showAuto={canRouteAuto}
        litellmAvailable={litellmAvailable}
        selectionPending={selectionPending}
        onAuto={() => {
          return persistSelection(() => {
            if (onEnableAuto) return onEnableAuto()
            if (sessionId) return setSessionRoutingMode(sessionId, 'auto')
          })
        }}
        onPick={(id) => {
          // Keyed off the id given, not off finding its row: the sidebar list
          // can lag behind the URL, and a change would land on the surface
          // default instead of the conversation.
          return persistSelection(() => {
            if (sessionId) return setSessionModel(sessionId, id)
            setModel(kind, id)
          })
        }}
      />
    </Dropdown>
  )
}

/**
 * Menu body, split out so it can call `useMenuClose`: the hook reads the
 * Dropdown's context, which is only in scope inside the panel.
 */
function ModelMenu({
  usable,
  active,
  autoActive,
  autoRouting,
  showAuto,
  litellmAvailable,
  selectionPending,
  onAuto,
  onPick,
}: {
  usable: ModelInfo[]
  active: ModelInfo
  autoActive: boolean
  autoRouting: ReturnType<typeof useStore.getState>['autoRouting']
  showAuto: boolean
  litellmAvailable: boolean
  selectionPending: boolean
  onAuto: () => void | Promise<void>
  onPick: (id: string) => void | Promise<void>
}) {
  const t = useT()
  const closeMenu = useMenuClose()
  // Insertion order, so the catalogue's own ordering still decides which vendor
  // comes first rather than the alphabet.
  const groups = [...usable.reduce((map, m) => {
    const vendor = m.vendor || m.provider
    map.set(vendor, [...(map.get(vendor) ?? []), m])
    return map
  }, new Map<string, ModelInfo[]>())]
  const autoReason =
    autoRouting.reason === 'classifier_unavailable'
      ? t('strict-local 분류 모델을 사용할 수 없습니다.')
      : autoRouting.reason === 'no_economy_models'
        ? t('사용 가능한 절약 모델이 없습니다.')
        : t('관리자가 Auto 비용 절약을 켜지 않았습니다.')
  return (
    <>
      {showAuto && (
        <div className="border-b border-line p-1.5">
          <button
            type="button"
            disabled={!autoRouting.available || selectionPending}
            onClick={() => {
              void onAuto()
              closeMenu()
            }}
            className="flex w-full items-start gap-2.5 rounded-control px-2.5 py-2 text-left transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-55"
          >
            <span className="mt-0.5 w-4 shrink-0 text-accent">
              {autoActive ? <Check size={14} /> : <Gauge size={14} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="text-base font-semibold">{t('Auto · 비용 절약')}</span>
                <Badge tone={autoRouting.available ? 'success' : 'neutral'}>
                  {autoRouting.available ? t('사용 가능') : t('사용 불가')}
                </Badge>
              </span>
              <span className="mt-0.5 block text-sm text-muted">
                {autoRouting.available
                  ? t('간단한 질문은 관리자가 지정한 절약 모델로 보내고, 복잡하면 현재 모델을 유지합니다.')
                  : autoReason}
              </span>
              <span className="mt-1 block truncate text-xs text-faint">
                {t('품질 모델')}: {active.label}
              </span>
            </span>
          </button>
        </div>
      )}
      <div className="px-2.5 pt-2 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
        {showAuto ? t('모델 직접 선택') : t('모델')}
      </div>
      {!litellmAvailable && (
        <div className="mx-1.5 mb-1 flex items-start gap-2 rounded-control border border-warn/30 bg-warn/5 px-2.5 py-2 text-sm text-warn">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          <span>
            {t('모델 목록을 모두 불러오지 못했습니다. 지금은 일부 모델만 고를 수 있습니다.')}
          </span>
        </div>
      )}
      {groups.map(([vendor, rows]) => (
        <div key={vendor}>
          {/* Grouped by who built the model, not by routing slug. A flat list of
              thirty names is read by scanning for a vendor anyway — "the Claude
              one" is how the choice is actually made. */}
          {groups.length > 1 && (
            <div className="px-2.5 pt-2 pb-0.5 text-2xs font-semibold tracking-wide text-faint uppercase">
              {vendor}
            </div>
          )}
          {rows.map((m) => (
        <button
          key={m.id}
          type="button"
          onClick={() => {
            void onPick(m.id)
            // Choosing ends the interaction: left open, the panel covers the
            // composer and the next trigger click reads as "close".
            closeMenu()
          }}
          disabled={selectionPending}
          className="flex w-full items-start gap-2.5 rounded-control px-2.5 py-2 text-left transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="mt-0.5 w-4 shrink-0 text-accent">
            {m.id === active.id && <Check size={14} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-base font-medium">{m.label}</span>
              <Badge>{m.provider}</Badge>
              {m.strictLocal && (
                <Badge tone="success">
                  <ShieldCheck size={10} />
                  strict-local
                </Badge>
              )}
              {!m.strictLocal && m.dataBoundary === 'self_hosted' && (
                <Badge tone="warn">{t('self-hosted · strict 미확인')}</Badge>
              )}
              {!m.strictLocal && m.dataBoundary === 'hybrid' && (
                <Badge tone="warn">{t('외부 전환 가능')}</Badge>
              )}
              {m.dataBoundary === 'external' && <Badge tone="warn">{t('외부 제공')}</Badge>}
              {m.dataBoundary === 'unknown' && (
                <Badge tone="warn">{t('경계 미확인')}</Badge>
              )}
              {m.id.endsWith(':free') && <Badge tone="success">{t('무료')}</Badge>}
              {m.adapter && (
                <Badge tone="warn">
                  <Plug size={10} />
                  {t('어댑터')}
                </Badge>
              )}
            </span>
            <span className="mt-0.5 block truncate text-sm text-muted">{m.description}</span>
            <span className="mt-1 flex items-center gap-2 text-xs text-faint">
              <span>{rateLabel(m, t)}</span>
              {m.contextWindow && <span>{formatTokens(m.contextWindow)} ctx</span>}
              {m.supportsVision && <Eye size={11} />}
              {m.supportsTools && <Wrench size={11} />}
            </span>
          </span>
        </button>
          ))}
        </div>
      ))}
    </>
  )
}
