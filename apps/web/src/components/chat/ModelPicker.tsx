import { Check, ChevronDown, Cpu, Eye, Plug, ShieldCheck, TriangleAlert, Wrench } from 'lucide-react'
import { Badge, Dropdown, useMenuClose } from '@/components/ui'
import { cn, formatTokens } from '@/lib/utils'
import { useStore } from '@/store/useStore'
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
  modality,
}: {
  kind: SessionKind
  /** When set, the picker reads and writes *this conversation's* model. */
  sessionId?: string | null
  compact?: boolean
    /**
     * Narrows the list once more. Audio and video share the `av` surface, so
     * `kinds` alone would offer speech models where a video is being made.
     */
  modality?: ModelInfo['modality']
}) {
  const t = useT()
  const { models, modelByKind, setModel, setSessionModel, sessions, modelsLoading, litellmAvailable } =
    useStore()
  const session = sessionId ? sessions.find((s) => s.id === sessionId) : undefined
  const usable = models.filter(
    (m) => m.kinds.includes(kind) && (!modality || m.modality === modality),
  )

    // Inside a conversation, that conversation's model — the surface default
    // would name the wrong one on an old thread.
  const currentId = session?.model || modelByKind[kind]
  const active = usable.find((m) => m.id === currentId) ?? usable[0]

  if (!active) {
    // An empty picker and a broken proxy look identical otherwise.
    return (
      <span className="flex items-center gap-1.5 px-2 py-1.5 text-[13px] text-faint">
        <Cpu size={14} />
        {modelsLoading ? t('모델 불러오는 중…') : t('사용 가능한 모델 없음')}
      </span>
    )
  }

  return (
    <Dropdown
      align="right"
      className="min-w-[340px]"
      trigger={({ open }) => (
        <button
          className={cn(
            'flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-[13px] font-medium transition-colors',
            open ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
          )}
        >
          <Cpu size={14} />
          <span className="max-w-[200px] truncate">{active.label}</span>
          {!compact && <ChevronDown size={14} className="text-faint" />}
        </button>
      )}
    >
      <ModelMenu
        usable={usable}
        active={active}
        litellmAvailable={litellmAvailable}
        onPick={(id) => {
          // Keyed off the id given, not off finding its row: the sidebar list
          // can lag behind the URL, and a change would land on the surface
          // default instead of the conversation.
          if (sessionId) void setSessionModel(sessionId, id)
          else setModel(kind, id)
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
  litellmAvailable,
  onPick,
}: {
  usable: ModelInfo[]
  active: ModelInfo
  litellmAvailable: boolean
  onPick: (id: string) => void
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
  return (
    <>
      <div className="px-2.5 pt-2 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">
        {t('모델')}
      </div>
      {!litellmAvailable && (
        <div className="mx-1.5 mb-1 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 px-2.5 py-2 text-[12px] text-warn">
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
            <div className="px-2.5 pt-2 pb-0.5 text-[10px] font-semibold tracking-wide text-faint uppercase">
              {vendor}
            </div>
          )}
          {rows.map((m) => (
        <button
          key={m.id}
          onClick={() => {
            onPick(m.id)
            // Choosing ends the interaction: left open, the panel covers the
            // composer and the next trigger click reads as "close".
            closeMenu()
          }}
          className="flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-elevated"
        >
          <span className="mt-0.5 w-4 shrink-0 text-accent">
            {m.id === active.id && <Check size={14} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[13px] font-medium">{m.label}</span>
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
            <span className="mt-0.5 block truncate text-xs text-muted">{m.description}</span>
            <span className="mt-1 flex items-center gap-2 text-[11px] text-faint">
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
