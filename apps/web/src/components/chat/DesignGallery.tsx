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

/** Checks printed on a card before the rest folds away. */
const CHECKS_ON_CARD = 3

/** A starting-point card, from either the built-in list or the person's own. */
type Sentence = {
  id: string
  title: string
  description: string
  fills: string[]
  prompt: string
  /** Uploaded form this starting point writes into. */
  form: FileRow | null
  /** Shared with the organisation rather than written by this person. */
  shared: boolean
  /** Whether the caller may edit or remove it; built-ins never are. */
  own: boolean
  group: string
  /** Design template the job renders in, or `''` for none. */
  renderTemplateId: string
  /** One example per blank, in `fills` order. */
  examples: string[]
  /** Requirements: 'web' | 'file'. */
  needs: string[]
  /** Workspace skills to switch on for the turn, by name. */
  skills: string[]
}

/** On media surfaces the sentence is the prompt; elsewhere it rides with the turn. */
const fillsTheComposer = (kind: SessionKind) => kind === 'image' || kind === 'av'

/** Cards per page: a 2x2 block. */
const PER_PAGE = 4

/** One card in the grid; `words` is what search reads. */
type Card = {
  key: string
  words: string
  design?: DesignTemplateRow
  sentence?: Sentence
}

/** Scaled live preview of a design template; sandboxed with no permissions. */
function Thumbnail({ id, deck }: { id: string; deck: boolean }) {
  const window_ = useRef<HTMLDivElement>(null)
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
        // Hidden until measured, or one frame at scale 1 paints over the next card.
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

/** Design template card, shared by the gallery and the designs screen. */
export function DesignTemplateCard({
  row,
  english,
  onPick,
  chosen,
}: {
  row: DesignTemplateRow
  english: boolean
  onPick: (row: DesignTemplateRow, prompt: string) => void
  /** The template the session already uses. */
  chosen?: boolean
}) {
  const t = useT()
  const text = templateText(row, english)
  return (
    <div
      className={cn(
        'group flex h-full flex-col overflow-hidden rounded-card border bg-panel transition-colors',
        chosen ? 'border-accent' : 'border-line hover:border-line-strong',
      )}
    >
      {row.hasPreview && <Thumbnail id={row.id} deck={row.kind === 'deck'} />}
      {/* The middle scrolls; the foot stays put. */}
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          <div className="flex items-start justify-between gap-2">
            <p className="text-base font-medium">{text.name}</p>
            <Badge>{text.category}</Badge>
          </div>
          <p className="line-clamp-2 text-sm text-muted">{text.description}</p>
          <Checks checks={row.checks} />
          {/* Blanks are named here and asked in the composer. */}
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

/** Action row at the foot of a card. */
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

/** Starting-point picker for one surface; on media surfaces the design templates are the jobs. */
export function DesignGalleryModal({
  kind,
  sessionId,
  open,
  onClose,
}: {
  kind: SessionKind
  /** Session whose template is already chosen, if any. */
  sessionId?: string | null
  open: boolean
  onClose: () => void
}) {
  const t = useT()
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

  /** Built-in starting points. */
  const saved = useStore((s) => s.promptTemplates)
  /** The person's own starting points, fetched when the gallery opens. */
  const [mine, setMine] = useState<TemplateRow[]>([])
  /** The one being edited, or `'new'`. */
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
          // Enough of the file for an attachment chip: name, tokens, error.
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
  // Surfaces with no starting points (image, a/v) show design templates as the jobs.
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
  // Clamped, not reset, when the result count shrinks.
  const at = Math.min(page, pages - 1)
  const shown = found.slice(at * PER_PAGE, at * PER_PAGE + PER_PAGE)

  // The session's template: an unsent pick outranks the server's row.
  const worn =
    (pendingTemplate?.surface === kind ? pendingTemplate.id : null) ??
    sessions.find((row) => row.id === sessionId)?.renderTemplateId ??
    null

  const pick = (row: DesignTemplateRow, prompt: string) => {
    if (row.id === worn) return
    start(row, prompt)
    // Template arguments are asked in the composer, like a starting point's blanks.
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

  /** Per-card override of the template a starting point renders in. */
  const [shapeOf, setShapeOf] = useState<Record<string, string>>({})

  // Which card's procedure is unfolded; one at a time.
  const [openPrompt, setOpenPrompt] = useState<string | null>(null)
  const pickStartingPoint = (row: Sentence) => {
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
    // Apply the job's template too; a job with none lets the surface choose.
    const wanted = shapeOf[row.id] ?? row.renderTemplateId
    const shape = wanted ? rows.find((one) => one.id === wanted) : undefined
    if (shape && shape.id !== worn) start(shape, '')
    onClose()
  }

  const closeForm = () => setWriting(null)

  // Optimistic; a failure puts the row back.
  const remove = async (id: string) => {
    const held = mine
    setMine((rows) => rows.filter((r) => r.id !== id))
    try {
      await templatesApi.remove(id)
    } catch {
      setMine(held)
    }
  }

  // The form takes over the whole dialog.
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
            // Bring the saved card into view.
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

        {/* Fixed-height window so the dialog does not resize between pages.
            The scrolling element and the grid are separate so an expanded
            card can grow its row. */}
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
                  {row.shared && <Badge>{t('공용')}</Badge>}
                </p>
                {row.description && (
                  <p className="mt-0.5 text-sm text-muted">{row.description}</p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {/* Skip the group badge when it would repeat the 공용 badge. */}
                  {!(row.shared && row.group === '공용') && <Badge>{row.group}</Badge>}
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
                {(row.needs.length > 0 || row.skills.length > 0) && (
                  <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                    {row.needs.includes('web') && <span className="inline-flex items-center gap-1"><Globe size={11} />{t('웹 검색으로 찾습니다')}</span>}
                    {row.needs.includes('file') && <span className="inline-flex items-center gap-1"><Paperclip size={11} />{t('파일을 첨부해야 합니다')}</span>}
                    {row.skills.length > 0 && <span className="inline-flex items-center gap-1"><Sparkles size={11} />{row.skills.join(' · ')}</span>}
                  </p>
                )}
                {/* Controlled so the open card closes in the same paint the new one opens. */}
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
                  <span className="text-xs text-faint">{t('고르면 입력창이 묻습니다')}</span>
                </div>
              </div>
                {/* Own cards only; shared ones are edited on the admin screen. */}
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
    </Modal>
  )
}

/** Whether a surface offers the picker; one gate for the gallery and composer buttons. */
export function offersTemplates(kind: SessionKind): boolean {
  return kind === 'chat' || kind === 'report' || kind === 'slides' || kind === 'image' || kind === 'av'
}

/** Button that opens the gallery, shown where the surface has anything to offer. */
export function DesignGallery({
  kind,
  sessionId,
}: {
  kind: SessionKind
  sessionId?: string | null
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
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
