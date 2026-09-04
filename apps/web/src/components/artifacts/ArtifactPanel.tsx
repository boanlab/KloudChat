import { AudioLines, Code2, Copy, Download, Eye, ImagePlus, Play, RefreshCw } from 'lucide-react'
import { SlideView } from '@/components/slides/DeckPanel'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ChartPanel, ChartThumb } from '@/components/chart/ChartPanel'
import { LintFindings } from '@/components/artifacts/LintFindings'
import { PicturePicker } from '@/components/artifacts/PicturePicker'
import {
  PanelControls,
  nextMode,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { DeckPanel, PresentStage } from '@/components/slides/DeckPanel'
import { ReportPanel } from '@/components/report/ReportPanel'
import { sectionText } from '@/components/report/SectionBody'
import { Badge, Button, ButtonLink, Dropdown, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact, errorMessage, fileUrl } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useStore } from '@/store/useStore'
import type { Artifact, CodeArtifact } from '@/types'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

const aspectClass: Record<string, string> = {
  '1:1': 'aspect-square',
  '16:9': 'aspect-video',
  '9:16': 'aspect-[9/16]',
  '4:3': 'aspect-[4/3]',
}

/** Strips Markdown notation for thumbnail text. */
function plainText(body: string): string {
  return body
    .replace(/^\s*\|.*\|\s*$/gm, '')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}[-*+]\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/`{1,3}([^`]*)`{1,3}/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(?<!\*)\*([^*]+)\*/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Thumbnail-safe render of any artifact; used by the panel and the gallery. */
export function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const t = useT()
  switch (artifact.kind) {
    case 'image':
      return (
        <img
          src={fileUrl(artifact.src)}
          alt={artifact.prompt}
          className="size-full bg-elevated object-cover"
        />
      )
    case 'video':
      // No thumbnailer: `posterSrc` is empty, so a `<video>` shows the first frame.
      return (
        <video
          src={fileUrl(artifact.src)}
          poster={fileUrl(artifact.posterSrc)}
          preload="metadata"
          muted
          playsInline
          className="size-full bg-black object-cover"
        />
      )
    case 'audio':
      // `waveform` is stored empty; nothing analyses the PCM.
      return (
        <div className="flex size-full items-center gap-2 bg-elevated px-4 text-muted">
          {artifact.waveform.length > 0 ? (
            artifact.waveform.map((p, i) => (
              <span
                key={i}
                className="flex-1 rounded-full bg-accent/45"
                style={{ height: `${Math.max(6, p * 60)}%` }}
              />
            ))
          ) : (
            <>
              <AudioLines size={18} className="shrink-0 text-accent" />
              <span className="min-w-0 flex-1 truncate text-sm">{artifact.title}</span>
              <span className="shrink-0 text-xs text-faint">
              {t('{n}초').replace('{n}', String(artifact.durationSec))}
            </span>
            </>
          )}
        </div>
      )
    case 'html':
      return (
        <iframe
          title={artifact.title}
          srcDoc={artifact.content}
          sandbox=""
          className="size-full border-0 bg-white"
        />
      )
    case 'report':
      // Drawn as a printed page: fixed white background and literal colours,
      // independent of the theme. #6b6b6b on white is 5.3:1 (WCAG AA).
      return (
        <div className="size-full overflow-hidden bg-white px-5 py-4 text-[#1a1a1a]">
          <p className="mb-0.5 truncate text-lg font-semibold">{artifact.title}</p>
          <p className="mb-3 text-xs text-[#6b6b6b]">
            {t('{n}개 절').replace('{n}', String(artifact.sections.length))}
          </p>
          {artifact.sections.slice(0, 3).map((section) => (
            <div key={section.id} className="mb-2">
              <p className="truncate text-sm font-semibold">{section.heading}</p>
              <p className="line-clamp-2 text-xs leading-relaxed text-[#595959]">
                {plainText(sectionText(section))}
              </p>
            </div>
          ))}
        </div>
      )
    case 'deck':
      return artifact.slides[0] ? (
        <div className="size-full overflow-hidden bg-white">
          {/* 0.78 fills a ~310px gallery card with the 400-unit slide space. */}
          <SlideView slide={artifact.slides[0]} scale={0.78} writing={false} />
        </div>
      ) : (
        <div className="grid size-full place-items-center bg-elevated text-base text-muted">
          {artifact.title}
        </div>
      )
    case 'chart':
      return <ChartThumb chart={artifact} />
    default:
      return (
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-base leading-relaxed">
          <code className="font-mono">{artifact.content}</code>
        </pre>
      )
  }
}

/** Whether an HTML artifact is a deck: by template kind, else by markup (same rule as the server). */
function useIsDeck(artifact: CodeArtifact) {
  const templates = useStore((s) => s.designTemplates)
  const template = templates.find((row) => row.id === artifact.templateId)
  return template ? template.kind === 'deck' : artifact.content.includes('class="slide')
}

/** One slide of an HTML deck. */
interface PageSlide {
  title: string
  /** The whole document with only this slide left in it. */
  doc: string
}

/** Splits an HTML deck at its `<section class="slide">` boundaries. */
function splitSlides(html: string): PageSlide[] {
  const page = new DOMParser().parseFromString(html, 'text/html')
  const count = page.querySelectorAll('section.slide').length
  return Array.from({ length: count }, (_, keep) => {
    // Whole page minus the other slides, so the stylesheet and wrapper survive.
    const one = page.cloneNode(true) as Document
    const sections = Array.from(one.querySelectorAll('section.slide'))
    sections.forEach((section, i) => {
      if (i !== keep) section.remove()
    })
    return {
      title: (sections[keep].querySelector('h2, h1')?.textContent ?? '').trim(),
      doc: `<!doctype html>${one.documentElement.outerHTML}`,
    }
  })
}

/** Presents an HTML deck one section at a time in a sandboxed frame. */
function PagePresent({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [presenting, setPresenting] = useState(false)
  const [index, setIndex] = useState(0)
  const slides = useMemo(
    () => (presenting ? splitSlides(artifact.content) : []),
    [presenting, artifact.content],
  )
  const written = /<section[^>]*class="[^"]*\bslide\b/.test(artifact.content)
  const at = Math.min(index, Math.max(slides.length - 1, 0))

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        disabled={!written}
        onClick={() => {
          setIndex(0)
          setPresenting(true)
        }}
      >
        <Play size={13} />
        {t('발표')}
      </Button>
      {presenting && slides.length > 0 && (
        <PresentStage
          title={artifact.title}
          index={at}
          count={slides.length}
          outline={slides.map((s) => s.title)}
          onIndex={setIndex}
          onClose={() => setPresenting(false)}
        >
          <div className="aspect-video max-h-full w-full max-w-6xl overflow-hidden rounded-control bg-white shadow-float">
            <iframe
              title={slides[at].title || artifact.title}
              srcDoc={slides[at].doc}
              sandbox=""
              className="size-full border-0"
            />
          </div>
        </PresentStage>
      )}
    </>
  )
}

/**
 * Export menu for an HTML artifact. Never opens the markup in a tab: a `blob:`
 * URL would run model-written markup under this origin.
 */
function PageExport({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [busy, setBusy] = useState<string | null>(null)
  const isDeck = useIsDeck(artifact)

  const save = async (format: 'pptx' | 'docx' | 'pdf' | 'hwpx' | 'md' | 'html') => {
    setBusy(format)
    try {
      await downloadArtifact(artifact.id, format, artifact.title || 'document')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dropdown
      align="right"
      trigger={() => (
        <Button variant="secondary" size="sm" disabled={busy !== null}>
          <Download size={13} />
          {t('내보내기')}
        </Button>
      )}
    >
      <MenuLabel>{t('형식 선택')}</MenuLabel>
      {isDeck ? (
        <MenuItem hint="PPTX" onClick={() => void save('pptx')}>
          PowerPoint
        </MenuItem>
      ) : (
        <MenuItem hint="DOCX" onClick={() => void save('docx')}>
          {t('Word 문서')}
        </MenuItem>
      )}
      <MenuItem hint="PDF" onClick={() => void save('pdf')}>
        PDF
      </MenuItem>
      {!isDeck && (
        <MenuItem hint="HWPX" onClick={() => void save('hwpx')}>
          {t('한글 문서')}
        </MenuItem>
      )}
      <MenuItem hint="HTML" onClick={() => void save('html')}>
        {t('원본 HTML')}
      </MenuItem>
      <MenuItem hint="MD" onClick={() => void save('md')}>
        {t('텍스트')}
      </MenuItem>
    </Dropdown>
  )
}

/** Rewrites one block of an HTML artifact; the server re-renders from the same seed. */
function RewriteBlock({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const blocks = artifact.blocks ?? []
  // The server refuses artifacts written before blocks kept their markup.
  if (blocks.length === 0) return null

  const rewrite = async () => {
    if (target === null) return
    setBusy(true)
    setError(null)
    try {
      await artifactsApi.rewriteBlock(artifact.id, target, note)
      await loadArtifacts()
      setTarget(null)
      setNote('')
    } catch (err) {
      setError(errorMessage(err, t('다시 쓰지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Dropdown
        align="right"
        trigger={() => (
          <Button variant="secondary" size="sm">
            <RefreshCw size={13} />
            {t('다시 쓰기')}
          </Button>
        )}
      >
        <MenuLabel>{t('어느 부분을 다시 쓸까요?')}</MenuLabel>
        {blocks.map((block, index) => (
          <MenuItem
            key={`${block.title}-${index}`}
            hint={String(index + 1)}
            onClick={() => {
              setNote('')
              setError(null)
              setTarget(index)
            }}
          >
            {block.title || t('제목 없음')}
          </MenuItem>
        ))}
      </Dropdown>

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={t('{name} 다시 쓰기').replace(
          '{name}',
          (target !== null && blocks[target]?.title) || '',
        )}
        description={t('무엇을 고칠지 적으면 그것만 반영합니다. 비워 두면 그냥 다시 씁니다.')}
        footer={
          <>
            <Button onClick={() => setTarget(null)} disabled={busy}>
              {t('취소')}
            </Button>
            <Button variant="primary" onClick={() => void rewrite()} disabled={busy}>
              {busy ? t('다시 쓰는 중…') : t('다시 쓰기')}
            </Button>
          </>
        }
      >
        <Textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-label={t('고칠 내용')}
          placeholder={t('예: 숫자를 빼고 무엇을 결정해야 하는지만 남겨 주세요.')}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/** Inserts an image artifact into one block; the server inlines its bytes. */
function AddBlockImage({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<number | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const blocks = artifact.blocks ?? []
  if (blocks.length === 0) return null

  const insert = async () => {
    if (target === null || !picked) return
    setBusy(true)
    setError(null)
    try {
      await artifactsApi.addBlockImage(artifact.id, target, picked, caption.trim())
      await loadArtifacts()
      setTarget(null)
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
      <Dropdown
        align="right"
        trigger={() => (
          <Button variant="secondary" size="sm">
            <ImagePlus size={13} />
            {t('그림 넣기')}
          </Button>
        )}
      >
        <MenuLabel>{t('어느 자리에 넣을까요?')}</MenuLabel>
        {blocks.map((block, index) => (
          <MenuItem
            key={`img-${block.title}-${index}`}
            hint={String(index + 1)}
            onClick={() => {
              setPicked(null)
              setCaption('')
              setError(null)
              setTarget(index)
            }}
          >
            {block.title || t('제목 없음')}
          </MenuItem>
        ))}
      </Dropdown>

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={t('{name}에 그림 넣기').replace(
          '{name}',
          (target !== null && blocks[target]?.title) || '',
        )}
        description={t('여기서 바로 만들거나 이미 만든 그림을 고를 수 있습니다. 링크가 아니라 파일 안에 담기므로 인쇄와 공유에서도 함께 보입니다.')}
        footer={
          <>
            <Button onClick={() => setTarget(null)} disabled={busy}>
              {t('취소')}
            </Button>
            <Button variant="primary" onClick={() => void insert()} disabled={busy || !picked}>
              {busy ? t('넣는 중…') : t('넣기')}
            </Button>
          </>
        }
      >
        <PicturePicker
          sessionId={artifact.sessionId}
          aspect="4:3"
          picked={picked}
          onPick={setPicked}
          caption={caption}
          onCaption={setCaption}
          about={(target !== null && blocks[target]?.title) || undefined}
          title={artifact.title}
          context={undefined}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/** HTML or code artifact with its controls; also used by the artifacts gallery dialog. */
export function CodePanel({
  artifact,
  headerControls,
}: {
  artifact: Extract<Artifact, { kind: 'code' | 'html' }>
  /** Width/close controls from a resizable host. */
  headerControls?: ReactNode
}) {
  const t = useT()
  const [tab, setTab] = useState<'preview' | 'source'>(
    artifact.kind === 'html' ? 'preview' : 'source',
  )
  const isDeck = useIsDeck(artifact)
  return (
    <div className="flex h-full min-h-0 flex-col">
      {artifact.kind === 'html' && (
        <header className="flex items-center gap-1 border-b border-line px-3 py-1.5">
          {(
            [
              { id: 'preview', label: t('미리보기'), icon: Eye },
              { id: 'source', label: t('소스'), icon: Code2 },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'flex items-center gap-1.5 rounded-control px-2 py-1 text-sm transition-colors',
                tab === t.id ? 'bg-elevated text-fg' : 'text-muted hover:text-fg',
              )}
            >
              <t.icon size={13} />
              {t.label}
            </button>
          ))}
          <span className="flex-1" />
          {headerControls}
          <LintFindings findings={artifact.lint} artifact={artifact} />
          <AddBlockImage artifact={artifact} />
          <RewriteBlock artifact={artifact} />
          {isDeck && <PagePresent artifact={artifact} />}
          <PageExport artifact={artifact} />
          <VersionHistory artifact={artifact} />
        </header>
      )}
      <div className="min-h-0 flex-1">
        {tab === 'preview' && artifact.kind === 'html' ? (
          isDeck ? (
            /* The deck seed sizes a slide to the viewport, so it is framed 16:9
               here. No `max-h`: a height cap would break the ratio; the column scrolls. */
            <div className="h-full overflow-auto p-4">
              <div className="mx-auto aspect-video w-full max-w-6xl overflow-hidden rounded-card border border-line shadow-raised">
                <ArtifactPreview artifact={artifact} />
              </div>
            </div>
          ) : (
            <ArtifactPreview artifact={artifact} />
          )
        ) : (
          <pre className="h-full overflow-auto bg-elevated px-4 py-3 text-base leading-relaxed">
            <code className="font-mono">{artifact.content}</code>
          </pre>
        )}
      </div>
    </div>
  )
}

const audioKindLabel: Record<'narration' | 'music', string> = {
  narration: '내레이션',
  music: '음악',
}

function TranscriptCopy({ text }: { text: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="secondary"
      size="sm"
      className="mt-3"
      onClick={async () => {
        if (!(await copyText(text))) return
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      }}
    >
      {copied ? t('복사됨') : t('대본 복사')}
    </Button>
  )
}

export function MediaPanel({
  artifact,
}: {
  artifact: Extract<Artifact, { kind: 'image' | 'audio' | 'video' }>
}) {
  const t = useT()
  const meta =
    artifact.kind === 'image'
      ? [
          [t('프롬프트'), artifact.prompt],
          // The model honours the ratio only approximately; show the actual one when it differs.
          [
            t('비율'),
            artifact.actualAspect && artifact.actualAspect !== artifact.aspect
              ? `${artifact.actualAspect} (${t('요청')} ${artifact.aspect})`
              : artifact.aspect,
          ],
          ...(artifact.width && artifact.height
            ? [[t('크기'), `${artifact.width}×${artifact.height}`]]
            : []),
          [t('스타일'), artifact.style],
          // No seed row: the upstream ignores the seed.
          [t('모델'), artifact.model],
        ]
      : artifact.kind === 'audio'
        ? [
            [t('프롬프트'), artifact.prompt],
            [t('유형'), audioKindLabel[artifact.audioKind]],
            [t('길이'), `${artifact.durationSec}초`],
            [t('모델'), artifact.model],
          ]
        : [
            [t('프롬프트'), artifact.prompt],
            [t('길이'), `${artifact.durationSec}초`],
            [t('비율'), artifact.aspect],
            [t('모델'), artifact.model],
          ]

  // Frame follows the file's actual aspect, not the requested one.
  const shape =
    artifact.kind === 'image'
      ? artifact.actualAspect || artifact.aspect
      : artifact.kind === 'video'
        ? artifact.aspect
        : ''
  const frame = artifact.kind === 'audio' ? 'h-28' : (aspectClass[shape] ?? 'aspect-square')

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {artifact.kind === 'audio' ? (
        <div className="bg-elevated px-4 py-5">
          <audio controls src={fileUrl(artifact.src)} className="w-full" preload="metadata">
            <track kind="captions" />
          </audio>
        </div>
      ) : artifact.kind === 'video' ? (
        <video
          controls
          src={fileUrl(artifact.src)}
          poster={fileUrl(artifact.posterSrc)}
          preload="metadata"
          playsInline
          className={cn('w-full bg-black', aspectClass[artifact.aspect] ?? 'aspect-video')}
        />
      ) : (
        <div
          className={cn('bg-elevated', artifact.kind === 'image' && artifact.width && artifact.height ? undefined : frame)}
          style={
            artifact.kind === 'image' && artifact.width && artifact.height
              ? { aspectRatio: `${artifact.width} / ${artifact.height}` }
              : undefined
          }
        >
          {artifact.kind === 'image' ? (
            <img
              src={fileUrl(artifact.src)}
              alt={artifact.prompt}
              className="size-full bg-elevated object-contain"
            />
          ) : (
            <ArtifactPreview artifact={artifact} />
          )}
        </div>
      )}
      <dl className="space-y-2.5 px-4 py-4">
        {meta.map(([k, v]) => (
          <div key={k} className="flex gap-3 text-base">
            <dt className="w-16 shrink-0 text-faint">{k}</dt>
            <dd className="min-w-0 flex-1 break-words">{v}</dd>
          </div>
        ))}
      </dl>
      {artifact.kind === 'audio' && artifact.transcript && (
        <section className="border-t border-line px-4 py-4">
          <h3 className="mb-2 text-sm font-medium text-faint">{t('대본')}</h3>
          <p className="text-base leading-relaxed whitespace-pre-wrap">
            {artifact.transcript}
          </p>
          <TranscriptCopy text={artifact.transcript} />
        </section>
      )}
    </div>
  )
}

/** localStorage key for the dragged panel width. */
const WIDTH_KEY = 'kchat-panel-width'
/** Minimum px left of the panel: 268px sidebar plus a usable composer. */
const CHAT_EDGE_MIN = 560

/** Draggable panel width, remembered across reloads. */
function useSplit(enabled: boolean) {
  const [width, setWidth] = useState<number | null>(() => {
    const saved = Number(localStorage.getItem(WIDTH_KEY))
    return Number.isFinite(saved) && saved > 0 ? saved : null
  })
  const dragging = useRef(false)

  useEffect(() => {
    if (!enabled) return
    const onMove = (e: PointerEvent) => {
      if (!dragging.current) return
      // Width is measured from the right edge.
      const next = Math.round(window.innerWidth - e.clientX)
      const min = 320
      const max = Math.max(min, window.innerWidth - CHAT_EDGE_MIN)
      setWidth(Math.min(max, Math.max(min, next)))
    }
    const onUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.removeProperty('cursor')
      document.body.style.removeProperty('user-select')
      setWidth((w) => {
        if (w) localStorage.setItem(WIDTH_KEY, String(w))
        return w
      })
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [enabled])

  const start = () => {
    dragging.current = true
    // On the body: the pointer leaves the handle immediately while dragging.
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  /** Keyboard step. */
  const nudge = (by: number) =>
    setWidth((w) => {
      const from = w ?? Math.round(window.innerWidth * 0.45)
      const next = Math.min(Math.max(320, from + by), Math.max(320, window.innerWidth - CHAT_EDGE_MIN))
      localStorage.setItem(WIDTH_KEY, String(next))
      return next
    })

  return { width, start, nudge, reset: () => { setWidth(null); localStorage.removeItem(WIDTH_KEY) } }
}

export function ArtifactPanel() {
  const t = useT()
  // Below this width the panel overlays the chat instead of sharing the row.
  const narrow = useMediaQuery('(max-width: 1359px)')
  // Hooks stay above the early return below.
  const [mode, setMode] = useState<PanelMode>('wide')
  const split = useSplit(!narrow)
  const { artifacts, openArtifactId, openArtifact } = useStore()
  const artifact = artifacts.find((a) => a.id === openArtifactId)
  if (!artifact) return null

  const selfWide =
    artifact.kind === 'report' || artifact.kind === 'deck' || artifact.kind === 'chart'
  // Reports, decks and charts own their panel chrome; the rest share a header.
  const selfChrome = selfWide

  // A dragged width wins over every mode default.
  const dragged = !narrow && split.width !== null

  // A mode change discards the dragged width; a repeat of the current mode
  // (a panel announcing `wide` on mount) must not.
  const changeMode = (next: PanelMode) => {
    if (next !== mode) split.reset()
    setMode(next)
  }

  return (
    <aside
      data-panel="artifact"
      style={dragged ? { width: split.width! } : undefined}
      className={cn(
        'relative flex shrink-0 flex-col border-l border-line bg-panel',
        narrow
          ? '!absolute inset-0 z-20 w-full min-w-0'
          : dragged
            ? 'min-w-0'
            : mode === 'full'
              // Overlays the row rather than squeezing the transcript to a sliver.
              ? '!absolute inset-0 z-20 w-full min-w-0'
              : mode === 'wide'
                ? 'w-[60%] min-w-[720px]'
                : selfWide
                  ? 'w-[52%] min-w-[460px]'
                  : 'w-[38%] min-w-[340px]',
      )}
    >
      {!narrow && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('패널 너비 조절')}
          title={t('끌어서 너비를 조절합니다. 두 번 누르면 기본값으로 돌아갑니다.')}
          tabIndex={0}
          onPointerDown={(e) => {
            e.preventDefault()
            split.start()
          }}
          onDoubleClick={split.reset}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') split.nudge(40)
            if (e.key === 'ArrowRight') split.nudge(-40)
            if (e.key === 'Home') split.reset()
          }}
          className="group absolute inset-y-0 -left-1 z-30 w-2 cursor-col-resize focus-visible:outline-none"
        >
          <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-accent group-focus-visible:bg-accent" />
        </div>
      )}
      {!selfChrome && (
        <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-base font-medium">{artifact.title}</p>
            <p className="text-xs text-faint">
              v{artifact.version} · {t('{when} 수정').replace('{when}', relativeTime(artifact.updatedAt))}
            </p>
          </div>
          <Badge>{'language' in artifact ? (artifact.language ?? artifact.kind) : artifact.kind}</Badge>
          {'content' in artifact && (
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('복사')}
              onClick={() => void copyText(artifact.content)}
            >
              <Copy size={15} />
            </Button>
          )}
          {'src' in artifact && artifact.src && (
            <ButtonLink
              variant="ghost"
              size="icon"
              aria-label={t('다운로드')}
              title={t('원본 파일을 내려받습니다')}
              href={fileUrl(artifact.src)}
              download={artifact.title}
            >
              <Download size={15} />
            </ButtonLink>
          )}
          <PanelControls
            mode={mode}
            onCycle={narrow ? undefined : () => changeMode(nextMode(mode))}
            onClose={() => openArtifact(null)}
          />
        </header>
      )}

      {selfChrome && (
        <div className="min-h-0 flex-1">
          {artifact.kind === 'report' ? (
            <ReportPanel
              report={artifact}
              onClose={() => openArtifact(null)}
              onModeChange={changeMode}
            />
          ) : artifact.kind === 'deck' ? (
            <DeckPanel
              deck={artifact}
              onClose={() => openArtifact(null)}
              onModeChange={changeMode}
            />
          ) : artifact.kind === 'chart' ? (
            <ChartPanel
              chart={artifact}
              onClose={() => openArtifact(null)}
              onModeChange={changeMode}
            />
          ) : null}
        </div>
      )}

      {!selfChrome &&
        (artifact.kind === 'image' || artifact.kind === 'audio' || artifact.kind === 'video' ? (
          <MediaPanel artifact={artifact} />
        ) : (
          <CodePanel artifact={artifact} />
        ))}
    </aside>
  )
}
