import {
  ChevronLeft,
  ChevronRight,
  Download,
  Grid2x2,
  ImagePlus,
  ListPlus,
  Loader2,
  Play,
  Presentation,
  Rows3,
  ShieldQuestion,
  StickyNote,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  PanelControls,
  usePanelWidth,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { usePanelNarrow } from '@/lib/usePanelNarrow'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DeckArtifact, LintFinding, Slide } from '@/types'
import { FactCheckResults } from '@/components/artifacts/FactCheckResults'
import { LintFindings, byWhere, fixNote } from '@/components/artifacts/LintFindings'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { useStore } from '@/store/useStore'
import { SlideChart } from '@/components/slides/SlideChart'
import { useT } from '@/lib/useT'
import { PicturePicker } from '@/components/artifacts/PicturePicker'


/**
 * Is there anything on this slide yet?
 *
 * Every field a slide can carry content in, not the two it could carry when
 * this was written. `bullets` and `body` were the whole list; a table's `rows`,
 * a strip's `metrics` and a chart's numbers live nowhere near them — so a
 * finished table slide was indistinguishable from one the model had not
 * written yet, and a deck containing one was "still being written" forever.
 * 내보내기, 발표 and 텍스트 수정 stayed disabled on a deck that was complete.
 *
 * Mirrors `deck.has_content` on the server, which drops contentless slides
 * from the stored deck and was dropping the same three layouts.
 */
export function hasContent(slide: Slide): boolean {
  if (slide.layout === 'title') return true
  return Boolean(
    slide.bullets?.length ||
      slide.body?.trim() ||
      slide.rows?.length ||
      slide.metrics?.length ||
      slide.chart,
  )
}

/**
 * The slide a finding was found on, or `undefined`.
 *
 * Matched on the title, which is all a finding carries. Exact first, then
 * ignoring whitespace — a title somebody has retyped differs from the one the
 * checks ran against by exactly that much, and refusing to fix a slide because
 * its title gained a space is a worse answer than fixing the one it obviously
 * means.
 */
function slideFor(slides: Slide[], where: string): Slide | undefined {
  if (!where) return undefined
  const exact = slides.find((s) => s.title === where)
  if (exact) return exact
  const loose = (text: string) => text.replace(/\s+/g, '')
  return slides.find((s) => loose(s.title) === loose(where))
}

/**
 * One slide, drawn in the same rectangle the exporter uses. The geometry is
 * kept in step with `deck_export.py` — a preview that differs from the .pptx
 * is discovered in the room.
 */
/**
 * One slide, drawn at whatever scale the caller has room for.
 *
 * Exported because the artifact gallery draws the first slide as a deck's
 * thumbnail — the same drawing, so a deck looks in the gallery like the deck
 * it opens as.
 */
/**
 * The fields the caller owns, when a slide is being typed over.
 *
 * A new `slide` prop arrives whenever the deck reloads, and the working copy
 * must not lose what is half-typed — but it must pick up a slide that is
 * genuinely different (somebody moved to the next one).
 */
function pick(next: Slide, working: Slide): Slide {
  return next.id === working.id ? working : next
}


export function SlideView({
  slide,
  scale = 1,
  writing = true,
  deckTitle = '',
  index,
  total,
  editable = false,
  onEdit,
}: {
  slide: Slide
  scale?: number
  /**
   * Whether the run that fills this deck is still going. Defaults to true so a
   * caller that cannot know says the softer of the two things.
   *
   * An empty slide means one of two opposite things and they must not read the
   * same. While the deck is being written it has not been reached yet, and the
   * answer is to wait. Once the run has ended it came back unusable, and
   * "쓰는 중…" on a deck that finished ten minutes ago is a screen telling
   * somebody to keep waiting for something that is never coming.
   */
  writing?: boolean
  /**
   * The deck's name and where this slide falls in it, for the footer.
   *
   * Optional because a thumbnail 400px wide draws a footer nobody can read;
   * the rail passes neither and gets a slide without one.
   */
  deckTitle?: string
  index?: number
  total?: number
  /**
   * Whether the words on this slide can be typed over.
   *
   * The panel's editor was a textarea with a syntax: first line the title, one
   * line per bullet, and `|` between cells for a table row. So somebody looking
   * at a comparison table on screen, wanting to change one cell, had to find
   * that cell inside `| 기존 | 개선 | 적용 시기 |` and count pipes. The slide
   * was right there and could not be touched.
   *
   * Edits are handed back as a whole slide, and the panel turns that back into
   * the same lines the textarea holds — so the two are one draft and `save()`
   * did not have to learn anything new.
   */
  editable?: boolean
  onEdit?: (next: Slide) => void
}) {
  const t = useT()
  /*
   * The slide as it is being typed.
   *
   * Held in a ref rather than in state: re-rendering a `contentEditable` while
   * somebody is inside it moves the caret to the front, and the browser is
   * already holding the characters. What this accumulates is the *other*
   * fields — edit the title, then a bullet, and the second edit has to carry
   * the first or it would hand back a slide with the old title.
   */
  const working = useRef(slide)
  working.current = editable ? { ...working.current, ...pick(slide, working.current) } : slide
  const edit = (patch: Partial<Slide>) => {
    working.current = { ...working.current, ...patch }
    onEdit?.(working.current)
  }
  /** What a `contentEditable` needs to be one, and nothing when it is not. */
  const typed = (read: (text: string) => Partial<Slide>) =>
    editable
      ? ({
          contentEditable: true,
          suppressContentEditableWarning: true,
          spellCheck: false,
          onBlur: (e: React.FocusEvent<HTMLElement>) =>
            edit(read(e.currentTarget.textContent ?? '')),
          className: 'outline-none focus:bg-accent-soft/40',
        } as const)
      : {}
  const accent = slide.accent ?? 'var(--accent)'
  const px = (n: number) => `${n * scale}px`
  /**
   * Type, which a person can make bigger or smaller on one slide.
   *
   * Separate from `px` on purpose: the ask was for the words, and growing the
   * padding and the gaps with them would only push the same amount of text off
   * the same edge. The gutter is the 서식's decision and stays where it is.
   *
   * `deck_export` multiplies its own sizes by the same number, so the `.pptx`
   * and the `.pdf` come out the size the screen showed. A control that only
   * changed the preview would be worse than no control.
   */
  const type = (n: number) => `${n * scale * (slide.textScale ?? 1)}px`
  /*
   * Every surface is a mix of the slide's own accent, so one deck in green and
   * one in navy are the same design rather than the same design plus a blue
   * table. `deck_export` computes the identical mixes in Python and draws them
   * into the .pptx and .pdf — see `_mix` there. Change a percentage here and
   * change it there, or the room sees a different deck from the panel.
   */
  const tint = `color-mix(in srgb, ${accent} 7%, #fff)`
  const hair = '#e6e6e6'
  const rows = slide.rows ?? []
  const metrics = slide.metrics ?? []
  const chart = slide.chart
  const pending = !hasContent(slide)
  // Two columns are only two columns when there is enough to fill them; four
  // bullets split in half reads as a mistake.
  const twoColumn = slide.layout === 'two-column' && (slide.bullets?.length ?? 0) >= 5
  /*
   * How tight the table has to be to stay on the slide.
   *
   * A slide is 225 units tall in this drawing and the body gets about 125 of
   * them. Seven rows at one comfortable size is 190, so the table ran off the
   * bottom edge and through the footer — which is exactly what a filled head
   * row makes obvious, because the overflow now has a colour. The row count is
   * known before anything is drawn, so the size follows it rather than the
   * slide losing its last row. `deck_export` scales the same way.
   */
  const dense = (() => {
    // What is left under the title once the head band, the title, the tab and
    // the foot have taken theirs, in this drawing's 225 units.
    const body = 122
    // One row in reserve for the cell that wraps to two lines — 시스템 전역
    // 또는 프로젝트 does, in a column sized for 도구.
    const perRow = body / (rows.length + 1.2)
    const size = Math.max(7.5, Math.min(12, perRow / 2.05))
    return { size, pad: Math.max(2, (perRow - size * 1.4) / 2) }
  })()

  /**
   * The cover, and every 장 that opens a section.
   *
   * Reversed out of the accent rather than set on white. A title slide has one
   * job — say what this is before anybody reads a word of it — and the deck
   * that came before this one opened on a white rectangle with a 4px stripe
   * down the edge, which is the same rectangle the seventeen slides behind it
   * were on. The block is the only thing here that is not type, and it is what
   * makes a deck look like a deck at a glance.
   */
  if (slide.layout === 'title') {
    return (
      <div
        className="relative flex size-full flex-col justify-center overflow-hidden"
        style={{ background: accent, padding: px(34) }}
      >
        <div
          style={{
            width: px(44),
            height: px(3),
            background: 'rgba(255,255,255,0.9)',
            marginBottom: px(18),
          }}
        />
        <h3
          style={{ fontSize: type(28), fontWeight: 700, lineHeight: 1.2, color: '#fff' }}
          {...typed((text) => ({ title: text }))}
        >
          {slide.title}
        </h3>
        {slide.body && (
          <p
            style={{
              fontSize: type(13),
              marginTop: px(12),
              lineHeight: 1.5,
              color: 'rgba(255,255,255,0.8)',
            }}
          >
            {slide.body}
          </p>
        )}
      </div>
    )
  }

  return (
    <div
      className="relative flex size-full flex-col overflow-hidden bg-white text-[#1a1a1a]"
      style={{
        paddingTop: px(24),
        paddingLeft: px(28),
        paddingRight: px(28),
        // Room for the footer, which is drawn against the bottom edge.
        paddingBottom: px(28),
      }}
    >
      {/* The band across the head. Where the 4px stripe down the left edge
          used to be: a rule that stands up is read as a margin mark, and one
          that lies across the top is read as the top of a slide. */}
      <div className="absolute inset-x-0 top-0" style={{ height: px(6), background: accent }} />

      {slide.layout === 'quote' && slide.body ? (
        <div className="flex flex-1 flex-col justify-center">
          <p style={{ fontSize: type(20), fontWeight: 600, lineHeight: 1.4, color: accent }}>
            “{slide.body}”
          </p>
          <p style={{ fontSize: type(12), marginTop: px(10), color: '#666' }}>{slide.title}</p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <h3
            style={{ fontSize: type(19), fontWeight: 700, lineHeight: 1.25 }}
            {...typed((text) => ({ title: text }))}
          >
            {slide.title}
          </h3>
          {/* The tab under the title. Two by twenty-six, and the only accent
              on a slide of prose — enough that the eye finds the same corner
              on every 장, not enough to compete with the words. */}
          <div
            style={{
              width: px(26),
              height: px(2),
              background: accent,
              marginTop: px(8),
              marginBottom: px(14),
            }}
          />
          {/* Words left, picture right — the geometry `deck_export` uses, so
              the preview and the .pptx put them in the same places. */}
          <div className="flex min-h-0 flex-1" style={{ gap: px(16) }}>
            <div className="flex min-w-0 flex-1 flex-col">
              {chart && <SlideChart chart={chart} accent={accent} scale={scale} />}
              {metrics.length > 0 && (
                /* One card each: the figure large, what it counts under it, and
                   a rule over the top in the accent. Set on the open slide they
                   were three numbers floating in a white field; carded, the eye
                   reads them as one row of comparable things. The same shape
                   `deck_export` draws into the .pptx and .pdf. */
                <div className="flex" style={{ gap: px(12), marginTop: px(6) }}>
                  {metrics.map(([figure, label], i) => (
                    <div
                      key={i}
                      className="min-w-0 flex-1"
                      style={{
                        background: tint,
                        borderTop: `${px(2)} solid ${accent}`,
                        padding: `${px(14)} ${px(14)} ${px(16)}`,
                      }}
                    >
                      <div
                        style={{
                          fontSize: type(30),
                          fontWeight: 700,
                          lineHeight: 1.1,
                          color: accent,
                        }}
                        {...typed((text) => ({
                          metrics: (working.current.metrics ?? []).map((m, at) =>
                            at === i ? ([text, m[1]] as [string, string]) : m,
                          ),
                        }))}
                      >
                        {figure}
                      </div>
                      <div
                        style={{ fontSize: type(11), marginTop: px(5), color: '#666' }}
                        {...typed((text) => ({
                          metrics: (working.current.metrics ?? []).map((m, at) =>
                            at === i ? ([m[0], text] as [string, string]) : m,
                          ),
                        }))}
                      >
                        {label}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {rows.length > 0 && (
                /* Clipped rather than allowed to run: a table one row too long
                   used to draw straight through the foot, and a slide whose
                   page number has a table row across it reads as a broken
                   export rather than a long table. */
                <div className="min-h-0 overflow-hidden">
                {/* The head row filled and reversed out, the body banded in the
                    faintest tint of the same accent, hairlines between and
                    nothing round the outside. A slide table is read at eight
                    metres: the head has to be a block of colour rather than
                    coloured words, and a full grid at that distance is a grey
                    blur. Kept in step with `deck_export`. */}
                <table
                  style={{
                    fontSize: type(dense.size),
                    lineHeight: 1.4,
                    width: '100%',
                    borderCollapse: 'collapse',
                    tableLayout: 'fixed',
                  }}
                >
                  <tbody>
                    {rows.map((row, r) => (
                      <tr
                        key={r}
                        style={{
                          background: r === 0 ? accent : r % 2 === 0 ? tint : 'transparent',
                          borderBottom: r === 0 ? 'none' : `1px solid ${hair}`,
                        }}
                      >
                        {row.map((cell, c) => (
                          <td
                            key={c}
                            style={{
                              padding: `${px(dense.pad)} ${px(9)}`,
                              verticalAlign: 'top',
                              wordBreak: 'keep-all',
                              fontWeight: r === 0 || c === 0 ? 600 : 400,
                              color: r === 0 ? '#fff' : '#1a1a1a',
                            }}
                            {...typed((text) => ({
                              rows: (working.current.rows ?? []).map((row2, ri) =>
                                ri === r ? row2.map((cell2, ci) => (ci === c ? text : cell2)) : row2,
                              ),
                            }))}
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
              {slide.bullets && rows.length === 0 && metrics.length === 0 && !chart && (
                <ul
                  style={{
                    fontSize: type(13),
                    lineHeight: 1.7,
                    // A long list down one edge wastes the right half of the
                    // rectangle and pushes the last item off the bottom.
                    // Splitting it is the same content, read in the shape it
                    // fits.
                    ...(twoColumn ? { columnCount: 2, columnGap: px(20) } : null),
                  }}
                >
                  {slide.bullets.map((b, i) => (
                    <li key={i} className="flex gap-2" style={{ breakInside: 'avoid' }}>
                      <span style={{ color: accent }}>•</span>
                      <span
                        {...typed((text) => ({
                          bullets: (working.current.bullets ?? []).map((old, at) =>
                            at === i ? text : old,
                          ),
                        }))}
                      >
                        {b}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {slide.body && !slide.bullets?.length && (
                <p
                  style={{ fontSize: type(12), color: '#555', marginTop: px(2), lineHeight: 1.6 }}
                  {...typed((text) => ({ body: text }))}
                >
                  {slide.body}
                </p>
              )}
              {/* 빈 장. 흰 화면만 두면 다 만들어진 것처럼 보인다 */}
              {pending && !slide.image && (
                <p style={{ fontSize: type(12), color: '#aaa', marginTop: px(6) }}>
                  {writing ? t('쓰는 중…') : t('내용이 비었습니다 — 텍스트 수정으로 채워 주세요.')}
                </p>
              )}
            </div>
            {slide.image?.src && (
              <div
                className="flex shrink-0 flex-col justify-center"
                style={{ width: pending ? '100%' : '42%' }}
              >
                <img
                  src={slide.image.src}
                  alt={slide.image.caption || t('그림')}
                  className="max-h-full w-full object-contain"
                />
                {slide.image.caption && (
                  <p style={{ fontSize: type(10), color: '#666', marginTop: px(4) }}>
                    {slide.image.caption}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* The foot. What deck this is on the left, where you are in it on the
          right — the two things somebody asks about from the floor. Drawn only
          where there is room to read it: the rail's thumbnails pass no index
          and get none. */}
      {index !== undefined && total !== undefined && (
        <div
          className="absolute flex items-center justify-between"
          style={{
            left: px(28),
            right: px(28),
            bottom: px(10),
            paddingTop: px(7),
            borderTop: `1px solid ${hair}`,
          }}
        >
          <span
            className="min-w-0 truncate"
            style={{ fontSize: type(8), letterSpacing: px(0.3), color: '#8a8a8a' }}
          >
            {deckTitle}
          </span>
          <span
            className="grid shrink-0 place-items-center"
            style={{
              minWidth: px(15),
              height: px(15),
              padding: `0 ${px(4)}`,
              background: accent,
              color: '#fff',
              fontSize: type(8),
              fontWeight: 700,
            }}
          >
            {index + 1}
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * Putting a picture on a slide of a JSON deck.
 *
 * The same path an HTML document has, on the track that never was HTML: the
 * picture was made on the image surface and the server embeds it as a `data:`
 * URI, so the deck stays one thing that previews, presents and exports with
 * the picture in it.
 */
function SlidePicture({ deck, slide }: { deck: DeckArtifact; slide: Slide }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)
  const refreshArtifact = useStore((s) => s.refreshArtifact)

  const insert = async () => {
    if (!picked) return
    setBusy(true)
    setError(null)
    try {
      await artifactsApi.addSlideImage(deck.id, slide.id, picked, caption.trim())
      await refreshArtifact(deck.id)
      await loadArtifacts()
      setOpen(false)
      setPicked(null)
      setCaption('')
    } catch (err) {
      setError(errorMessage(err, t('그림을 넣지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setPicked(null)
          setCaption('')
          setError(null)
          setOpen(true)
        }}
      >
        <ImagePlus size={13} />
        {slide.image ? t('그림 바꾸기') : t('그림 넣기')}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('{name}에 그림 넣기').replace('{name}', slide.title || t('이 장'))}
        description={t('여기서 바로 만들거나 이미 만든 그림을 고를 수 있습니다. 링크가 아니라 파일 안에 담기므로 인쇄와 공유에서도 함께 보입니다.')}
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>
              {t('취소')}
            </Button>
            <Button variant="primary" onClick={() => void insert()} disabled={busy || !picked}>
              {busy ? t('넣는 중…') : t('넣기')}
            </Button>
          </>
        }
      >
        <PicturePicker
          sessionId={deck.sessionId}
          aspect="16:9"
          picked={picked}
          onPick={setPicked}
          caption={caption}
          onCaption={setCaption}
          about={slide.title || t('이 장')}
          title={deck.title}
          /* What this 장 already says, so the suggestion draws what the words
             cannot rather than illustrating them a second time. */
          context={[
            slide.body,
            ...(slide.bullets ?? []),
            ...(slide.rows ?? []).map((row) => row.join(' · ')),
            ...(slide.metrics ?? []).map(([value, label]) => `${value} — ${label}`),
          ]
            .filter(Boolean)
            .join('\n')}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * The editable text of a slide: the title, then whatever the slide is made of.
 *
 * A table row comes out as `| 구분 | 탐지 |`, the shape anybody who has written
 * Markdown already knows, and goes back in the same way. Before this, a slide's
 * table was simply absent from the box: somebody opened 텍스트 수정 on a table
 * slide, saw a title and nothing else, typed the lines they wanted, and saved.
 * The save turned the slide into `bullets` — and `SlideView` draws bullets only
 * where `rows` is empty, so the table stayed and every word they typed was
 * swallowed with no error and no trace.
 *
 * Metrics go out the same way, as `| 99.5% | 대응률 |`, so a KPI strip is
 * editable for the first time as well.
 */
function toLines(slide: Slide): string {
  const rows = (slide.rows ?? []).map((row) => `| ${row.join(' | ')} |`)
  const metrics = (slide.metrics ?? []).map(([figure, label]) => `| ${figure} | ${label} |`)
  return [
    slide.title,
    ...(slide.bullets ?? []),
    slide.body ?? '',
    ...rows,
    ...metrics,
  ]
    .filter(Boolean)
    .join('\n')
}

/** `| a | b |` → `['a', 'b']`, or `null` for a line that is not a row. */
function toCells(line: string): string[] | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith('|')) return null
  const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
  // `| --- | --- |` is a Markdown rule, not data. Somebody pasting a table
  // from elsewhere brings one with them.
  if (cells.every((c) => /^:?-{2,}:?$/.test(c))) return null
  return cells.length > 1 ? cells : null
}

/**
 * The room a deck is shown in: black, full-screen, and holding the keyboard.
 *
 * Shared with the HTML deck panel, where a deck is markup rather than slide
 * objects. What stands on the stage differs; the counter, the keys and the way
 * out are the same job.
 */
export function PresentStage({
  title,
  index,
  count,
  onIndex,
  onClose,
  notes,
  outline,
  children,
}: {
  title: string
  index: number
  count: number
  onIndex: (i: number) => void
  onClose: () => void
  /** The presenter's own screen. Left out by a deck that carries no notes. */
  notes?: ReactNode
  /** Slide titles, so a long deck can be jumped through rather than stepped. */
  outline?: string[]
  children: ReactNode
}) {
  const t = useT()
  const [showNotes, setShowNotes] = useState(true)
  const [showList, setShowList] = useState(false)

  /**
   * Keyboard, owned while presenting. Capture phase and stopped here: an
   * Escape left to bubble would also close the dialog the deck opened from.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const keys = ['Escape', 'ArrowRight', 'ArrowLeft', ' ', 'n', 'N']
      if (!keys.includes(e.key)) return
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight' || e.key === ' ') onIndex(Math.min(index + 1, count - 1))
      if (e.key === 'ArrowLeft') onIndex(Math.max(index - 1, 0))
      if (e.key.toLowerCase() === 'n') setShowNotes((s) => !s)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [index, count, onIndex, onClose])

  /* Portalled to the body rather than left where the panel sits. The deck can
     be opened inside a dialog, and an animated ancestor makes `fixed` resolve
     against *that box* — which turns full-screen rehearsal into a slide shown
     in a 500px window. */
  return createPortal(
    <div role="dialog" aria-label={t('발표 모드')} className="fixed inset-0 z-50 flex flex-col bg-black">
      <div className="flex items-center gap-2 px-4 py-2 text-white/70">
        <Presentation size={14} />
        <span className="text-base">{title}</span>
        <span className="ml-auto text-base tabular-nums">
          {index + 1} / {count}
        </span>
        {outline && (
          <button
            onClick={() => setShowList((s) => !s)}
            aria-pressed={showList}
            className="rounded-control px-2 py-1 text-sm transition-colors hover:bg-white/10"
          >
            {t('장 목록')}
          </button>
        )}
        {notes !== undefined && (
          <button
            onClick={() => setShowNotes((s) => !s)}
            className="rounded-control px-2 py-1 text-sm transition-colors hover:bg-white/10"
          >
            {t('노트')} (N)
          </button>
        )}
        <button
          onClick={onClose}
          aria-label={t('발표 끝내기')}
          className="rounded-control p-1.5 transition-colors hover:bg-white/10"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex min-h-0 flex-1">
        {/* 어디까지 왔고 다음이 무엇인지. 스무 장짜리 덱을 한 장씩 넘겨
            찾는 것은 방에 사람이 앉아 있을 때 할 일이 아니다. */}
        {outline && showList && (
          <nav
            aria-label={t('장 목록')}
            className="w-52 shrink-0 overflow-y-auto border-r border-white/10 py-2"
          >
            {outline.map((name, i) => (
              <button
                key={i}
                onClick={() => onIndex(i)}
                aria-current={i === index}
                aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
                className={cn(
                  'flex w-full items-start gap-2 px-3 py-1.5 text-left text-sm leading-snug transition-colors',
                  i === index
                    ? 'bg-white/15 text-white'
                    : 'text-white/55 hover:bg-white/10 hover:text-white',
                )}
              >
                <span className="shrink-0 text-white/40 tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1 line-clamp-2">{name || t('제목 없음')}</span>
              </button>
            ))}
          </nav>
        )}
        <div className="flex min-h-0 flex-1 items-center justify-center px-6 pb-4">{children}</div>
      </div>
      {notes !== undefined && showNotes && (
        <div className="max-h-40 overflow-y-auto border-t border-white/10 px-6 py-3 text-base leading-relaxed text-white/75">
          {notes}
        </div>
      )}
      <div className="flex items-center justify-center gap-2 pb-4 text-white/70">
        <button
          onClick={() => onIndex(Math.max(index - 1, 0))}
          disabled={index === 0}
          aria-label={t('이전 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronLeft size={18} />
        </button>
        <span className="text-sm">{t('← → 로 넘기고 Esc 로 끝냅니다')}</span>
        <button
          onClick={() => onIndex(Math.min(index + 1, count - 1))}
          disabled={index >= count - 1}
          aria-label={t('다음 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>,
    document.body,
  )
}

/**
 * Full-screen rehearsal. A deck is checked by walking it at the size it will
 * be shown at — text too small to read from the back of the room is legible
 * in a 400px thumbnail.
 */
function PresentMode({
  deck,
  index,
  onIndex,
  onClose,
}: {
  deck: DeckArtifact
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const t = useT()
  // Measured, not chosen. `SlideView` sizes every rule, every gap and every
  // type size as `n * scale`, so the drawing only looks like itself when
  // `scale` is the stage's own width over 400 — the relationship
  // `useStageScale` exists to keep. This screen had a hard 2.4 instead, tuned
  // for some window nobody wrote down, and on a 1152px stage the right answer
  // is 2.88: every word, the accent bar and all the padding came out 17%
  // small. Presenting a deck and reading it in the panel showed two different
  // designs of the same slide, which also meant rehearsing at a size the room
  // would never see.
  const stage = useStageScale()
  const slide = deck.slides[index]
  if (!slide) return null
  return (
    // `outline` is what draws the slide list in the presentation header. The
    // HTML artifact panel has always passed it and this one never did, so the
    // same deck presented from here had no way to jump to a slide — the one
    // thing a presenter reaches for when a question comes from the floor.
    <PresentStage
      title={deck.title}
      index={index}
      count={deck.slides.length}
      onIndex={onIndex}
      onClose={onClose}
            outline={deck.slides.map((s) => s.title)}
      notes={slide.notes || <span className="text-white/35">{t('노트 없음')}</span>}
    >
      <div
        ref={stage.ref}
        className="aspect-video max-h-full w-full max-w-6xl overflow-hidden rounded-control shadow-float"
      >
        <SlideView
          slide={slide}
          scale={stage.scale}
          writing={false}
          deckTitle={deck.title}
          index={index}
          total={deck.slides.length}
        />
      </div>
    </PresentStage>
  )
}

/**
 * Stage width → `scale`. `SlideView` sizes everything off it, so a fixed value
 * tuned for a 460px desktop stage overflows a 210px phone one and the preview
 * stops matching the `.pptx`.
 */
function useStageScale() {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1.15)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      if (width > 0) setScale(Math.max(0.45, width / 400))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return { ref, scale }
}

export function DeckPanel({
  deck,
  onClose,
  onModeChange,
}: {
  deck: DeckArtifact
  onClose?: () => void
  /** Fires when the reader asks for room. A deck is checked by looking at it,
   *  and the stage beside a transcript is about 330px wide. */
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)

  /**
   * One finding from the checks, fixed.
   *
   * Rewrites the slide it was found on, through the endpoint that mirrors the
   * report's — so the deck changes, a snapshot is kept, and a rewrite that
   * reads worse is one press of 되돌리기 from undone.
   *
   * The deck could not do this until now. `deck.rewrite_slide` existed and was
   * reachable only by asking in the conversation, which is a request rather
   * than an action: the deck does not change, and the reader has to watch the
   * transcript and work out for themselves whether anything happened.
   *
   * A finding about the deck as a whole names no slide and has nowhere to go,
   * so it keeps the button hidden rather than pretending.
   */
  const fixFinding = async (finding: LintFinding) => {
    const slide = slideFor(deck.slides, finding.where)
    if (!slide) throw new Error(t('어느 장을 고쳐야 하는지 알 수 없습니다.'))
    const row = await artifactsApi.rewriteSlide(
      deck.id,
      slide.id,
      t('검사에서 지적된 문제를 고쳐 주세요: {message}').replace('{message}', finding.message),
    )
    const data = (row.data ?? {}) as { slides?: Slide[] }
    // Written onto the object this panel holds as well as into the store — the
    // artifacts screen opens its modal on a copy it took when the card was
    // clicked, so a store refresh alone leaves the new slide invisible exactly
    // where it was asked for.
    if (data.slides) deck.slides = data.slides
    deck.version = row.version
  }
  /**
   * Every finding at once, one rewrite per slide.
   *
   * Not a loop over `fixFinding`: two findings about one slide would be two
   * rewrites of it, and the second lands on what the first produced — asked to
   * fix a line that is no longer there, it writes the first fix back out.
   * Grouped, a slide is rewritten once and told everything found in it.
   *
   * One after another, not together: the slides share a deck and a version, so
   * two rewrites in flight means the second saves over the first.
   */
  const fixAllFindings = async (findings: LintFinding[]) => {
    const failed: string[] = []
    for (const [where, group] of byWhere(findings)) {
      const slide = where ? slideFor(deck.slides, where) : undefined
      if (!slide) {
        // A deck has no conversation path of its own for a finding about the
        // whole thing, so it is named rather than silently dropped.
        failed.push(where || t('덱 전체'))
        continue
      }
      try {
        const row = await artifactsApi.rewriteSlide(
          deck.id,
          slide.id,
          fixNote(
            group,
            t('검사에서 지적된 문제를 고쳐 주세요: {message}'),
            t('검사에서 지적된 문제를 모두 고쳐 주세요:\n{list}'),
          ),
        )
        const data = (row.data ?? {}) as { slides?: Slide[] }
        if (data.slides) deck.slides = data.slides
        deck.version = row.version
      } catch {
        failed.push(where)
      }
    }
    if (failed.length > 0) {
      throw new Error(
        t('고치지 못한 장이 있습니다: {list}').replace('{list}', failed.join(', ')),
      )
    }
  }

  const panel = usePanelNarrow<HTMLDivElement>()
  const stage = useStageScale()
  const [selected, setSelected] = useState(0)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [presenting, setPresenting] = useState(false)
  //: The rail shows the deck either as pictures or as an outline. Both answer
  //: different questions — "which slide was the chart on" and "does the
  //: argument run in the right order".
  const [rail, setRail] = useState<'thumbs' | 'outline'>('thumbs')
  //: In a panel narrower than the deck asks for, the rail becomes a drawer,
  //: the same way the report's contents do. Beside the stage it is 132px of a
  //: 390px panel, which leaves the slide 119px — a picture of a slide rather
  //: than the slide.
  const [railOpen, setRailOpen] = useState(false)

  const runFactCheck = async (slideId: string) => {
    setChecking(true)
    setError(null)
    try {
      const row = await artifactsApi.factcheckSlide(deck.id, slideId)
      const next = (row.data as { slides?: Slide[] } | null)?.slides
      if (next) deck.slides = next
      deck.version = row.version
    } catch (err) {
      setError(errorMessage(err, t('확인하지 못했습니다.')))
    } finally {
      setChecking(false)
    }
  }
  // Slides arrive one at a time, so the selection can point past the end.
  const index = Math.min(selected, Math.max(deck.slides.length - 1, 0))
  const slide = deck.slides[index] as Slide | undefined
  const weakSlides = deck.slides
    .map((s, i) => (s.factCheck?.claims.some((c) => c.verdict !== 'supported') ? i : -1))
    .filter((i) => i >= 0)
  // Still being written, which is the only thing these controls need to wait
  // for: export would 404 on a document the server does not have yet, and an
  // edit would be overwritten by the next slide event of a run still going.
  //
  // This asked whether every slide had content, which answers the same
  // question almost always and answers it wrong in the one case that matters.
  // A slide whose model call came back unusable stays empty — the writer falls
  // back to bullets and salvages what it can, and sometimes there is nothing
  // to salvage — and the deck was then locked for good: no export, no
  // 발표, and no 텍스트 수정, which is the control that exists to fix exactly
  // this. The deck had finished writing; only its result was disappointing,
  // and a disappointing result is the reader's to repair.
  const writing = deck.draft === true || deck.slides.length === 0

  useEffect(() => {
    if (writing) setEditing(false)
  }, [writing])

  //: The deck as it stood when this edit began, for the same comparison the
  //: report panel makes.
  const baseline = useRef('')

  /**
   * Refetch before editing. The panel opens ahead of the store's refresh, and
   * an editor opened in that gap would baseline on older text and save into a
   * phantom conflict.
   */
  const startEditing = async () => {
    if (!slide) return
    setError(null)
    const latest = await artifactsApi.get(deck.id).catch(() => null)
    const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
    if (latest && onServer) {
      deck.slides = onServer
      deck.version = latest.version
    }
    const current = deck.slides[index]
    if (!current) return
    baseline.current = JSON.stringify(deck.slides)
    setDraft(toLines(current))
    setNotes(current.notes ?? '')
    setEditing(true)
  }

  const save = async () => {
    if (!slide) return
    const lines = draft
      .split('\n')
      .map((l) => l.replace(/^\s*[-*•]\s*/, '').trim())
      .filter(Boolean)
    if (lines.length === 0) {
      setError(t('내용이 비어 있습니다. 저장하지 않았습니다.'))
      return
    }

    const [title, ...rest] = lines
    const table = rest.map(toCells).filter(Boolean) as string[][]
    const words = rest.filter((line) => toCells(line) === null)

    // Layout follows the shape that arrived: on a quote slide one line is a
    // quotation and several are bullets, since quote renders only the first.
    //
    // The rows a person typed replace the rows that were there, and typing none
    // on a slide that had a table removes it. Both are the same rule — what is
    // in the box is what the slide becomes — and it is the rule that was
    // missing: the table used to survive an edit that never mentioned it and
    // then hide the bullets that did get typed.
    const shaped: Slide =
      table.length > 0
        ? // `metrics` is a table of exactly two columns whose left side is a
          // figure. Kept as metrics only if that is what the slide already was;
          // otherwise two columns are two columns.
          slide.metrics?.length && table.every((row) => row.length === 2)
          ? { ...slide, metrics: table.map(([f, l]) => [f, l] as [string, string]), rows: undefined }
          : { ...slide, layout: 'table', rows: table, metrics: undefined }
        : { ...slide, rows: undefined, metrics: undefined }

    const edited: Slide =
      slide.layout === 'quote' && words.length <= 1
        ? { ...shaped, title, body: words[0] ?? '', bullets: undefined, notes }
        : slide.layout === 'title'
          ? { ...shaped, title, body: words.join(' '), notes }
          : table.length > 0
            ? { ...shaped, title, bullets: words.length ? words : undefined, body: undefined, notes }
            : { ...shaped, layout: 'bullets', title, bullets: words, body: undefined, notes }

    const slides = deck.slides.map((s, i) => (i === index ? edited : s))
    setSaving(true)
    setError(null)
    try {
      // Same check the report panel makes, and for the same reason: this PATCH
      // carries every slide, so saving over somebody else's edit throws their
      // work away silently. Compared by content — a version number from a
      // list fetched minutes ago says nothing about who edited what.
      const latest = await artifactsApi.get(deck.id).catch(() => null)
      const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
      if (onServer && JSON.stringify(onServer) !== baseline.current) {
        setError(
          t('이 덱은 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      // PATCHing `data` as one deck is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      const row = await artifactsApi.update(deck.id, {
        data: { kind: 'deck', theme: deck.theme, slides },
        summary: t('{n}장 편집').replace('{n}', String(index + 1)),
        // The version the check above read. See ReportPanel for why.
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = slides
      // Kept in step with the server, or the next save on this panel sends a
      // version that is one behind and is refused as somebody else's edit.
      deck.version = row.version
      baseline.current = JSON.stringify(slides)
      setEditing(false)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  /**
   * Adding, removing and reordering slides.
   *
   * A deck arrives with the shape the model chose and there was no way to
   * change it: not one control on either surface added a slide, removed one, or
   * moved one. Everything a person could do to the structure of a document they
   * had to do by asking for the whole thing again, which throws away every edit
   * they had made to the slides they were keeping.
   *
   * Saved through the same door `save` uses — the whole deck as one PATCH,
   * checked against the server first — so a structural edit is snapshotted and
   * one click from undone like any other.
   */
  const restructure = async (next: Slide[], summary: string, land: number) => {
    setSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id).catch(() => null)
      const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
      if (onServer && JSON.stringify(onServer) !== baseline.current) {
        setError(
          t('이 덱은 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      const row = await artifactsApi.update(deck.id, {
        data: { kind: 'deck', theme: deck.theme, slides: next },
        summary,
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = next
      deck.version = row.version
      baseline.current = JSON.stringify(next)
      setEditing(false)
      // Follow the slide, not the number. After a move the thing somebody was
      // looking at is somewhere else, and a panel that stayed on the index
      // would show them a different slide as though nothing had happened.
      setSelected(Math.max(0, Math.min(land, next.length - 1)))
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  const addSlide = (after: boolean) => {
    const at = after ? index + 1 : index
    const blank: Slide = {
      id: `sl${Date.now().toString(36)}`,
      layout: 'bullets',
      title: t('새 장'),
      bullets: [],
      notes: '',
    }
    const next = [...deck.slides.slice(0, at), blank, ...deck.slides.slice(at)]
    void restructure(next, t('{n}장 추가').replace('{n}', String(at + 1)), at)
  }

  const moveSlide = (by: -1 | 1) => {
    const to = index + by
    if (to < 0 || to >= deck.slides.length) return
    const next = [...deck.slides]
    ;[next[index], next[to]] = [next[to], next[index]]
    void restructure(next, t('{n}장 옮김').replace('{n}', String(index + 1)), to)
  }

  /** 크게 / 보통 / 작게, on this slide only. */
  const setTextScale = (value: number) => {
    const next = deck.slides.map((row, i) =>
      i === index ? { ...row, textScale: value === 1 ? undefined : value } : row,
    )
    void restructure(next, t('{n}장 글자 크기').replace('{n}', String(index + 1)), index)
  }

  const removeSlide = () => {
    // The last one is not removable: a deck of no slides has nothing to open,
    // nothing to present and nothing to export, and the way to get rid of it is
    // to delete the deck.
    if (deck.slides.length <= 1) {
      setError(t('마지막 한 장은 지울 수 없습니다. 덱 자체를 지우려면 결과물 목록에서 지우세요.'))
      return
    }
    const next = deck.slides.filter((_, i) => i !== index)
    void restructure(next, t('{n}장 지움').replace('{n}', String(index + 1)), Math.max(0, index - 1))
  }

  const go = (i: number) => {
    setSelected(Math.max(0, Math.min(i, deck.slides.length - 1)))
    setEditing(false)
    // Picking one is the end of the errand: what you wanted to see is the
    // stage the drawer is covering.
    setRailOpen(false)
  }

  return (
    <div ref={panel.ref} className="flex h-full min-h-0 flex-col">
      {/* 접히는 머리말. 390px 에서는 이 줄이 화면보다 넓고, flex 는 그럴 때
          자식을 줄여서 "내보내기" 를 한 자씩 네 줄로 세운다. 제목은 줄어들되
          버튼은 줄어들지 않는 것이 옳은 순서다. */}
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <Presentation size={15} className="shrink-0 text-accent" />
        <p className="min-w-0 flex-1 truncate text-base font-medium max-sm:basis-full">
          {deck.title}
        </p>
        {/* 장수와 테마는 참고 정보다. 좁으면 먼저 접는다. */}
        <Badge className="max-sm:hidden">
          {t('{n}장').replace('{n}', String(deck.slides.length))}
        </Badge>
        {/* 장마다 눌러 보지 않아도 확인이 필요한 곳이 몇 군데인지 보이게 한다 */}
        {weakSlides.length > 0 && (
          <button
            onClick={() => go(weakSlides[0])}
            title={t('{list}번 장').replace('{list}', weakSlides.map((i) => i + 1).join(', '))}
          >
            <Badge tone="warn">
              <TriangleAlert size={10} />
              {t('확인 필요 {n}장').replace('{n}', String(weakSlides.length))}
            </Badge>
          </button>
        )}
        <Badge className="max-sm:hidden">{deck.theme}</Badge>
        <LintFindings
          findings={deck.lint}
          artifact={deck}
          onFix={fixFinding}
          onFixAll={fixAllFindings}
        />
        {/* Only where there is a drawer to open: with the rail standing beside
            the stage this button opens what is already on screen. */}
        {panel.narrow && (
          <Button
            size="sm"
            aria-label={t('장 목록')}
            title={t('장 목록을 엽니다')}
            onClick={() => setRailOpen((o) => !o)}
          >
            <Rows3 size={13} />
            {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
          </Button>
        )}
        {/* 발표 모드. 덱은 방에서 보이는 크기로 한 번 넘겨 봐야 끝난다 */}
        <Button size="sm" disabled={writing} onClick={() => setPresenting(true)}>
          <Play size={13} />
          {t('발표')}
        </Button>
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={writing}>
              <Download size={14} />
              {t('내보내기')}
            </Button>
          )}
        >
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          <MenuItem hint="PPTX" onClick={() => void download(deck.id, 'pptx', deck.title)}>
            PowerPoint
          </MenuItem>
          <MenuItem hint="PDF" onClick={() => void download(deck.id, 'pdf', deck.title)}>
            {t('PDF (발표용)')}
          </MenuItem>
          <MenuItem hint="MD" onClick={() => void download(deck.id, 'md', deck.title)}>
            {t('텍스트 (노트 포함)')}
          </MenuItem>
        </Dropdown>
        {/* 저장 시점. 한 장을 고쳐 놓고 원래가 나았다는 것은 고친 뒤에야
            알게 되고, 그때 되돌릴 곳이 이 줄 말고는 없다. */}
        <VersionHistory
          artifact={deck}
          // 되돌린 덱의 몇째 장인지는 편집기가 열릴 때 잡아 둔 것과 다르다.
          // 열어 둔 초안을 그대로 저장하면 방금 되돌린 장을 덮어쓴다.
          onRestored={() => setEditing(false)}
        />
        <PanelControls mode={width.mode} onCycle={width.cycle} onClose={onClose} />
      </header>

      <div className="relative flex min-h-0 flex-1">
        {panel.narrow && railOpen && (
          <button
            aria-label={t('장 목록 닫기')}
            className="absolute inset-0 z-10 bg-black/30"
            onClick={() => setRailOpen(false)}
          />
        )}
        {/* ── 장 목록 레일 ───────────────────────────────────────────────
            Slides are ordered argument, and the order is the thing under
            revision for as long as the deck exists. A rail keeps the whole
            sequence beside the slide being worked on rather than below it. */}
        <nav
          className={cn(
            'w-[132px] shrink-0 flex-col border-r border-line bg-sidebar/40',
            panel.narrow
              ? railOpen
                ? 'absolute inset-y-0 left-0 z-20 flex shadow-overlay'
                : 'hidden'
              : 'flex',
          )}
        >
          <div className="flex items-center gap-0.5 border-b border-line px-1.5 py-1.5">
            {(
              [
                { id: 'thumbs', icon: Grid2x2, label: '그림으로' },
                { id: 'outline', icon: Rows3, label: '차례로' },
              ] as const
            ).map((v) => (
              <button
                key={v.id}
                onClick={() => setRail(v.id)}
                aria-pressed={rail === v.id}
                aria-label={t(v.label)}
                title={t(v.label)}
                className={cn(
                  'grid size-6 place-items-center rounded-control transition-colors',
                  rail === v.id ? 'bg-elevated text-fg' : 'text-faint hover:text-fg',
                )}
              >
                <v.icon size={13} />
              </button>
            ))}
            <span className="ml-auto pr-1 text-xs text-faint tabular-nums">
              {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1.5">
            {deck.slides.map((s, i) => {
              const weak = s.factCheck?.claims.some((c) => c.verdict !== 'supported')
              return rail === 'thumbs' ? (
                <button
                  key={s.id}
                  onClick={() => go(i)}
                  aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
                  aria-current={i === index}
                  className={cn(
                    'relative block aspect-video w-full overflow-hidden rounded-control border-2 bg-white transition-colors',
                    i === index ? 'border-accent' : 'border-line hover:border-line-strong',
                  )}
                >
                  <SlideView slide={s} scale={0.3} writing={writing} />
                  <span className="absolute bottom-0.5 left-0.5 rounded bg-black/55 px-1 text-2xs font-medium text-white tabular-nums">
                    {i + 1}
                  </span>
                  {weak && (
                    <span className="absolute top-0.5 right-0.5 grid size-3.5 place-items-center rounded-full bg-warn text-white">
                      <TriangleAlert size={9} />
                    </span>
                  )}
                </button>
              ) : (
                <button
                  key={s.id}
                  onClick={() => go(i)}
                  aria-current={i === index}
                  className={cn(
                    'flex w-full items-start gap-1.5 rounded-control px-1.5 py-1 text-left text-xs leading-snug transition-colors',
                    i === index ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
                  )}
                >
                  <span className="shrink-0 text-faint tabular-nums">{i + 1}</span>
                  <span className="min-w-0 flex-1 line-clamp-2">{s.title}</span>
                  {weak && <TriangleAlert size={10} className="mt-0.5 shrink-0 text-warn" />}
                </button>
              )
            })}
          </div>
        </nav>

        {/* ── 무대 ─────────────────────────────────────────────────────── */}
        {/* The slide takes the room, the notes take the rest.
            Both used to sit in one band capped at `max-w-lg` — 32rem — so on a
            940px panel the slide was drawn a third of the width it had and
            everything below the notes was empty. That cap made sense when this
            lived beside a transcript in a 330px column; it stopped making sense
            the moment the panel could be widened, and nothing followed.
            A column instead: the slide keeps its 16:9 across the full width,
            and the notes are their own band under a rule, growing into whatever
            height is left rather than trailing the slide as a caption. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <div className="flex shrink-0 items-center gap-2 p-4">
              <button
                onClick={() => go(index - 1)}
                disabled={index === 0}
                aria-label={t('이전 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronLeft size={16} />
              </button>
              <div
                ref={stage.ref}
                className="aspect-video min-w-0 flex-1 overflow-hidden rounded-card border border-line shadow-raised"
              >
                {slide ? (
                  <SlideView
                    slide={slide}
                    scale={stage.scale}
                    writing={writing}
                    deckTitle={deck.title}
                    index={index}
                    total={deck.slides.length}
                    /* 텍스트 수정을 누른 동안에는 슬라이드가 곧 편집기다.
                       고친 것은 아래 상자와 같은 초안으로 흘러가므로 저장은
                       한 곳에서만 일어난다. */
                    editable={editing}
                    onEdit={(next) => setDraft(toLines(next))}
                  />
                ) : (
                  <div className="grid size-full place-items-center bg-white text-base text-[#999]">
                    {t('구성을 잡는 중…')}
                  </div>
                )}
              </div>
              <button
                onClick={() => go(index + 1)}
                disabled={index >= deck.slides.length - 1}
                aria-label={t('다음 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronRight size={16} />
              </button>
          </div>

          {slide && (
            <div className="flex min-h-40 flex-1 flex-col border-t border-line bg-elevated/40 p-4">
              <div className="flex items-center gap-2">
                  <StickyNote size={13} className="shrink-0 text-faint" />
                  <span className="flex-1 text-xs font-semibold tracking-wide text-faint uppercase">
                    {t('발표 노트')}
                  </span>
                  {!editing && (
                    <Dropdown
                      align="right"
                      trigger={() => (
                        <Button variant="ghost" size="sm" disabled={writing || saving}>
                          <ListPlus size={13} />
                          {t('장 편집')}
                        </Button>
                      )}
                    >
                      <MenuLabel>{t('{n}번 장').replace('{n}', String(index + 1))}</MenuLabel>
                      <MenuItem onClick={() => addSlide(false)}>{t('앞에 장 추가')}</MenuItem>
                      <MenuItem onClick={() => addSlide(true)}>{t('뒤에 장 추가')}</MenuItem>
                      <MenuItem onClick={() => moveSlide(-1)} disabled={index === 0}>
                        {t('위로 옮기기')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => moveSlide(1)}
                        disabled={index >= deck.slides.length - 1}
                      >
                        {t('아래로 옮기기')}
                      </MenuItem>
                      <MenuItem onClick={removeSlide}>{t('이 장 지우기')}</MenuItem>
                      <MenuLabel>{t('글자 크기')}</MenuLabel>
                      <MenuItem
                        onClick={() => setTextScale(1.25)}
                        hint={(slide.textScale ?? 1) > 1 ? '✓' : undefined}
                      >
                        {t('크게')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => setTextScale(1)}
                        hint={(slide.textScale ?? 1) === 1 ? '✓' : undefined}
                      >
                        {t('보통')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => setTextScale(0.8)}
                        hint={(slide.textScale ?? 1) < 1 ? '✓' : undefined}
                      >
                        {t('작게')}
                      </MenuItem>
                    </Dropdown>
                  )}
                  {!editing && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={writing || checking}
                      onClick={() => void runFactCheck(slide.id)}
                    >
                      {checking ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <ShieldQuestion size={13} />
                      )}
                      {t('팩트체크')}
                    </Button>
                  )}
                  {editing ? (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                        {t('취소')}
                      </Button>
                      <Button variant="primary" size="sm" onClick={() => void save()} disabled={saving}>
                        {saving && <Loader2 size={13} className="animate-spin" />}
                        {t('저장')}
                      </Button>
                    </>
                  ) : (
                    <>
                      <SlidePicture deck={deck} slide={slide} />
                      <Button variant="ghost" size="sm" onClick={() => void startEditing()} disabled={writing}>
                        {t('텍스트 수정')}
                      </Button>
                    </>
                  )}
                </div>

                {editing ? (
                  <div className="mt-2 space-y-2">
                    <Textarea
                      rows={5}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      aria-label={t('슬라이드 텍스트')}
                    />
                    <p className="text-xs text-faint">
                      {t('위 슬라이드에서 글자를 눌러 바로 고칠 수 있습니다. 아래 상자는 한 번에 훑어 고칠 때 씁니다 — 첫 줄이 제목, 나머지 줄이 각각 한 항목이고, | 로 나눈 줄은 표의 한 행입니다.')}
                    </p>
                    <Textarea
                      rows={3}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder={t('발표 노트')}
                      aria-label={t('발표 노트')}
                    />
                    {error && <p className="text-sm text-danger">{error}</p>}
                  </div>
                ) : (
                  /* The band owns the rest of the panel, so the note scrolls
                     inside it rather than pushing the slide off the top. */
                  <div className="mt-1.5 min-h-0 flex-1 overflow-y-auto">
                    <p className="text-base text-muted">
                      {slide.notes || <span className="text-faint">{t('노트 없음')}</span>}
                    </p>
                    {slide.factCheck?.status === 'done' && (
                      <FactCheckResults check={slide.factCheck} />
                    )}
                    {error && !editing && <p className="mt-2 text-sm text-danger">{error}</p>}
                  </div>
                )}
            </div>
          )}
        </div>
      </div>

      {presenting && (
        <PresentMode
          deck={deck}
          index={index}
          onIndex={setSelected}
          onClose={() => setPresenting(false)}
        />
      )}
    </div>
  )
}
