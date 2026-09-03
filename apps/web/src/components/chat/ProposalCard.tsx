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

  /*
   * The outline the person has made of the proposal, if they have touched it.
   * Held at the top because the clarify and figures stages return before the
   * outline is ever built, and a hook behind a return runs in a different
   * order on the next render.
   */
  const [edited, setEdited] = useState<{ title: string; layout?: string }[] | null>(null)
  /*
   * 고른 적이 없으면 계획을 따른다.
   *
   * These were `useState(pending.plan?.visualStyle ?? 'editorial')`, and
   * `useState` keeps only the value of the first render. The card mounts while
   * the turn is still streaming, so on the render that matters `pending.plan`
   * is often not there yet — the initial value froze at `editorial`, the plan
   * arrived a moment later saying `minimal`, and two things went wrong at
   * once: the impression the outline had chosen for the subject was silently
   * replaced by the default, and `dirty` went true, so a card nobody had
   * touched offered 「고친 대로 생성」.
   *
   * Held as "what the person picked, or nothing yet". Nothing yet means the
   * plan decides, however late it arrives.
   */
  const [pickedStyle, setPickedStyle] = useState<string | null>(null)
  const [pickedDensity, setPickedDensity] = useState<string | null>(null)
  const visualStyle = pickedStyle ?? pending.plan?.visualStyle ?? 'editorial'
  const density = pickedDensity ?? pending.plan?.density ?? 'speaker'
  const setVisualStyle = setPickedStyle
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
    // The files the request arrived with go back out with the approval. This
    // is a second request, and the server builds its context from what this
    // one carries — without them the approved outline is written from the
    // sentence alone, against the document the person actually attached.
    void send(sessionId, kind, label, { ...opts, attachments: pending.attachments })

  if (pending.stage === 'figures') {
    /*
     * The second of two questions, and the expensive one.
     *
     * Asked apart from the outline on purpose. A picture costs multiples of
     * what the prose does, and a figure changes the sentences beside it — a
     * section told a diagram is coming writes 아래 그림과 같이. Folding both
     * into one 이대로 생성 meant somebody approving a shape also bought
     * pictures, and somebody who wanted the shape without them had no way to
     * say so.
     *
     * Asked *before* the writing for the same reason: decline it afterwards
     * and the prose still refers to figures that are not there.
     */
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
        {/* 장수와 값을 묻는 자리에 함께 둔다. 무엇을 사는지 모르고 누르는
            버튼은 승인이 아니다. */}
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
  const proposed: { title: string; layout?: string }[] =
    plan.slides ?? plan.blocks ?? (plan.sections ?? []).map((title) => ({ title }))
  /*
   * The outline, editable in place.
   *
   * It used to be a list you could only accept or argue with: changing one
   * heading meant typing a note, waiting for the planner again, and reading a
   * whole new outline to find out whether the rest survived. For a word. So
   * the titles are inputs, the rows move and delete, and 이대로 생성 sends what
   * is on screen — the layouts stay the planner's, because a layout the 서식
   * does not style is a section with no design.
   */
  const items = edited ?? proposed
  const change = (next: { title: string; layout?: string }[]) => setEdited(next)
  const move = (from: number, by: number) => {
    const to = from + by
    if (to < 0 || to >= items.length) return
    const next = [...items]
    ;[next[from], next[to]] = [next[to], next[from]]
    change(next)
  }
  // 사람이 고른 것만 「고침」이다 — 계획이 말한 것을 그대로 쓰는 것은 고친 것이
  // 아니다.
  const dirty =
    (edited !== null && JSON.stringify(edited) !== JSON.stringify(proposed)) ||
    (pickedStyle !== null && pickedStyle !== (plan.visualStyle ?? 'editorial')) ||
    (Boolean(plan.slides) &&
      pickedDensity !== null &&
      pickedDensity !== (plan.density ?? 'speaker'))
  //: Only the shape the surface actually stores. `sections` is headings.
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
      {(plan.sections || plan.slides) && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-sm font-medium text-fg">{t('어떤 인상으로 만들까요?')}</legend>
          <div className={cn('grid grid-cols-1 gap-2', plan.slides ? 'sm:grid-cols-4' : 'sm:grid-cols-3')} aria-label={t('결과물 디자인')}>
            {([
              ['editorial', t('정돈된 편집'), t('선명한 구분과 안정적인 정보 배치')],
              ['poster', t('강한 인상'), t('큰 제목과 색면으로 메시지를 강조')],
              ['minimal', t('차분한 여백'), t('장식을 덜고 내용에 집중')],
              // 덱만 입는 네 얼굴. A report has no cover to compose and no
              // slide number to set in a gutter, so it keeps the three above.
              ...(plan.slides
                ? ([
                    ['dark', t('다크'), t('어두운 바탕에 빛나는 강조색 — 기술·제품 발표')],
                    ['split', t('분할'), t('왼쪽 색면과 큰 번호 — 보고·제안')],
                    ['warm', t('따뜻한 종이'), t('크림색 바탕과 둥근 상자 — 교육·문화')],
                    ['mono', t('흑백'), t('검정 선과 큰 제목 — 디자인·건축·연구')],
                  ] as const)
                : []),
            ] as const).map(([value, label, description]) => (
              <button
                type="button"
                key={value}
                aria-pressed={visualStyle === value}
                onClick={() => setVisualStyle(value)}
                className={cn('overflow-hidden rounded-lg border text-left transition', visualStyle === value ? 'border-accent ring-2 ring-accent/20' : 'border-line hover:border-muted')}
              >
                <span className={cn('relative block aspect-video overflow-hidden', value === 'poster' ? 'bg-[#f7f0e6]' : value === 'minimal' || value === 'split' || value === 'mono' ? 'bg-white' : value === 'dark' ? 'bg-[#0f172a]' : value === 'warm' ? 'bg-[#f6f1e8]' : 'bg-[#253b80]')}>
                  {value === 'editorial' && <><span className="absolute inset-x-0 top-0 h-1 bg-[#6d7fea]"/><span className="absolute left-[12%] top-[29%] h-1 w-[18%] bg-white/80"/><span className="absolute left-[12%] top-[42%] h-3 w-[58%] bg-white"/><span className="absolute left-[12%] top-[62%] h-1.5 w-[40%] bg-white/50"/></>}
                  {value === 'poster' && <><span className="absolute inset-y-0 left-0 w-2 bg-[#d84a35]"/><span className="absolute -right-5 -top-5 size-20 rounded-full border-[10px] border-[#d84a35]/15"/><span className="absolute left-[12%] top-[22%] text-3xl font-black text-[#d84a35]/15">01</span><span className="absolute left-[12%] top-[52%] h-3 w-[70%] bg-[#27211e]"/><span className="absolute left-[12%] top-[70%] h-1.5 w-[45%] bg-[#d84a35]"/></>}
                  {value === 'minimal' && <><span className="absolute -right-8 -top-8 size-24 rounded-full bg-[#4c6fbf]/10"/><span className="absolute left-[12%] top-[35%] h-1 w-[16%] bg-[#4c6fbf]"/><span className="absolute left-[12%] top-[50%] h-2.5 w-[52%] bg-[#252525]"/><span className="absolute left-[12%] top-[68%] h-1 w-[35%] bg-[#aaa]"/></>}
                  {value === 'dark' && <><span className="absolute -right-10 -bottom-12 size-32 rounded-full bg-[#6d7fea]/40 blur-md"/><span className="absolute left-[12%] top-[32%] h-1 w-[16%] bg-white/80"/><span className="absolute left-[12%] top-[46%] h-3 w-[60%] bg-white"/><span className="absolute left-[12%] top-[66%] h-1.5 w-[38%] bg-white/40"/></>}
                  {value === 'split' && <><span className="absolute inset-y-0 left-0 w-[38%] bg-[#1f6feb]"/><span className="absolute left-[6%] bottom-[10%] text-2xl font-black text-white/40">01</span><span className="absolute left-[46%] top-[34%] h-1 w-[12%] bg-[#1f6feb]"/><span className="absolute left-[46%] top-[48%] h-3 w-[44%] bg-[#111827]"/><span className="absolute left-[46%] top-[68%] h-1.5 w-[30%] bg-[#9aa3b2]"/></>}
                  {value === 'warm' && <><span className="absolute -right-8 top-2 size-28 rounded-full bg-[#c2410c]/85"/><span className="absolute left-[12%] top-[32%] h-1 w-[14%] bg-[#c2410c]"/><span className="absolute left-[12%] top-[46%] h-3 w-[46%] rounded bg-[#3f3328]"/><span className="absolute left-[12%] top-[66%] h-1.5 w-[32%] rounded bg-[#a8998a]"/></>}
                  {value === 'mono' && <><span className="absolute left-[7%] top-[12%] h-5 w-3 border-l-2 border-t-2 border-[#111]"/><span className="absolute right-[7%] bottom-[12%] h-5 w-3 border-r-2 border-b-2 border-[#111]"/><span className="absolute left-[16%] top-[40%] h-3.5 w-[64%] bg-[#111]"/><span className="absolute left-[16%] top-[64%] h-1.5 w-[28%] bg-[#111]/50"/></>}
                </span>
                <span className="block px-2.5 py-2">
                  <span className="block text-sm font-medium text-fg">{label}</span>
                  <span className="mt-0.5 block text-xs leading-4 text-muted">{description}</span>
                </span>
              </button>
            ))}
          </div>
        </fieldset>
      )}
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
            // Back to "nothing picked", which is the plan.
            setPickedStyle(null)
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
