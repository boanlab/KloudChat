import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { sessionsApi } from '@/lib/api'
import {
  CONTEXT_PARTS,
  compactTokens,
  gaugeLevel,
  gaugePercent,
  summaryLimit,
  withDraft,
  type ContextPart,
  type ContextUsage,
} from '@/lib/contextGauge'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { effectiveModelId, useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

/** Wait after the conversation, model or attachments change before asking the server again. */
const REFRESH_MS = 350

const PART_LABEL: Record<ContextPart, string> = {
  system: '기본 시스템 지침',
  instructions: '에이전트·프로젝트 지침',
  memory: '대화 요약·메모리',
  files: '첨부·프로젝트 자료',
  history: '대화 기록',
  draft: '입력 중인 글',
  tools: '도구 정의',
}

const TONE = { ok: 'text-accent', near: 'text-warn', full: 'text-danger' } as const

function Ring({ percent, tone }: { percent: number; tone: string }) {
  const r = 7
  const length = 2 * Math.PI * r
  return (
    <svg viewBox="0 0 18 18" className={cn('size-[18px] shrink-0 -rotate-90', tone)} aria-hidden>
      <circle cx="9" cy="9" r={r} fill="none" strokeWidth="2.5" className="stroke-line-strong" />
      <circle
        cx="9"
        cy="9"
        r={r}
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray={`${(length * percent) / 100} ${length}`}
      />
    </svg>
  )
}

/**
 * How much of the model's window the conversation's next turn takes: a ring with the percent,
 * and on hover or tap the breakdown. Chat only; the draft is counted as it is typed.
 */
export function ContextGauge({
  sessionId,
  kind,
  projectId,
  draft,
  attachmentIds,
  phone,
}: {
  sessionId: string | null
  kind: SessionKind
  projectId?: string | null
  draft: string
  attachmentIds: string[]
  phone: boolean
}) {
  const t = useT()
  const session = useStore((s) => (sessionId ? s.sessions.find((row) => row.id === sessionId) : undefined))
  const agents = useStore((s) => s.agents)
  const modelByKind = useStore((s) => s.modelByKind)
  const models = useStore((s) => s.models)
  const streaming = useStore((s) => !!sessionId && !!s.running[sessionId])
  const modelId = effectiveModelId(session, kind, agents, modelByKind)
  const [usage, setUsage] = useState<ContextUsage | null>(null)
  const [open, setOpen] = useState(false)
  const [hover, setHoverNow] = useState(false)
  // Leaving the ring for the popover crosses a gap: closing waits a moment.
  const leaving = useRef<number | undefined>(undefined)
  const setHover = useCallback((on: boolean) => {
    window.clearTimeout(leaving.current)
    if (on) setHoverNow(true)
    else leaving.current = window.setTimeout(() => setHoverNow(false), 150)
  }, [])
  useEffect(() => () => window.clearTimeout(leaving.current), [])
  const anchor = useRef<HTMLDivElement>(null)
  const popover = useRef<HTMLDivElement>(null)
  const popoverId = useId()
  const [place, setPlace] = useState({ right: 8, bottom: 8 })

  // Asked again when the conversation grows (a turn ends), the model or the attachments change.
  const messageCount = session?.messages.length ?? 0
  const attachmentsKey = attachmentIds.join(',')
  // the writing surfaces: a report's and a deck's turns read the same context as a chat's
  const enabled = kind === 'chat' || kind === 'report' || kind === 'slides'
  // A conversation held in the browser (`unsent`) has no server row until its first send: it is
  // counted as a new one, from its project and agent, not asked for by an id the server lacks.
  const started = !!session && !session.unsent
  const askedId = started ? sessionId : null
  const startProjectId = started ? undefined : (session?.projectId ?? projectId ?? null)
  const startAgentId = started ? undefined : (session?.agentId ?? null)
  // the question the server counts with is the one typed when it is asked; later keystrokes
  // are counted here (`withDraft`)
  const draftRef = useRef(draft)
  useEffect(() => {
    draftRef.current = draft
  }, [draft])
  useEffect(() => {
    if (!enabled || streaming) return
    let live = true
    const timer = window.setTimeout(() => {
      sessionsApi
        .contextUsage({
          sessionId: askedId,
          kind: kind === 'report' || kind === 'slides' ? kind : 'chat',
          projectId: startProjectId,
          agentId: startAgentId,
          model: modelId || null,
          draft: draftRef.current,
          attachments: attachmentsKey ? attachmentsKey.split(',') : [],
        })
        .then((next) => live && setUsage(next))
        .catch(() => live && setUsage(null))
    }, REFRESH_MS)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [enabled, kind, askedId, started, startProjectId, startAgentId, modelId, attachmentsKey, messageCount, streaming])

  const shown = open || hover
  const measure = useCallback(() => {
    const box = anchor.current?.getBoundingClientRect()
    if (!box) return
    setPlace({
      right: Math.max(8, window.innerWidth - box.right),
      bottom: Math.max(8, window.innerHeight - box.top + 6),
    })
  }, [])
  useLayoutEffect(() => {
    if (shown) measure()
  }, [shown, measure])
  // A popover wider than the room left of the ring moves right until it fits the screen.
  useLayoutEffect(() => {
    const box = popover.current?.getBoundingClientRect()
    if (!shown || !box || box.left >= 8) return
    setPlace((was) => ({ ...was, right: Math.max(8, window.innerWidth - box.width - 8) }))
  }, [shown, place.right])
  useEffect(() => {
    if (!open) return
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (anchor.current?.contains(target) || popover.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', measure)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', measure)
    }
  }, [open, measure])

  if (!enabled || !usage) return null
  const now = withDraft(usage, draft)
  const percent = gaugePercent(now.used, now.window)
  const level = gaugeLevel(now)
  const label = (id: string | null | undefined) => models.find((m) => m.id === id)?.label ?? id ?? ''
  const tokens = t('대화 {used} / {window} 토큰')
    .replace('{used}', compactTokens(now.used))
    .replace('{window}', compactTokens(now.window))
  const willSummarise = now.used >= summaryLimit(now)

  return (
    <div
      ref={anchor}
      className="relative inline-flex shrink-0"
      data-testid="context-gauge"
      data-level={level}
      onPointerEnter={(event) => event.pointerType === 'mouse' && setHover(true)}
      onPointerLeave={(event) => event.pointerType === 'mouse' && setHover(false)}
    >
      <span
        role="meter"
        aria-label={t('컨텍스트 사용량')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${percent}% · ${tokens}`}
        className={cn('flex h-9 items-center gap-1 rounded-control px-1.5 text-sm tabular-nums', TONE[level])}
      >
        <Ring percent={percent} tone={TONE[level]} />
        {!phone && <span>{percent}%</span>}
      </span>
      <button
        type="button"
        className="absolute inset-0 rounded-control hover:bg-elevated/60 focus-visible:outline-2 focus-visible:outline-accent"
        aria-label={`${t('컨텍스트 사용량 자세히')} (${percent}%)`}
        aria-expanded={shown}
        aria-controls={shown ? popoverId : undefined}
        onClick={() => setOpen((was) => !was)}
        onFocus={(event) => event.currentTarget.matches(':focus-visible') && setHover(true)}
        onBlur={() => setHover(false)}
      />
      {shown &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            ref={popover}
            id={popoverId}
            role="dialog"
            aria-label={t('컨텍스트 사용량')}
            data-testid="context-gauge-popover"
            style={
              phone
                ? { position: 'fixed', left: 8, right: 8, bottom: place.bottom }
                : { position: 'fixed', right: place.right, bottom: place.bottom }
            }
            className={cn(
              'animate-fade-up z-[100] rounded-card border border-line bg-panel p-3 text-sm text-fg shadow-overlay',
              !phone && 'w-72 max-w-[calc(100vw-1rem)]',
            )}
            onPointerEnter={(event) => event.pointerType === 'mouse' && setHover(true)}
            onPointerLeave={(event) => event.pointerType === 'mouse' && setHover(false)}
          >
            <p className="flex items-baseline justify-between gap-2 font-medium">
              <span>{tokens}</span>
              <span className={cn('tabular-nums', TONE[level])}>{percent}%</span>
            </p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-elevated" aria-hidden>
              <div
                className={cn('h-full rounded-full', level === 'ok' ? 'bg-accent' : level === 'near' ? 'bg-warn' : 'bg-danger')}
                style={{ width: `${percent}%` }}
              />
            </div>
            <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-muted">
              {CONTEXT_PARTS.filter((part) => now.parts[part] > 0).map((part) => (
                <div key={part} className="contents" data-part={part}>
                  <dt>{t(PART_LABEL[part])}</dt>
                  <dd className="text-right tabular-nums text-fg">{compactTokens(now.parts[part])}</dd>
                </div>
              ))}
            </dl>
            {(now.summarised || willSummarise) && (
              <p className="mt-3 rounded-control bg-elevated px-2 py-1.5 text-fg" data-testid="context-gauge-summary">
                {t('오래된 대화는 요약되어 이어집니다')}
                {now.summarisedMessages > 0 &&
                  ` · ${t('앞선 메시지 {n}개').replace('{n}', String(now.summarisedMessages))}`}
              </p>
            )}
            {!now.summarised && !willSummarise && (
              <p className="mt-3 text-muted">
                {t('요약 전까지 {n} 토큰 남음').replace(
                  '{n}',
                  compactTokens(Math.max(0, summaryLimit(now) - now.used)),
                )}
              </p>
            )}
            <p className="mt-2 text-muted" data-testid="context-gauge-model">
              {t('모델 {model} · 창 {window} 토큰')
                .replace('{model}', label(now.model))
                .replace('{window}', compactTokens(now.statedWindow))}
              {now.foreignTokenizer && ` · ${t('외부 모델은 토큰을 다르게 세어 창을 작게 잡습니다')}`}
            </p>
            {now.routed && (
              <p className="mt-1 text-muted">
                {now.routedBasis === 'last'
                  ? t('Auto가 턴마다 모델을 고릅니다. 마지막으로 답한 모델 기준입니다')
                  : t('Auto가 턴마다 모델을 고릅니다. 기본 모델 기준입니다')}
              </p>
            )}
            {now.lastTurn && (
              <p className="mt-1 text-muted">
                {t('지난 답의 실제 입력 {n} 토큰(도구 단계 합산)').replace(
                  '{n}',
                  compactTokens(now.lastTurn.inputTokens),
                )}
              </p>
            )}
            <p className="mt-2 text-xs text-faint">{t('보낼 내용을 미리 센 추정치입니다')}</p>
          </div>,
          document.body,
        )}
    </div>
  )
}
