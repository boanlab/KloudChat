import {
  ChevronDown,
  ChevronUp,
  CircleCheck,
  FileWarning,
  ImagePlus,
  ListOrdered,
  Loader2,
  Plus,
  RotateCcw,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Badge, Button } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { PendingPlan, SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** Clarification or outline approval before artifact generation. */
export function ProposalCard({
  sessionId,
  pending,
  kind,
}: {
  sessionId: string
  pending: PendingPlan
  kind: SessionKind
}) {
  const t = useT()
  const send = useStore((s) => s.send)
  const streaming = useStore((s) => !!s.running[sessionId])
  const [picked, setPicked] = useState<Record<string, string>>({})

  // Hooks stay above the stage-specific early returns.
  // The person's edits to the outline, or null if untouched.
  const [edited, setEdited] = useState<{ title: string; layout?: string }[] | null>(null)
  // Null means the plan decides, however late it arrives (the card mounts while streaming).
  const [pickedDensity, setPickedDensity] = useState<string | null>(null)
  // The look is the plan's; the outline card does not ask for it (the ribbon changes it).
  const visualStyle = pending.plan?.visualStyle ?? 'editorial'
  const density = pickedDensity ?? pending.plan?.density ?? 'speaker'
  const setDensity = setPickedDensity

  const run = (
    opts: {
      approve?: boolean
      answers?: Record<string, string>
      includeFigures?: boolean
      plan?: Record<string, unknown>
    },
    label: string,
  ) =>
    // The approval is a second request; the server needs the attachments again.
    void send(sessionId, kind, label, { ...opts, attachments: pending.attachments })

  if (pending.stage === 'figures') {
    // Asked before writing: prose written for figures refers to them.
    const drawn = pending.figures ?? []
    const credits = pending.figureCredits ?? 0
    return (
      <Shell tone="accent" icon={<ImagePlus size={15} />} title={t('그림을 넣을까요?')}>
        <ul className="space-y-1">
          {drawn.map((figure, i) => (
            <li key={`${i}-${figure.caption}`} className="flex items-baseline gap-2 text-base">
              <span className="w-5 shrink-0 text-right text-sm tabular-nums text-faint">
                {figure.section + 1}
              </span>
              <span className="min-w-0 flex-1">{figure.caption}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm text-muted">
          {t('{n}장 · 약 {c} 크레딧 · {m}')
            .replace('{n}', String(drawn.length))
            .replace('{c}', credits.toLocaleString())
            .replace('{m}', pending.figureModel ?? '')}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={streaming}
            onClick={() =>
              run({ approve: true, includeFigures: true }, t('그림을 넣어 주세요'))
            }
          >
            {streaming ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}
            {t('그림 넣고 생성')}
          </Button>
          <Button
            size="sm"
            disabled={streaming}
            onClick={() =>
              run({ approve: true, includeFigures: false }, t('그림 없이 생성해 주세요'))
            }
          >
            {t('그림 없이 생성')}
          </Button>
        </div>
      </Shell>
    )
  }

  if (pending.stage === 'clarify') {
    const questions = pending.questions ?? []
    const answered = questions.every((q) => picked[q.id])
    return (
      <Shell tone="warn" icon={<FileWarning size={15} />} title={t('시작하기 전에')}>
        <div className="space-y-3">
          {questions.map((q) => (
            <div key={q.id}>
              <p className="text-base font-medium">{q.question}</p>
              {q.detail && <p className="mt-0.5 text-sm text-muted">{q.detail}</p>}
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {q.options.map((option) => (
                  <button
                    key={option}
                    onClick={() => setPicked((current) => ({ ...current, [q.id]: option }))}
                    className={cn(
                      'rounded-control border px-2.5 py-1 text-base transition-colors',
                      picked[q.id] === option
                        ? 'border-accent bg-accent-soft text-accent'
                        : 'border-line hover:bg-elevated',
                    )}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <p className="text-sm text-faint">
            {t('고를 것이 없으면 아래 입력창에 직접 적어도 됩니다.')}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={streaming || !answered}
              title={answered ? undefined : t('먼저 위 항목을 골라 주세요')}
              onClick={() => run({ answers: picked }, Object.values(picked).join(' · '))}
            >
              {streaming ? <Loader2 size={13} className="animate-spin" /> : null}
              {t('이대로 계속')}
            </Button>
            <Button
              size="sm"
              disabled={streaming}
              onClick={() => run({ answers: {} }, t('있는 자료로 진행해 주세요'))}
            >
              {t('있는 자료로 진행')}
            </Button>
          </div>
        </div>
      </Shell>
    )
  }

  const plan = pending.plan ?? {}
  const proposed: { title: string; layout?: string }[] =
    plan.slides ?? plan.blocks ?? (plan.sections ?? []).map((title) => ({ title }))
  // Titles are editable and rows move or delete; layouts stay the planner's.
  const items = edited ?? proposed
  const change = (next: { title: string; layout?: string }[]) => setEdited(next)
  const move = (from: number, by: number) => {
    const to = from + by
    if (to < 0 || to >= items.length) return
    const next = [...items]
    ;[next[from], next[to]] = [next[to], next[from]]
    change(next)
  }
  // Dirty only when a pick differs from the plan.
  const dirty =
    (edited !== null && JSON.stringify(edited) !== JSON.stringify(proposed)) ||
    (Boolean(plan.slides) &&
      pickedDensity !== null &&
      pickedDensity !== (plan.density ?? 'speaker'))
  // Only the shape the surface stores; `sections` is headings.
  const asPlan = () =>
    plan.sections
      ? { ...plan, visualStyle, sections: items.map((i) => i.title) }
      : plan.slides
        ? { ...plan, visualStyle, density, slides: items }
        : { ...plan, blocks: items }

  return (
    <Shell
      tone="accent"
      icon={<ListOrdered size={15} />}
      title={plan.title || t('이렇게 구성하려고 합니다')}
    >
      <ol className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex items-center gap-1.5 text-base">
            <span className="w-5 shrink-0 text-right text-sm tabular-nums text-faint">
              {i + 1}
            </span>
            <input
              value={item.title}
              onChange={(e) => {
                const next = [...items]
                next[i] = { ...next[i], title: e.target.value }
                change(next)
              }}
              aria-label={t('{n}번 제목').replace('{n}', String(i + 1))}
              className="min-w-0 flex-1 rounded-control border border-transparent bg-transparent px-1.5 py-0.5 hover:border-line focus:border-accent focus:bg-panel focus:outline-none"
            />
            {item.layout && <Badge>{item.layout}</Badge>}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('{n}번 위로').replace('{n}', String(i + 1))}
              disabled={i === 0}
              onClick={() => move(i, -1)}
            >
              <ChevronUp size={13} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('{n}번 아래로').replace('{n}', String(i + 1))}
              disabled={i === items.length - 1}
              onClick={() => move(i, 1)}
            >
              <ChevronDown size={13} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('{n}번 지우기').replace('{n}', String(i + 1))}
              disabled={items.length <= 1}
              onClick={() => change(items.filter((_, at) => at !== i))}
            >
              <X size={13} />
            </Button>
          </li>
        ))}
      </ol>
      {/* No look picker here: the outline is about what is said. The design is chosen,
          and changed, from the document's own ribbon (홈 › 디자인). */}
      {plan.slides && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-sm font-medium text-fg">{t('어떻게 사용할 자료인가요?')}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {([
              ['speaker', t('발표하면서 설명'), t('한 장에 한 가지 핵심, 큰 글자와 짧은 문장')],
              ['reading', t('자료만 전달'), t('설명 없이 읽어도 이해되는 표·근거·세부 내용')],
            ] as const).map(([value, label, description]) => (
              <button type="button" key={value} aria-pressed={density === value} onClick={() => setDensity(value)} className={cn('rounded-lg border px-3 py-2 text-left transition', density === value ? 'border-accent bg-accent-soft' : 'border-line hover:bg-elevated')}>
                <span className="block text-sm font-medium text-fg">{label}</span>
                <span className="block text-xs text-muted">{description}</span>
              </button>
            ))}
          </div>
        </fieldset>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            change([...items, { title: '', ...(items[0]?.layout ? { layout: items[0].layout } : {}) }])
          }
        >
          <Plus size={13} />
          {t('항목 추가')}
        </Button>
        {dirty && (
          <Button size="sm" variant="ghost" onClick={() => {
            setEdited(null)
            setPickedDensity(null)
          }}>
            <RotateCcw size={13} />
            {t('처음 제안으로')}
          </Button>
        )}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={streaming || items.every((i) => !i.title.trim())}
          onClick={() =>
            run(
              { approve: true, ...(dirty ? { plan: asPlan() } : {}) },
              dirty ? t('고친 구성으로 생성해 주세요') : t('이대로 생성해 주세요'),
            )
          }
        >
          {streaming ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <CircleCheck size={13} />
          )}
          {dirty ? t('고친 대로 생성') : t('이대로 생성')}
        </Button>
        <span className="text-sm text-muted">
          {t('제목을 직접 고치거나, 크게 바꿀 것이 있으면 아래 입력창에 적어 주세요.')}
        </span>
      </div>
    </Shell>
  )
}

function Shell({
  tone,
  icon,
  title,
  children,
}: {
  tone: 'accent' | 'warn'
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'animate-fade-up rounded-card border px-4 py-3',
        tone === 'warn' ? 'border-warn/40 bg-warn/5' : 'border-accent/30 bg-accent-soft/40',
      )}
    >
      <p
        className={cn(
          'mb-2 flex items-center gap-2 text-base font-medium',
          tone === 'warn' ? 'text-warn' : 'text-accent',
        )}
      >
        {icon}
        {title}
      </p>
      {children}
    </div>
  )
}
