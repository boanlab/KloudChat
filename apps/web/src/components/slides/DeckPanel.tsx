import {
  BadgeCheck,
  CircleHelp,
  Download,
  ExternalLink,
  Loader2,
  Presentation,
  ShieldQuestion,
  StickyNote,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DeckArtifact, FactCheck, Slide } from '@/types'
import { useT } from '@/lib/useT'

const verdictMeta = {
  supported: { icon: BadgeCheck, tone: 'success' as const, label: '근거 있음', color: 'text-success' },
  unsupported: { icon: TriangleAlert, tone: 'danger' as const, label: '근거 없음', color: 'text-danger' },
  uncertain: { icon: CircleHelp, tone: 'warn' as const, label: '확인 필요', color: 'text-warn' },
}

/**
 * Per-claim verdicts with the source behind each one. The server refuses to
 * issue a confident verdict without a source; this is where it is shown.
 */
function FactCheckResults({ check }: { check: FactCheck }) {
  const t = useT()
  if (check.claims.length === 0) {
    return (
      <p className="mt-3 rounded-xl border border-line bg-panel p-3 text-[12px] text-muted">
        {t('검색으로 확인할 수 있는 주장이 이 장에는 없습니다. 의견과 정의는 판정하지 않습니다.')}
      </p>
    )
  }
  const weak = check.claims.filter((c) => c.verdict !== 'supported').length
  return (
    <div className="mt-3 rounded-xl border border-line bg-panel p-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldQuestion size={13} className="shrink-0 text-accent" />
        <span className="text-[11px] font-semibold tracking-wide text-faint uppercase">{t('팩트체크')}</span>
        <Badge tone={weak > 0 ? 'warn' : 'success'}>
          {weak > 0 ? t('확인 필요 {n}').replace('{n}', String(weak)) : t('전부 근거 있음')}
        </Badge>
      </div>
      <div className="space-y-2.5">
        {check.claims.map((c) => {
          const meta = verdictMeta[c.verdict]
          const Icon = meta.icon
          return (
            <div key={c.id} className="flex items-start gap-2 text-[12px]">
              <Icon size={13} className={cn('mt-0.5 shrink-0', meta.color)} />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{c.text}</p>
                <p className="mt-0.5 text-muted">{c.note}</p>
                {c.sourceUrl && (
                  <a
                    href={c.sourceUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                  >
                    <ExternalLink size={10} />
                    {t('근거 열기')}
                  </a>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * One slide, drawn in the same rectangle the exporter uses. The geometry is
 * kept in step with `deck_export.py` — a preview that differs from the .pptx
 * is discovered in the room.
 */
function SlideView({ slide, scale = 1 }: { slide: Slide; scale?: number }) {
  const t = useT()
  const accent = slide.accent ?? 'var(--accent)'
  const px = (n: number) => `${n * scale}px`
  const pending = !slide.bullets?.length && !slide.body

  return (
    <div
      className="relative flex size-full flex-col overflow-hidden bg-white text-[#1a1a1a]"
      style={{ padding: px(28) }}
    >
      <div className="absolute top-0 left-0 h-full" style={{ width: px(4), background: accent }} />
      {slide.layout === 'title' ? (
        <div className="flex flex-1 flex-col justify-center" style={{ paddingLeft: px(16) }}>
          <h3 style={{ fontSize: px(28), fontWeight: 700, lineHeight: 1.2 }}>{slide.title}</h3>
          {slide.body && (
            <p style={{ fontSize: px(13), marginTop: px(12), color: '#666' }}>{slide.body}</p>
          )}
        </div>
      ) : slide.layout === 'quote' && slide.body ? (
        <div className="flex flex-1 flex-col justify-center" style={{ paddingLeft: px(16) }}>
          <p style={{ fontSize: px(20), fontWeight: 600, lineHeight: 1.4, color: accent }}>
            “{slide.body}”
          </p>
          <p style={{ fontSize: px(12), marginTop: px(10), color: '#666' }}>{slide.title}</p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col" style={{ paddingLeft: px(16) }}>
          <h3 style={{ fontSize: px(19), fontWeight: 700, marginBottom: px(12) }}>{slide.title}</h3>
          {slide.bullets && (
            <ul style={{ fontSize: px(13), lineHeight: 1.7 }}>
              {slide.bullets.map((b, i) => (
                <li key={i} className="flex gap-2">
                  <span style={{ color: accent }}>•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}
          {slide.body && !slide.bullets?.length && (
            <p style={{ fontSize: px(12), color: '#555', marginTop: px(8), lineHeight: 1.6 }}>
              {slide.body}
            </p>
          )}
          {/* 아직 안 쓰인 장. 빈 흰 화면이면 다 만들어진 것처럼 보인다 */}
          {pending && (
            <p style={{ fontSize: px(12), color: '#aaa', marginTop: px(6) }}>{t('쓰는 중…')}</p>
          )}
        </div>
      )}
    </div>
  )
}

/** The editable text of a slide, as lines: the title, then the bullets. */
function toLines(slide: Slide): string {
  return [slide.title, ...(slide.bullets ?? []), slide.body ?? ''].filter(Boolean).join('\n')
}

export function DeckPanel({ deck, onClose }: { deck: DeckArtifact; onClose?: () => void }) {
  const t = useT()
  const [selected, setSelected] = useState(0)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const runFactCheck = async (slideId: string) => {
    setChecking(true)
    setError(null)
    try {
      const row = await artifactsApi.factcheckSlide(deck.id, slideId)
      const next = (row.data as { slides?: Slide[] } | null)?.slides
      if (next) deck.slides = next
    } catch (err) {
      setError(err instanceof Error ? err.message : t('확인하지 못했습니다.'))
    } finally {
      setChecking(false)
    }
  }
  // Slides arrive one at a time, so the selection can point past the end.
  const index = Math.min(selected, Math.max(deck.slides.length - 1, 0))
  const slide = deck.slides[index] as Slide | undefined
  // A deck still being written has no server id: export would 404 and an edit
  // would be overwritten by the next slide event.
  const weakSlides = deck.slides
    .map((s, i) => (s.factCheck?.claims.some((c) => c.verdict !== 'supported') ? i : -1))
    .filter((i) => i >= 0)
  const writing = deck.slides.length === 0 || deck.slides.some((s) => !s.bullets?.length && !s.body)

  useEffect(() => {
    if (writing) setEditing(false)
  }, [writing])

  const startEditing = () => {
    if (!slide) return
    setError(null)
    setDraft(toLines(slide))
    setNotes(slide.notes ?? '')
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
    // Layout follows the shape that arrived: on a quote slide one line is a
    // quotation and several are bullets, since quote renders only the first.
    const edited: Slide =
      slide.layout === 'quote' && rest.length <= 1
        ? { ...slide, title, body: rest[0] ?? '', bullets: undefined, notes }
        : slide.layout === 'title'
          ? { ...slide, title, body: rest.join(' '), notes }
          : { ...slide, layout: 'bullets', title, bullets: rest, body: undefined, notes }

    const slides = deck.slides.map((s, i) => (i === index ? edited : s))
    setSaving(true)
    setError(null)
    try {
      // PATCHing `data` as one deck is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      await artifactsApi.update(deck.id, {
        data: { kind: 'deck', theme: deck.theme, slides },
        summary: t('{n}장 편집').replace('{n}', String(index + 1)),
      })
      deck.slides = slides
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('저장하지 못했습니다.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <Presentation size={15} className="shrink-0 text-accent" />
        <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{deck.title}</p>
        <Badge>{t('{n}장').replace('{n}', String(deck.slides.length))}</Badge>
        {/* 장마다 눌러 보지 않아도 확인이 필요한 곳이 몇 군데인지 보이게 한다 */}
        {weakSlides.length > 0 && (
          <button
            onClick={() => setSelected(weakSlides[0])}
            title={t('{list}번 장').replace('{list}', weakSlides.map((i) => i + 1).join(', '))}
          >
            <Badge tone="warn">
              <TriangleAlert size={10} />
              {t('확인 필요 {n}장').replace('{n}', String(weakSlides.length))}
            </Badge>
          </button>
        )}
        <Badge>{deck.theme}</Badge>
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
        {onClose && (
          <Button variant="ghost" size="icon" aria-label={t('닫기')} onClick={onClose}>
            <X size={15} />
          </Button>
        )}
      </header>

      {/* 선택된 장 미리보기 */}
      <div className="border-b border-line bg-elevated/40 p-4">
        <div className="mx-auto aspect-video w-full max-w-lg overflow-hidden rounded-xl border border-line shadow-sm">
          {slide ? (
            <SlideView slide={slide} scale={1.15} />
          ) : (
            <div className="grid size-full place-items-center bg-white text-[13px] text-[#999]">
              {t('구성을 잡는 중…')}
            </div>
          )}
        </div>

        {slide && (
          <div className="mx-auto mt-3 max-w-lg">
            <div className="flex items-center gap-2">
              <StickyNote size={13} className="shrink-0 text-faint" />
              <span className="flex-1 text-[11px] font-semibold tracking-wide text-faint uppercase">
                {t('발표 노트')}
              </span>
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
                <Button variant="ghost" size="sm" onClick={startEditing} disabled={writing}>
                  {t('텍스트 수정')}
                </Button>
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
                <p className="text-[11px] text-faint">{t('첫 줄이 제목, 나머지 줄이 각각 한 항목')}</p>
                <Textarea
                  rows={3}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('발표 노트')}
                  aria-label={t('발표 노트')}
                />
                {error && <p className="text-[12px] text-danger">{error}</p>}
              </div>
            ) : (
              <>
                <p className="mt-1.5 text-[13px] text-muted">
                  {slide.notes || <span className="text-faint">{t('노트 없음')}</span>}
                </p>
                {slide.factCheck?.status === 'done' && (
                  <FactCheckResults check={slide.factCheck} />
                )}
                {error && !editing && <p className="mt-2 text-[12px] text-danger">{error}</p>}
              </>
            )}
          </div>
        )}
      </div>

      {/* 썸네일 그리드 */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-3">
          {deck.slides.map((s, i) => (
            <button
              key={s.id}
              onClick={() => {
                setSelected(i)
                setEditing(false)
              }}
              className={cn(
                'group relative aspect-video overflow-hidden rounded-lg border-2 bg-white transition-colors',
                i === index ? 'border-accent' : 'border-line hover:border-line-strong',
              )}
            >
              <SlideView slide={s} scale={0.42} />
              <span className="absolute bottom-1 left-1 rounded bg-black/55 px-1.5 py-0.5 text-[10px] font-medium text-white">
                {i + 1}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
