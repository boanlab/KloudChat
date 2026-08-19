import { AudioLines, Code2, Copy, Download, Eye, ImagePlus, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ChartPanel, ChartThumb } from '@/components/chart/ChartPanel'
import { LintFindings } from '@/components/artifacts/LintFindings'
import { PanelControls } from '@/components/artifacts/PanelControls'
import { DeckPanel } from '@/components/slides/DeckPanel'
import { ReportPanel } from '@/components/report/ReportPanel'
import { Badge, Button, ButtonLink, Dropdown, Input, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact, errorMessage, fileUrl } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { useNarrowLayout } from '@/lib/useMediaQuery'
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

/** Thumbnail-safe render of any artifact. Used by the panel and the gallery. */
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
            // `posterSrc` is empty — there is no thumbnailer — so a `<video>`
            // is rendered to show the first frame.
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
      // `waveform` is stored empty: nothing analyses the PCM.
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
      return (
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-base leading-relaxed">
          <code className="font-mono">
            {artifact.sections.map((s) => `## ${s.heading}\n${s.content}`).join('\n\n')}
          </code>
        </pre>
      )
    case 'deck':
      return (
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-base leading-relaxed">
          <code className="font-mono">
            {artifact.slides.map((s, i) => `${i + 1}. ${s.title}`).join('\n')}
          </code>
        </pre>
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

/**
 * The formats an HTML artifact can leave in.
 *
 * `.html` is the artifact itself — the faithful copy, and the one whose print
 * rules turn into a PDF in the reader's own browser. The rest are the server
 * reading the markup back into slides or sections and handing them to the
 * exporters this product already had, so a deck opens in PowerPoint as
 * editable slides rather than as a picture of one.
 *
 * The file is never opened in a tab from here: a `blob:` URL inherits this
 * origin, and model-written markup is not something to run inside it however
 * thoroughly it was stripped on the way in.
 */
function PageExport({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const templates = useStore((s) => s.designTemplates)
  const [busy, setBusy] = useState<string | null>(null)

  // Same rule the server follows: the template says which kind it is, and a
  // template that stopped existing leaves the markup to say so.
  const template = templates.find((row) => row.id === artifact.templateId)
  const isDeck = template ? template.kind === 'deck' : artifact.content.includes('class="slide')
  //: A picture is embedded, so the document itself is the evidence.
  const hasPicture = artifact.content.includes('data:image/')

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
        // Said rather than discovered afterwards: every other format carries
        // an embedded picture and this one cannot, so a document with one in
        // it says so before the download rather than after.
        <MenuItem
          hint={hasPicture ? t('그림 제외') : 'HWPX'}
          onClick={() => void save('hwpx')}
        >
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

/**
 * Rewriting one block of an HTML artifact.
 *
 * The preview is sandboxed, so there is no clicking into the document to say
 * "this part" the way the report panel does. What there is instead is the plan
 * the file was written from — the blocks are on the artifact — so the choice
 * is made from a list and the document is re-rendered from the same seed.
 */
function RewriteBlock({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const blocks = artifact.blocks ?? []
  // Written before blocks kept their markup: the server refuses those, and a
  // button that always fails is worse than no button.
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

/**
 * Putting a picture this workspace already made into one block of a page.
 *
 * The writing model never produces one and is not allowed to point at one, so
 * the picture comes from the other direction: the image surface made it, and
 * the server inlines its bytes on this click. Nothing is fetched when a reader
 * opens the file, which is the only way a picture and a sandboxed artifact can
 * both be true.
 */
function AddBlockImage({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<number | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const artifacts = useStore((s) => s.artifacts)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const blocks = artifact.blocks ?? []
  // Same session first: the picture somebody made for this document is almost
  // always the one they made while writing it.
  const pictures = artifacts
    .filter((a) => a.kind === 'image')
    .sort((a, b) => {
      const mine = Number(b.sessionId === artifact.sessionId) - Number(a.sessionId === artifact.sessionId)
      return mine || +new Date(b.updatedAt) - +new Date(a.updatedAt)
    })
    .slice(0, 24)

  if (blocks.length === 0 || pictures.length === 0) return null

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
        title={t('{name} 에 그림 넣기').replace(
          '{name}',
          (target !== null && blocks[target]?.title) || '',
        )}
        description={t('이미지 화면에서 만든 그림이 문서 안에 그대로 들어갑니다. 링크가 아니라 파일 안에 담기므로 인쇄와 공유에서도 함께 보입니다.')}
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
        <div className="grid max-h-64 grid-cols-3 gap-2 overflow-y-auto">
          {pictures.map((picture) => (
            <button
              key={picture.id}
              onClick={() => setPicked(picture.id)}
              aria-label={picture.title}
              aria-pressed={picked === picture.id}
              className={cn(
                'aspect-video overflow-hidden rounded-control border-2 transition-colors',
                picked === picture.id ? 'border-accent' : 'border-line hover:border-line-strong',
              )}
            >
              <ArtifactPreview artifact={picture} />
            </button>
          ))}
        </div>
        <div className="mt-3">
          <Input
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            aria-label={t('설명')}
            placeholder={t('그림 아래에 붙일 설명 (선택)')}
          />
        </div>
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * An HTML or code artifact with the controls that belong to it.
 *
 * Exported because the artifacts gallery opens the same document in a dialog,
 * and until it used this it showed the file with no way to check it, rewrite a
 * block, add a picture or export it — the same artifact answering differently
 * depending on which screen you opened it from.
 */
export function CodePanel({ artifact }: { artifact: Extract<Artifact, { kind: 'code' | 'html' }> }) {
  const t = useT()
  const [tab, setTab] = useState<'preview' | 'source'>(
    artifact.kind === 'html' ? 'preview' : 'source',
  )
  return (
    <div className="flex h-full min-h-0 flex-col">
      {artifact.kind === 'html' && (
        <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
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
          <LintFindings findings={artifact.lint} artifact={artifact} />
          <AddBlockImage artifact={artifact} />
          <RewriteBlock artifact={artifact} />
          <PageExport artifact={artifact} />
        </div>
      )}
      <div className="min-h-0 flex-1">
        {tab === 'preview' && artifact.kind === 'html' ? (
          <ArtifactPreview artifact={artifact} />
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

/** Take the narration somewhere else — a script to edit, or notes to keep. */
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
          // The request, and — when they differ — what actually came back. The
          // ratio reaches the model as a phrase rather than a parameter, so it
          // is honoured approximately and a lone "16:9" over a square picture
          // is a claim the file does not support.
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
          // No seed row: the field is sent but the upstream ignores it, and a
          // seed that cannot reproduce the picture promises that it can.
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

  // Audio has no aspect ratio — a short fixed strip is the right shape for it.
  const frame =
    artifact.kind === 'audio' ? 'h-28' : (aspectClass[artifact.aspect] ?? 'aspect-square')

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* The player. Media that cannot be played is media nobody can check, and
          until this existed the only way to hear a clip that had already been
          paid for was to open its /api/files URL by hand. */}
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
        <div className={cn('bg-elevated', frame)}>
          <ArtifactPreview artifact={artifact} />
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
      {/* The narration, in words. It was stored from the first clip and shown
          nowhere — which left the only way to check what was said, or to edit
          it and ask again, as listening to the whole thing. */}
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

/**
 * Right-hand panel, branching by artifact kind: a table of contents for
 * reports, a thumbnail grid for decks, a viewer plus parameters for media.
 */
/** Where the split was left, so it survives a reload. */
const WIDTH_KEY = 'kchat-panel-width'

/**
 * A split the reader can move, remembered across reloads.
 *
 * A fixed share works on a maximised browser and not on a half-screen one,
 * where the document ends up a column too narrow to read. Which side needs the
 * room changes minute to minute, so it is the reader's to set.
 */
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
      // Measured from the right edge: the panel is what is being sized, and
      // the transcript takes whatever is left.
      const next = Math.round(window.innerWidth - e.clientX)
      const min = 320
      const max = Math.max(min, window.innerWidth - 360)
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
    // Held on the body: the pointer leaves the 6px handle immediately, and
    // without this the cursor flickers and the drag selects the transcript.
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  /** Steps for the keyboard, which cannot drag. */
  const nudge = (by: number) =>
    setWidth((w) => {
      const from = w ?? Math.round(window.innerWidth * 0.45)
      const next = Math.min(Math.max(320, from + by), window.innerWidth - 360)
      localStorage.setItem(WIDTH_KEY, String(next))
      return next
    })

  return { width, start, nudge, reset: () => { setWidth(null); localStorage.removeItem(WIDTH_KEY) } }
}

export function ArtifactPanel() {
  const t = useT()
  const narrow = useNarrowLayout()
  //: Set by ReportPanel while it needs the width — an open editor, or focus
  //: mode. Declared above the early return: this panel renders once with no
  //: artifact, and a hook below `return null` changes the hook count between
  //: renders (React #300).
  const [wideReport, setWideReport] = useState(false)
  const split = useSplit(!narrow)
  const { artifacts, openArtifactId, openArtifact } = useStore()
  const artifact = artifacts.find((a) => a.id === openArtifactId)
  if (!artifact) return null

  const selfWide =
    artifact.kind === 'report' || artifact.kind === 'deck' || artifact.kind === 'chart'
  // Reports and decks own their whole panel chrome; the rest share a header.
  const selfChrome = selfWide

  // A width the reader dragged wins over every default, including the one an
  // open editor would otherwise impose — they can see the editor and decide.
  const dragged = !narrow && split.width !== null

  return (
    <aside
      data-panel="artifact"
      style={dragged ? { width: split.width! } : undefined}
      className={cn(
        'relative flex shrink-0 flex-col border-l border-line bg-panel',
        narrow
          ? 'absolute inset-0 z-20 w-full min-w-0'
          : dragged
            ? 'min-w-0'
            : wideReport
              // Editing needs source and preview side by side, and the document
              // column is only ~350px — so the panel borrows width while an
              // editor is open, or while the reader asked for room to read.
              ? 'w-[72%] min-w-[720px]'
              : selfWide
                ? 'w-[52%] min-w-[460px]'
                : 'w-[38%] min-w-[340px]',
      )}
    >
      {!narrow && (
        /* The grab strip. Six pixels wide with a wider invisible target, sat
           over the border so the border itself looks like the thing you pull. */
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
            // The keyboard cannot drag, so it steps.
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
          {/* 코드와 미디어도 넓게 볼 수 있어야 한다. 한 줄이 긴 코드는 340px
              패널에서 전부 접히고, 그림은 썸네일만 한 크기로 남는다. */}
          <PanelControls
            wide={wideReport}
            onToggleWide={narrow ? undefined : () => setWideReport(!wideReport)}
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
              onWideChange={setWideReport}
            />
          ) : artifact.kind === 'deck' ? (
            <DeckPanel
              deck={artifact}
              onClose={() => openArtifact(null)}
              onWideChange={setWideReport}
            />
          ) : artifact.kind === 'chart' ? (
            <ChartPanel
              chart={artifact}
              onClose={() => openArtifact(null)}
              onWideChange={setWideReport}
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
