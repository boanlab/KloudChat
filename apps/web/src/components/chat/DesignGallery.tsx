import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Globe,
  LayoutGrid,
  Paperclip,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Badge, Button, Dropdown, MenuItem, Modal } from '@/components/ui'
import {
  argumentText,
  templateText,
  downloadFile,
  templatesApi,
  type DesignTemplateRow,
  type FileRow,
  type TemplateRow,
} from '@/lib/api'
import { Input } from '@/components/ui'
import { currentLang } from '@/lib/i18n'
import { cn, upsertById } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { TemplateForm } from '@/components/chat/TemplateForm'
import { useDesignTemplates, useStartTemplate } from '@/lib/useDesignTemplates'
import { useT } from '@/lib/useT'

/**
 * The blanks a media template leaves, and the button that fills them in.
 *
 * The filled sentence goes to the composer rather than the model: on these
 * surfaces the prompt is the entire input, so a template that sent something
 * unread would be one nobody could correct. Every value starts at the
 * template's own default, so the card is usable without typing.
 */

/**
 * How many of a template's rules the card prints before folding the rest away.
 *
 * The files hold six or seven, as sentences rather than chips, and printed
 * whole they stop the card being scannable in a grid. Three works because the
 * structural rules come first in every file — the ones that tell two shapes
 * apart.
 */
const CHECKS_ON_CARD = 3

/**
 * A card in the sentence half of the grid, whichever list it came from.
 *
 * The two lists agree on every field but the form, so they are concatenated
 * rather than branched on: a built-in framing and one somebody wrote behave
 * the same way once they are on screen.
 */
type Sentence = {
  id: string
  title: string
  description: string
  fills: string[]
  prompt: string
  /** An uploaded 양식 this starting point writes into, when there is one. */
  form: FileRow | null
  /** Offered to the whole organisation rather than written by this person. */
  shared: boolean
  /** Whether the caller may rewrite or remove it. Built-ins never are. */
  own: boolean
  /** 업무별 탐색을 위한 분류. 개인 시작점에는 없을 수 있다. */
  group: string
  /** The 서식 this job comes out wearing, or `''` when it has no fixed shape. */
  renderTemplateId: string
  /** One worked example per blank, in `fills` order. */
  examples: string[]
  /** What the job cannot run without: 'web' | 'file'. */
  needs: string[]
  /** Workspace skills to switch on for the turn, by name. */
  skills: string[]
}

/**
 * Whether picking a sentence fills the composer or rides with the turn.
 *
 * On the two media surfaces the sentence *is* the prompt — the person edits a
 * description of a picture and sends that, and a turn carrying an unseen
 * framing would leave them with nothing to edit. Everywhere else the framing
 * is the machinery's, and it goes with the turn instead of into their mouth:
 * typed into the box it comes back out in the transcript under their name,
 * which is the same reason a 서식's example sentence is not typed there either.
 */
const fillsTheComposer = (kind: SessionKind) => kind === 'image' || kind === 'av'

/**
 * Cards to a page. Four, in the two columns the grid already has, so a page is
 * a 2×2 block that fits without scrolling.
 *
 * The catalogue is seventeen 서식 and however many sentences somebody has
 * kept, and all of it in one column was a dialog you scrolled and scrolled.
 */
const PER_PAGE = 4

/** One card in the grid, from either half. `words` is what a search reads. */
type Card = {
  key: string
  words: string
  design?: DesignTemplateRow
  sentence?: Sentence
}

/**
 * What a review will read the finished document against.
 *
 * The honest difference between two shapes of the same kind: 회의록 keeps what
 * was decided apart from what was discussed, 안내문 wants grounds, an
 * effective date and its attachments.
 *
 * 확인하는 것 rather than 지키는 것: the lines are the questions a reviewer
 * asks of the finished document. The shape cannot promise the answers, only
 * see to it that they are asked.
 *
 * Korean in both languages — there is no English checklist to fall back to,
 * and writing one here would put words in the reviewer's mouth. `lang` says
 * which language it is, so a browser breaks its lines as Korean.
 */

/**
 * The 서식 itself, miniature.
 *
 * Seventeen 서식 differ almost entirely in CSS, and text on a card cannot show
 * CSS — so the card shows the finished thing, shrunk. sandbox with no
 * permissions: nothing in a card is meant to be clicked, and the route is
 * public static.
 *
 * The scale is measured rather than written down. A fixed `scale(0.39)` is a
 * scale for one card width, and the card is a grid cell — at 1440px it is
 * 480px wide, and a 820px page shrunk by 0.39 covers 320 of them, leaving a
 * third of every card blank. Measured against the window it goes in, the page
 * fills the card at any width the grid hands it.
 */
function Thumbnail({ id, deck }: { id: string; deck: boolean }) {
  const window_ = useRef<HTMLDivElement>(null)
  // Decks are wide, documents are tall; both are shown from the top.
  const width = deck ? 1280 : 820
  const [scale, setScale] = useState(0)

  useEffect(() => {
    const node = window_.current
    if (!node) return
    const fit = () => setScale(node.clientWidth / width)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [width])

  return (
    <div
      ref={window_}
      className="relative h-28 shrink-0 overflow-hidden border-b border-line bg-elevated"
      aria-hidden="true"
    >
      <iframe
        src={`/api/design-templates/${id}/preview`}
        sandbox=""
        tabIndex={-1}
        loading="lazy"
        title=""
        className="pointer-events-none absolute left-0 top-0 origin-top-left"
        // Hidden until measured: drawn at scale 1 for even one frame, an 820px
        // page paints over the card beside it.
        style={{ width, height: 448, transform: `scale(${scale})`, visibility: scale ? 'visible' : 'hidden' }}
      />
    </div>
  )
}

function Checks({ checks }: { checks: string[] }) {
  const t = useT()
  if (checks.length === 0) return null
  const rest = checks.slice(CHECKS_ON_CARD)
  const line = (text: string) => (
    <li key={text} className="flex gap-1.5">
      <span aria-hidden>·</span>
      <span>{text}</span>
    </li>
  )
  // 접어 둔다. 이 목록이 카드에서 가장 큰 덩이이고, 펼쳐 두면 카드 하나가
  // 515px 이 되어 2×2 한 쪽이 화면에 들어가지 않는다. 고르는 자리에서 필요한
  // 것은 "이 서식이 무엇을 따지는가" 이지 여덟 줄 전부가 아니고, 몇 개인지는
  // 접힌 채로도 말해 준다.
  return (
    <details className="rounded-control bg-elevated p-2 text-xs leading-relaxed">
      <summary className="cursor-pointer font-medium text-muted transition-colors hover:text-fg">
        {t('이 서식이 확인하는 것 {n}개').replace('{n}', String(checks.length))}
      </summary>
      <ul lang="ko" className="mt-1 space-y-0.5 text-faint">
        {checks.slice(0, CHECKS_ON_CARD).map(line)}
      </ul>
      {rest.length > 0 && (
        <ul lang="ko" className="mt-0.5 space-y-0.5 text-faint">
          {rest.map(line)}
        </ul>
      )}
    </details>
  )
}

/**
 * One entry of the catalogue: its own shape, what it asks you to bring, what
 * it will be read against, and the button that starts it.
 *
 * The same card in the gallery and on the 디자인 screen, because the two ask
 * one question in two rooms. Where the button leads is the caller's business —
 * inside a session it fills the composer that is already open, and from the
 * catalogue there is no session yet, so the surface has to be opened first.
 *
 * The preview is the template's *own* seed filled with its own sample, so what
 * is on the card is the thing that will be produced rather than a screenshot
 * of it. The frame is sandboxed with no permissions at all, which is also why
 * the seeds carry no script.
 */
export function DesignTemplateCard({
  row,
  english,
  onPick,
  chosen,
}: {
  row: DesignTemplateRow
  english: boolean
  onPick: (row: DesignTemplateRow, prompt: string) => void
  /** True when this is the 서식 the session is already wearing. */
  chosen?: boolean
}) {
  const t = useT()
  const text = templateText(row, english)
  return (
    // `h-full` and a column: a row holds a 서식 card and a sentence card side
    // by side, and they are nothing like the same height. Without this the
    // shorter one floats at the top of its cell with a band of nothing under
    // it, which reads as a card that failed to load.
    <div
      className={cn(
        'group flex h-full flex-col overflow-hidden rounded-card border bg-panel transition-colors',
        // 지금 입고 있는 서식. 고른 것을 말해 주지 않으면 고르지 않은 것과
        // 같아 보이고, 같아 보이면 한 번 더 누른다.
        chosen ? 'border-accent' : 'border-line hover:border-line-strong',
      )}
    >
      {row.hasPreview && <Thumbnail id={row.id} deck={row.kind === 'deck'} />}
      {/* 가운데는 스크롤하고 발치는 고정한다. 행 높이는 격자가 정하므로,
          펼친 점검 목록처럼 행보다 큰 내용은 여기 안에서 흘러야 한다 —
          카드째 잘리면 시작 버튼이 사라지고, 카드째 늘리면 쪽 높이가 흔들린다. */}
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          <div className="flex items-start justify-between gap-2">
            <p className="text-base font-medium">{text.name}</p>
            <Badge>{text.category}</Badge>
          </div>
          {/* 두 줄이면 고르기에 충분하다. 긴 설명은 카드 높이를 제각각으로
              만들던 첫 번째 범인이다. */}
          <p className="line-clamp-2 text-sm text-muted">{text.description}</p>
          <Checks checks={row.checks} />
          {/* 무엇을 물을지만 보여 준다. 답은 입력창에서 받는다 — 카드에
              빈칸을 다섯 개 두면 시작하기 전에 결심을 시키는 셈이고, 그만큼
              키가 큰 카드는 격자를 넘쳐 다음 줄 위로 겹쳤다. */}
          {(row.arguments.length > 0 ? row.arguments.map((a) => argumentText(a, english).label) : text.fills).length > 0 && (
            <div className="flex flex-wrap gap-1">
              {(row.arguments.length > 0 ? row.arguments.map((a) => argumentText(a, english).label) : text.fills).map((fill) => (
                <span
                  key={fill}
                  className="rounded-full border border-line px-2 py-0.5 text-xs text-faint"
                >
                  {fill}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="pt-2">
          <Foot chosen={chosen}>
            <Button
              size="sm"
              disabled={chosen}
              onClick={() => onPick(row, row.arguments.length > 0 ? '' : text.examplePrompt)}
            >
              {chosen ? t('고른 서식') : row.figure ? t('이 도식으로 시작') : t('이 서식으로 시작')}
            </Button>
          </Foot>
        </div>
      </div>
    </div>
  )
}

/**
 * The row of actions at the bottom of a card.
 *
 * 시작 버튼 하나와, 이미 고른 서식이라는 말. 여기에는 한동안 그 서식이 싣고
 * 다니는 빈 양식을 `.docx`·`.pptx` 로 내려받는 버튼이 함께 있었다. 파일은
 * 그대로 남아 있다 — 모델이 무엇을 채워야 하는지 그 파일에서 읽고, 카드가
 * 보여 주는 모양이 그 파일과 어긋나지 않는지 시험이 지킨다. 다만 그것을
 * 사람이 받아 가는 문은 닫는다.
 */
function Foot({
  chosen,
  children,
}: {
  chosen?: boolean
  children: React.ReactNode
}) {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {children}
      {chosen && <span className="text-xs text-faint">{t('이미 고른 서식입니다')}</span>}
    </div>
  )
}

/**
 * Shapes the answer can come out in, for the surface being worked on.
 *
 * The prompt gallery beside it answers "what do I ask for"; this one answers
 * "what should it look like when it arrives". They are separate because the
 * two choices are independent — any prompt can be written into any shape.
 *
 * One surface at a time, because that is the question a session asks. The
 * whole catalogue, every surface at once, is on the 디자인 screen.
 *
 * The cards are filled in the colours the session will actually be rendered
 * in: a shape chosen here comes out wearing the project's design system, so a
 * card that showed the default indigo was advertising a document nobody would
 * receive.
 */
export function DesignGalleryModal({
  kind,
  sessionId,
  open,
  onClose,
}: {
  kind: SessionKind
  /** The conversation whose 서식 is already chosen, when there is one. */
  sessionId?: string | null
  open: boolean
  onClose: () => void
}) {
  const t = useT()
  // 이 대화상자는 이제 한 가지만 묻는다: 무슨 일을 시작할 것인가.
  //
  // 결과 서식 was the other tab, and asking it separately meant asking about
  // typography of somebody who came to write an incident report. A 시작점
  // brings the shape its job comes in; a job with no fixed shape leaves the
  // surface to choose one from the subject. The whole 서식 catalogue is still
  // its own screen for anybody who wants to browse it — /designs.
  const [category, setCategory] = useState<string | 'all'>('all')
  const rows = useDesignTemplates(open)
  const start = useStartTemplate()
  const setDraft = useStore((s) => s.setDraft)
  const pendingTemplate = useStore((s) => s.pendingTemplate)
  const sessions = useStore((s) => s.sessions)
  const setPendingAttachment = useStore((s) => s.setPendingAttachment)
  const setPendingStartingTemplate = useStore((s) => s.setPendingStartingTemplate)

  const english = currentLang() === 'en'
  const forSurface = useMemo(
    () =>
      rows
        .filter((r) => r.surface === kind)
        .map((r) => ({ row: r, text: templateText(r, english) })),
    [rows, kind, english],
  )

  /** Saved prompts shown alongside rendering templates. */
  const saved = useStore((s) => s.promptTemplates)
  /**
   * The person's own, which the merge left behind.
   *
   * The store carries the built-in catalogue and nothing else, so folding the
   * old picker in here quietly dropped every starting point somebody had
   * written — along with the 양식 file such a card can carry, which is the
   * whole of "이 양식대로 써 줘": the model reads the document's actual shape
   * instead of a description of it.
   *
   * Fetched when the gallery opens rather than on mount. This sits under every
   * empty composer and the request buys nothing until somebody looks.
   */
  const [mine, setMine] = useState<TemplateRow[]>([])
  /**
   * The one being written, or `'new'` while writing a fresh one.
   *
   * Somewhere to write one at all. The old picker carried 시작점 추가 and the
   * merge did not bring it, which left the API, the form and the cards in
   * place with no way to make a card — a person could use a starting point
   * somebody else had written and never write their own.
   */
  const [writing, setWriting] = useState<TemplateRow | 'new' | null>(null)
  useEffect(() => {
    if (!open) return
    let live = true
    void templatesApi
      .list()
      .then((rows) => live && setMine(rows))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [open])

  const sentences = useMemo<Sentence[]>(
    () => [
      ...mine
        .filter((row) => row.kind === kind)
        .map((row) => ({
          id: row.id,
          title: row.title,
          description: row.description,
          fills: row.fills,
          prompt: row.prompt,
          // Enough of the file to stand as an attachment chip. The composer
          // renders a name, a token count and an extraction error; the rest it
          // never reads.
          form: row.fileId
            ? {
                id: row.fileId,
                name: row.fileName,
                size: 0,
                mime: '',
                tokens: row.fileTokens,
                projectId: null,
                sessionId: null,
                preview: '',
                error: row.fileError,
                createdAt: row.updatedAt,
              }
            : null,
          shared: row.shared,
          own: row.mine !== false,
          group: row.group || '내 시작점',
          renderTemplateId: row.renderTemplateId || '',
          examples: row.examples ?? [],
          needs: row.needs ?? [],
          skills: [],
        })),
      ...saved
        .filter((row) => row.kind === kind)
        .map((row) => ({
          id: row.id,
          title: row.title,
          description: row.description,
          fills: row.fills,
          prompt: row.prompt,
          form: null,
          shared: false,
          // The built-in catalogue is the product's, not anybody's to edit.
          own: false,
          group: row.group || '기본',
          renderTemplateId: row.renderTemplateId || '',
          examples: row.examples ?? [],
          needs: row.needs ?? [],
          skills: row.skills ?? [],
        })),
    ],
    [mine, saved, kind],
  )
  /*
   * What this chip gathers: the starting points somebody saved, beside the
   * shipped 서식 categories. It read 내 문장 — "my sentences" — which names
   * the thing by what it is made of rather than by what it does, and sits
   * oddly next to 업무 · 학업 · 연구. The cards under it already say
   * 시작점으로 붙이기, so the chip says the same word.
   */
  /**
   * 이 표면에서 시작점 노릇을 하는 것.
   *
   * On 챗 · 보고서 · 슬라이드 a 시작점 is a job and a 서식 is the shape it
   * comes in, so the cards are the jobs and each one names its own shape.
   *
   * On 이미지 and 오디오/동영상 there is no such split: the 서식 *is* the job —
   * 방법 구조도, 티저 그림, 포스터 — and its example sentence is the prompt.
   * Those surfaces ship no 시작점 at all, so a dialogue that only ever draws
   * 시작점 came up empty on them: 「조건에 맞는 시작점이 없습니다」 on a screen
   * with six 서식 behind it.
   */
  const formatsAreTheJobs = sentences.length === 0 && forSurface.length > 0

  const categories = useMemo(
    () =>
      formatsAreTheJobs
        ? [...new Set(forSurface.map((c) => c.text.category))]
        : [...new Set(sentences.map((row) => row.group))],
    [formatsAreTheJobs, forSurface, sentences],
  )
  const visibleSentences =
    category === 'all' ? sentences : sentences.filter((row) => row.group === category)
  const visibleFormats =
    category === 'all' ? forSurface : forSurface.filter((c) => c.text.category === category)

  /**
   * The two halves as one list, so a page can be cut across both.
   *
   * They were rendered as two loops, which is fine while everything is on one
   * screen and wrong the moment it is not: a page boundary that falls inside
   * the 서식 half leaves the sentences unreachable, and one that falls between
   * them makes a short page.
   */
  const cards: Card[] = useMemo(
    () =>
      formatsAreTheJobs
        ? visibleFormats.map(({ row, text }) => ({
            key: row.id,
            design: row,
            words: `${text.name} ${text.description} ${text.category} ${text.fills.join(' ')}`,
          }))
        : visibleSentences.map((row) => ({
            key: row.id,
            sentence: row,
            words: `${row.title} ${row.description} ${row.group} ${row.fills.join(' ')}`,
          })),
    [formatsAreTheJobs, visibleFormats, visibleSentences],
  )

  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const found = needle
    ? cards.filter((card) => card.words.toLowerCase().includes(needle))
    : cards

  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(found.length / PER_PAGE))
  // Clamped rather than reset. Narrowing the search from page three should
  // land on the last page that still has cards, not throw the reader back to
  // the first one every keystroke.
  const at = Math.min(page, pages - 1)
  const shown = found.slice(at * PER_PAGE, at * PER_PAGE + PER_PAGE)

  /**
   * The 서식 this session is already wearing.
   *
   * Two places say so and they disagree for one turn: the pick somebody just
   * made and has not sent, and the row the server wrote once a turn used it.
   * The pick wins while it stands, because it is the more recent answer to the
   * same question.
   */
  const worn =
    (pendingTemplate?.surface === kind ? pendingTemplate.id : null) ??
    sessions.find((row) => row.id === sessionId)?.renderTemplateId ??
    null

  const pick = (row: DesignTemplateRow, prompt: string) => {
    // 이미 고른 것을 다시 고르지 않는다. 같은 서식을 두 번 고르는 것은 아무
    // 일도 아니지만, 아무 일도 아닌 것을 누르게 두면 눌린 것이 무엇이었는지
    // 헷갈린다 — 카드가 고른 상태를 말하고 버튼이 잠기는 이유다.
    if (row.id === worn) return
    start(row, prompt)
    // 빈칸은 입력창이 묻는다. A media 서식 with arguments used to ask them on
    // the card; now the card names them and the composer asks, the same
    // way a written starting point does on the other surfaces.
    if (row.arguments.length > 0) {
      const text = templateText(row, english)
      setPendingStartingTemplate({
        id: row.id,
        title: text.name,
        fills: row.arguments.map((a) => argumentText(a, english).label),
        examples: row.arguments.map((a) => argumentText(a, english).initial),
        blanks: row.arguments.map((a) => ({
          name: a.name,
          options: argumentText(a, english).options,
          long: Boolean(a.long),
        })),
        examplePrompt: text.examplePrompt,
      })
    }
    onClose()
  }

  /**
   * 시작점마다 사람이 골라 준 모양.
   *
   * 서식 열일곱 가운데 열이 어떤 시작점의 이름에도 없어 고를 길이 없었다.
   * They are built, previewed and maintained, and the only door to them —
   * the 결과 서식 tab — was folded into this list without bringing them
   * along, so 「사건 보고서」 was reachable and 「연구 노트」 was not. The
   * shape stays a property of the job, as it should be; it is now one the
   * person can change before starting.
   */
  const [shapeOf, setShapeOf] = useState<Record<string, string>>({})

  //: Which card's procedure is unfolded — one at a time.
  const [openPrompt, setOpenPrompt] = useState<string | null>(null)
  const pickStartingPoint = (row: Sentence) => {
    // 채운 빈칸을 한 문장으로. Only the ones that were filled — a blank line
    // 「기간·언어: 」 tells the model nothing and the reader that something
    // was forgotten.
    if (fillsTheComposer(kind)) setDraft(row.prompt || row.title)
    else
      setPendingStartingTemplate({
        id: row.id,
        title: row.title,
        fills: row.fills,
        examples: row.examples,
        needs: row.needs,
        skills: row.skills,
      })
    setPendingAttachment(row.form)
    // 그 일이 입고 나올 모양까지 함께 걸친다.
    //
    // 결과 서식 was a second tab: choose the job, then choose what it looks
    // like. The second question is about typography and it was being asked of
    // somebody who came to write an incident report — which has a shape, and
    // that shape is `doc-incident`. A 시작점 that names one applies it here;
    // one that names none leaves the surface to pick a look from the subject,
    // which is what `deck._theme_style` and `report._outline_style` do.
    const wanted = shapeOf[row.id] ?? row.renderTemplateId
    const shape = wanted ? rows.find((one) => one.id === wanted) : undefined
    if (shape && shape.id !== worn) start(shape, '')
    onClose()
  }

  const closeForm = () => setWriting(null)

  /**
   * Optimistic. The card is theirs and the list is short, so a spinner on a
   * delete they just asked for is only a delay; a failure puts it back.
   */
  const remove = async (id: string) => {
    const held = mine
    setMine((rows) => rows.filter((r) => r.id !== id))
    try {
      await templatesApi.remove(id)
    } catch {
      setMine(held)
    }
  }

  // Writing one takes the whole dialog rather than opening a second over it: a
  // form on top of a grid of cards is two scrolling regions and no clear place
  // to look.
  if (writing) {
    return (
      <Modal
        open={open}
        onClose={() => {
          closeForm()
          onClose()
        }}
        title={writing === 'new' ? t('시작점 만들기') : t('시작점 수정')}
        width="max-w-3xl"
      >
        <TemplateForm
          kind={kind}
          template={writing === 'new' ? undefined : writing}
          onCancel={closeForm}
          onSaved={(row) => {
            setMine((rows) => upsertById(rows, row))
            closeForm()
            // 방금 쓴 것을 보여 준다. 쪽이 나뉘어 있으므로 저장하고 돌아온
            // 자리가 첫 쪽이면, 넷째 뒤에 붙은 새 카드는 어디에도 없는 것과
            // 같다 — 만들었는데 사라진 것처럼 보인다.
            setCategory('all')
            setQuery(row.title)
            setPage(0)
          }}
        />
      </Modal>
    )
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('작업 시작하기')}
      description={t('일을 고르면 입력창이 그 일에 필요한 것만 묻습니다. 웹 검색이나 파일이 필요한 일은 카드가 미리 말해 줍니다.')}
      width="max-w-3xl"
    >
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">
              {formatsAreTheJobs ? t('어떤 모양으로 받을까요?') : t('어떤 일을 시작할까요?')}
            </h3>
            <p className="text-sm text-muted">
              {formatsAreTheJobs
                ? t('빈칸을 채우면 그대로 요청이 됩니다.')
                : t('고르면 입력창이 그 일에 필요한 것만 묻습니다.')}
            </p>
          </div>
          {!formatsAreTheJobs && (
            <Button size="sm" onClick={() => setWriting('new')}>
              <Plus size={13} />
              {t('내 시작점 만들기')}
            </Button>
          )}
        </div>
        <div className="mb-3 flex items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {categories.length > 1 &&
              (['all', ...categories] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => {
                    setCategory(c)
                    setPage(0)
                  }}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-sm transition-colors',
                    category === c
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:text-fg',
                  )}
                >
                  {c === 'all' ? t('전체') : c}
                </button>
              ))}
          </div>
          {/* 이름·설명·준비물을 함께 읽는다. 서식을 찾는 사람은 이름을 외우고
              있지 않고 "각주" 나 "표본" 처럼 자기가 해야 할 일을 기억한다. */}
          <div className="relative shrink-0">
            <Search
              size={13}
              className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-faint"
            />
            <Input
              aria-label={formatsAreTheJobs ? t('서식 검색') : t('시작점 검색')}
              placeholder={
                formatsAreTheJobs ? t('서식 또는 용도 검색') : t('업무 또는 준비물 검색')
              }
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(0)
              }}
              className="h-8 w-44 pl-7 text-sm"
            />
          </div>
        </div>

        {/* 두 줄 자리를 늘 잡아 두고 위부터 채운다.
            쪽마다 카드 높이가 달라 — 서식 카드는 204px, 문장 카드는 116px —
            대화상자가 382 와 686 사이를 오르내렸다. 넘길 때마다 상자가 자라고
            줄면 닫기 버튼도 쪽 넘김도 매번 다른 자리에 있다.

            204 × 2 + 사이 12. 바닥이지 천장이 아니므로, 더 큰 카드가 오는 쪽은
            그만큼 자란다 — 잘라 내는 것보다 낫다. 남는 자리는 아래에 둔다. */}
        {/* Fixed window, evenly split rows. The 서식 cards grew a live
           miniature and pages stopped agreeing on a height, so the dialog
           breathed between pages — close and 다음 쪽 moved on every turn. A
           fixed window pins that; even rows keep the two cards of a row the
           same size; and the card below pins its buttons to its foot and
           scrolls only its middle, so neither dead air nor clipping shows. */}
        {/* 창은 정말로 고정이어야 한다. `min-h` 와 `max-h` 사이에서는 쪽마다
            내용만큼 자라므로, 마지막 쪽이 넷이 아니라 둘인 순간 상자가 줄고
            닫기 버튼이 움직인다 — 고정 창이라고 적어 둔 자리에서. 높이를
            못박고, 넘치는 쪽은 이미 그렇듯 안에서 스크롤한다. */}
        {/* 스크롤을 갖는 요소와 행 높이를 정하는 그리드는 다른 요소여야 한다.
            둘을 하나로 합치면 auto 트랙이 고정 높이 예산 안에서 나눠 갖는
            쪽으로 굳어, 카드 하나가 펼쳐져도 그 행이 못 자라고 다음 행과
            겹친다 — 바닥으로 쓴 높이가 천장이 되어 버리는 것. 바깥은 창을
            고정하고 넘치면 스크롤하며, 안쪽 그리드는 자기 콘텐츠만큼 자란다. */}
        <div className="h-[58vh] min-h-80 overflow-y-auto pr-1">
        <div className="grid content-start gap-3 sm:grid-cols-2">
          {shown.map((card) =>
            card.design ? (
              <DesignTemplateCard
                key={card.key}
                row={card.design}
                english={english}
                onPick={pick}
                chosen={card.design.id === worn}
              />
            ) : (
              ((row) => (
              <div key={row.id} className="group relative flex min-h-44 flex-col">
              <div className="flex w-full flex-1 flex-col rounded-card border border-line p-3 text-left transition-colors hover:border-accent">
                <p className="flex items-center gap-1.5 text-base font-medium">
                  {row.title}
                  {/* 조직의 것인지 내가 쓴 것인지. 같은 격자에 섞여 있으므로
                      말해 주지 않으면 남의 문장을 제 것으로 고치려 든다. */}
                  {row.shared && <Badge>{t('공용')}</Badge>}
                </p>
                {row.description && (
                  <p className="mt-0.5 text-sm text-muted">{row.description}</p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {/* 제목 옆에 이미 공용이라고 적혀 있다. 분류가 마침 공용인
                      시작점은 같은 낱말을 한 카드에 두 번 싣게 되므로, 여기서는
                      뺀다 — 두 번 적힌 말은 두 가지를 뜻하는 것처럼 읽힌다. */}
                  {!(row.shared && row.group === '공용') && <Badge>{row.group}</Badge>}
                  {/* 이 일이 어떤 모양으로 나올지. 결과 서식을 따로 고르게
                      하던 자리를 대신한다 — 고르는 대신 말해 주는 것으로. */}
                  {(() => {
                    const wanted = shapeOf[row.id] ?? row.renderTemplateId
                    const shape = wanted ? rows.find((one) => one.id === wanted) : undefined
                    return (
                      <Dropdown
                        trigger={() => (
                          <button
                            type="button"
                            aria-label={t('{name} 결과 모양 고르기').replace('{name}', row.title)}
                            className={cn(
                              'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors',
                              shape
                                ? 'border-accent/40 bg-accent-soft text-accent hover:border-accent'
                                : 'border-line text-muted hover:border-line-strong hover:text-fg',
                            )}
                          >
                            {shape ? templateText(shape, english).name : t('모양 고르기')}
                            <ChevronDown size={11} />
                          </button>
                        )}
                      >
                        {/* 고르지 않는 것도 하나의 선택이다 — 그때는 주제를
                            보고 표면이 스스로 모양을 잡는다. */}
                        <MenuItem onClick={() => setShapeOf((all) => ({ ...all, [row.id]: '' }))}>
                          {t('주제에 맞게 새로 만들기')}
                        </MenuItem>
                        {forSurface.map(({ row: one, text }) => (
                          <MenuItem
                            key={one.id}
                            onClick={() => setShapeOf((all) => ({ ...all, [row.id]: one.id }))}
                          >
                            {text.name}
                          </MenuItem>
                        ))}
                      </Dropdown>
                    )
                  })()}
                </div>
                {/* 무엇을 가져와야 하는지. 고른 뒤 입력창이 이것을 이름으로
                    물으므로, 고르기 전에 같은 말을 보여 준다 — 카드가 붙여 줄
                    문장을 미리 적어 두는 것과는 다른 일이다. */}
                {/* 무엇을 물을지. 답은 입력창이 받는다 — 고른 뒤, 챗을 시작하면서. */}
                {row.fills.length > 0 && (
                  <div className="mt-2">
                    <p className="mb-1 text-xs font-medium text-muted">{t('적어 달라고 할 것')}</p>
                    <div className="flex flex-wrap gap-1">
                      {row.fills.map((fill) => (
                        <span
                          key={fill}
                          className="rounded-full border border-line px-2 py-0.5 text-xs text-faint"
                        >
                          {fill}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {/* 이 일이 무엇으로 되는지. Said before the pick: web search
                    the answer is found with, a file it is about, the skills it
                    runs with. Nothing here is a surprise after sending. */}
                {(row.needs.length > 0 || row.skills.length > 0) && (
                  <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                    {row.needs.includes('web') && <span className="inline-flex items-center gap-1"><Globe size={11} />{t('웹 검색으로 찾습니다')}</span>}
                    {row.needs.includes('file') && <span className="inline-flex items-center gap-1"><Paperclip size={11} />{t('파일을 첨부해야 합니다')}</span>}
                    {row.skills.length > 0 && <span className="inline-flex items-center gap-1"><Sparkles size={11} />{row.skills.join(' · ')}</span>}
                  </p>
                )}
                {/* 한 번에 하나만 펼친다. Two open procedures side by side push the
                    row's cards to different heights and the grid stops reading as a
                    grid; the person opened the second one to compare, and the first
                    was already read.

                    Controlled outright: the summary's own toggle is cancelled and
                    the state decides, so the one that was open closes and the new
                    one opens in the same paint — the browser opening the second
                    first, and the first closing a frame later, jumped the grid and
                    left the new one where it had been before the shift. The card
                    is then kept in view, since closing the one above it moves it. */}
                <details
                  className="mt-2 rounded-control bg-elevated px-2.5 py-2 text-xs leading-relaxed"
                  open={openPrompt === row.id}
                >
                  <summary
                    className="cursor-pointer font-medium text-muted"
                    onClick={(e) => {
                      e.preventDefault()
                      const card = e.currentTarget.closest('details')
                      setOpenPrompt((current) => (current === row.id ? null : row.id))
                      requestAnimationFrame(() =>
                        card?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }),
                      )
                    }}
                  >
                    {t('실제 작업 방식 보기')}
                  </summary>
                  <p className="mt-1.5 whitespace-pre-wrap text-faint">{row.prompt}</p>
                </details>
                <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2">
                  <Button
                    size="sm"
                    aria-label={t('{name} 시작점 선택').replace('{name}', row.title)}
                    onClick={() => pickStartingPoint(row)}
                  >
                    {t('이 시작점 선택')}
                  </Button>
                  {row.form && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t('{name} 양식 내려받기').replace('{name}', row.title)}
                      onClick={() => void downloadFile(row.form!.id, row.form!.name)}
                    >
                      <Download size={13} />
                      {row.form.name}
                    </Button>
                  )}
                  {/* 다음이 무엇인지. 「이번 요청에만 적용」 was scope jargon
                      nobody needed at this moment; what they need is to know
                      the request lands in the box, editable, before it goes. */}
                  <span className="text-xs text-faint">{t('고르면 입력창이 묻습니다')}</span>
                </div>
              </div>
                {/* 쓴 사람만. 공용은 관리자 화면에서 고친다 — 여기서 고치면
                    한 사람이 조직 전체의 문장을 바꾸게 된다.

                    카드 안이 아니라 위에 얹는다. 카드가 버튼 하나이므로 안에
                    또 버튼을 두면 중첩이 되고, 고치려다 고르게 된다. */}
                {row.own && (
                  <div className="absolute top-2 right-2 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 수정').replace('{name}', row.title)}
                      onClick={() => {
                        const held = mine.find((r) => r.id === row.id)
                        if (held) setWriting(held)
                      }}
                    >
                      <Pencil size={13} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', row.title)}
                      onClick={() => void remove(row.id)}
                    >
                      <Trash2 size={13} />
                    </Button>
                  </div>
                )}
              </div>
              ))(card.sentence!)
            ),
          )}
          {shown.length === 0 && (
            <p className="col-span-full py-8 text-center text-sm text-faint">
              {formatsAreTheJobs
                ? t('조건에 맞는 서식이 없습니다.')
                : t('조건에 맞는 시작점이 없습니다.')}
            </p>
          )}
        </div>
        </div>

      {/* 쪽 넘김. 한 쪽이 2×2 이므로 스크롤 없이 한눈에 들어오고, 어디쯤인지는
          숫자가 말한다 — 점만 찍어 두면 열일곱 개 가운데 어디인지 알 수 없다. */}
      {pages > 1 && (
        <div className="mt-3 flex items-center justify-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('이전 쪽')}
            disabled={at === 0}
            onClick={() => setPage(at - 1)}
          >
            <ChevronLeft size={14} />
          </Button>
          <span className="text-sm text-muted tabular-nums">
            {at + 1} / {pages}
          </span>
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('다음 쪽')}
            disabled={at >= pages - 1}
            onClick={() => setPage(at + 1)}
          >
            <ChevronRight size={14} />
          </Button>
        </div>
      )}

      {/* 하나 쓰는 자리. 목록 아래에 두는 것은 '고르기' 가 이 화면의 일이고
          '쓰기' 는 그다음이기 때문이다. */}
    </Modal>
  )
}

/**
 * Whether a surface offers this picker at all.
 *
 * 챗 does not. A 서식 is a shape a result comes out in — a report's sections,
 * a deck's slides, a picture's frame — and a conversation has no shape to
 * choose: what came back here was a list of example sentences, offered under
 * a name that promises a document. The other three surfaces keep it, and a
 * saved sentence for 챗 is still what 시작점 offers on the empty screen.
 *
 * One answer rather than a condition at each door: the composer's own button
 * asked the same question and would otherwise have kept opening the modal the
 * gallery button no longer shows.
 */
export function offersTemplates(kind: SessionKind): boolean {
  // Chat has no rendering template, but it does have saved starting points.
  // Hiding the only picker made those rows impossible to use from a fresh
  // conversation even though they remained editable in settings.
  return kind === 'chat' || kind === 'report' || kind === 'slides' || kind === 'image' || kind === 'av'
}

/**
 * The button that opens it, where a surface has anything to offer.
 *
 * Rendered on the empty state of a new session and in the composer's own menu,
 * so the choice is reachable after the first turn as well — a shape you can
 * only pick before you start is one you cannot change your mind about.
 */
export function DesignGallery({
  kind,
  sessionId,
}: {
  kind: SessionKind
  sessionId?: string | null
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  // Surface-specific templates and saved prompts
  const has = useStore(
    (s) =>
      s.designTemplates.some((row) => row.surface === kind) ||
      s.promptTemplates.some((row) => row.kind === kind),
  )
  if (!offersTemplates(kind) || !has) return null
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        <LayoutGrid size={14} />
        {t('작업 시작하기')}
      </Button>
      <DesignGalleryModal
        kind={kind}
        sessionId={sessionId}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  )
}
