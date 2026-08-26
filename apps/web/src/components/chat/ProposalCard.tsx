import { CircleCheck, FileWarning, ListOrdered, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { PendingPlan, SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * What a document intends to write, before it writes it.
 *
 * These surfaces used to produce a document from every sentence typed at them,
 * including a question, and the document replaced whatever was there. So a
 * request the model could not ground — an attached paper that arrived a third
 * read — still produced a deck, about nothing in particular, in place of the
 * one somebody had spent the afternoon on.
 *
 * Now a generation stops here. In `clarify` it is holding a question it needs
 * answered; in `outline` it is holding the shape it means to write. Neither
 * has produced an artifact, which is the actual protection: there is nothing
 * to undo, because nothing has been replaced.
 *
 * The buttons are the only thing that writes. Typing goes on working — a note
 * in the composer re-plans with that note taken into account — which is the
 * back-and-forth these surfaces never had.
 */
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

  const run = (opts: { approve?: boolean; answers?: Record<string, string> }, label: string) =>
    void send(sessionId, kind, label, opts)

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
          {/* Said out loud, because a chip list reads as a closed set and this
              one is not: the box below takes an answer nobody thought to offer. */}
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
            {/* Deliberately available. The point is not to make people answer
                questions, it is to stop the guessing being invisible. */}
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
  const items: { title: string; layout?: string }[] =
    plan.slides ?? plan.blocks ?? (plan.sections ?? []).map((title) => ({ title }))

  return (
    <Shell
      tone="accent"
      icon={<ListOrdered size={15} />}
      title={plan.title || t('이렇게 구성하려고 합니다')}
    >
      <ol className="space-y-1">
        {items.map((item, i) => (
          <li key={`${i}-${item.title}`} className="flex items-baseline gap-2 text-base">
            <span className="w-5 shrink-0 text-right text-sm tabular-nums text-faint">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1">{item.title}</span>
            {item.layout && <Badge>{item.layout}</Badge>}
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={streaming}
          onClick={() => run({ approve: true }, t('이대로 생성해 주세요'))}
        >
          {streaming ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <CircleCheck size={13} />
          )}
          {t('이대로 생성')}
        </Button>
        <span className="text-sm text-muted">
          {t('고칠 곳이 있으면 아래 입력창에 적어 주세요. 다시 구성해 옵니다.')}
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
