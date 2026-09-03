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

/** Thumbnail-safe render of any artifact. Used by the panel and the gallery. */
/**
 * Markdown notation out, the sentence left.
 *
 * For thumbnails only. A preview does not need a parser — it needs the words
 * without the marks that tell a renderer what to do with them, because at
 * thumbnail size the marks are most of what you can see.
 */
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
      /*
       * A page, not its source.
       *
       * This drew the sections into a monospace block with the Markdown left
       * in — `## 배경`, `| --- |`, `**근거**` — so the 아티팩트 gallery was a
       * wall of grey notation with no way to tell one report from another.
       * Somebody looking for the thing they wrote yesterday reads a thumbnail
       * the way they read a shelf: by its shape.
       */
      return (
        <div className="size-full overflow-hidden bg-white px-5 py-4 text-[#1a1a1a]">
          <p className="mb-0.5 truncate text-lg font-semibold">{artifact.title}</p>
          {/* 흰 종이 위의 회색. 이 미리보기는 다크 테마를 따르지 않는다 —
              문서는 인쇄물이고 종이는 언제나 희다 — 그래서 색이 토큰이 아니라
              값이다. #999 는 흰 바탕에서 2.85:1 로 WCAG 의 4.5:1 에 못 미쳤고,
              #6b6b6b 는 5.3:1 이면서 여전히 제목보다 뒤에 선다. */}
          <p className="mb-3 text-xs text-[#6b6b6b]">
            {t('{n}개 절').replace('{n}', String(artifact.sections.length))}
          </p>
          {/* Three, not six. A thumbnail is read at a glance from across a
              gallery, and eight lines of 10px prose is the wall this replaced. */}
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
      /*
       * The first slide, drawn.
       *
       * A numbered list of titles was the honest thing to show when a slide
       * was a white rectangle with a stripe down one edge. It is not any more:
       * a deck opens on a cover reversed out of its accent, and that cover is
       * how somebody picks this deck out of a gallery of a hundred. The list
       * said what the deck was about; the cover says which deck it is.
       */
      return artifact.slides[0] ? (
        <div className="size-full overflow-hidden bg-white">
          {/* The gallery card is about 310px across and the slide is drawn in a
              400-unit space, so this is the scale at which one fills the other.
              At 0.42 the cover was a blue rectangle with a caption nobody could
              read. */}
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

/**
 * Whether an HTML artifact is a deck or a document.
 *
 * The same rule the server follows before it exports one: the template says
 * which kind it is, and a template that stopped existing across an upgrade
 * leaves the markup to say so.
 */
function useIsDeck(artifact: CodeArtifact) {
  const templates = useStore((s) => s.designTemplates)
  const template = templates.find((row) => row.id === artifact.templateId)
  return template ? template.kind === 'deck' : artifact.content.includes('class="slide')
}

/** One slide of an HTML deck. */
interface PageSlide {
  title: string
  /** The whole document again, with only this slide left in it. */
  doc: string
}

/**
 * An HTML deck split at its slides.
 *
 * `page_export.to_slides` reads the same `<section class="slide">` boundary
 * server-side and pays for it in design, since the seed needs a browser. Here
 * there is one, so a slide stays the markup it was written as.
 */
function splitSlides(html: string): PageSlide[] {
  const page = new DOMParser().parseFromString(html, 'text/html')
  const count = page.querySelectorAll('section.slide').length
  return Array.from({ length: count }, (_, keep) => {
    // The whole page with the other slides taken out, rather than the section
    // lifted into a page of its own: the stylesheet, the body's own class and
    // anything else the seed wrapped around the deck belong to every slide.
    const one = page.cloneNode(true) as Document
    const sections = Array.from(one.querySelectorAll('section.slide'))
    sections.forEach((section, i) => {
      if (i !== keep) section.remove()
    })
    return {
      // The heading the seed's wrapper wrote from the outline. Read off the
      // markup rather than off `blocks`, so a rewritten block cannot leave the
      // list naming something the slide no longer says.
      title: (sections[keep].querySelector('h2, h1')?.textContent ?? '').trim(),
      doc: `<!doctype html>${one.documentElement.outerHTML}`,
    }
  })
}

/**
 * Presenting a deck that came out as a document.
 *
 * A JSON deck presents by drawing its slide objects; this one cannot, because
 * its design lives in the file's own stylesheet. So the file goes on the wall
 * one section at a time, in the same sandboxed frame the preview uses.
 */
function PagePresent({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [presenting, setPresenting] = useState(false)
  const [index, setIndex] = useState(0)
  // Split on the way in rather than on every render: a document being written
  // changes with every block, and none of those are being presented.
  const slides = useMemo(
    () => (presenting ? splitSlides(artifact.content) : []),
    [presenting, artifact.content],
  )
  // Nothing to walk yet. Same shape as the deck panel's button while the
  // slides are still arriving — present, and plainly not ready.
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
 * The formats an HTML artifact can leave in.
 *
 * `.html` is the artifact itself, and its print rules turn into a PDF in the
 * reader's browser. The rest are the server reading the markup back into
 * slides or sections for the existing exporters.
 *
 * Never opened in a tab from here: a `blob:` URL inherits this origin, and
 * model-written markup is not something to run inside it.
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

/**
 * Rewriting one block of an HTML artifact.
 *
 * The preview is sandboxed, so there is no clicking into the document to say
 * "this part". The blocks the file was written from stand in for that, and
 * the document is re-rendered from the same seed.
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
 * The writing model never produces one and may not point at one, so the
 * picture comes from the image surface and the server inlines its bytes.
 * Nothing is fetched when a reader opens the file.
 */
function AddBlockImage({ artifact }: { artifact: CodeArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<number | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const blocks = artifact.blocks ?? []
  // Only the absence of somewhere to put one is a reason to draw nothing;
  // having no pictures is not — the picker makes them now.
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
          /* A figure in a document sits in a text column, not across a slide. */
          aspect="4:3"
          picked={picked}
          onPick={setPicked}
          caption={caption}
          onCaption={setCaption}
          about={(target !== null && blocks[target]?.title) || undefined}
          title={artifact.title}
          /* The block list carries titles only; the body lives in the
             rendered document. The title is what the suggestion has to go on
             here, and it is enough to name a subject. */
          context={undefined}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * An HTML or code artifact with the controls that belong to it.
 *
 * Exported because the artifacts gallery opens the same document in a dialog
 * and needs the same controls — check, rewrite a block, add a picture,
 * export.
 */
export function CodePanel({
  artifact,
  headerControls,
}: {
  artifact: Extract<Artifact, { kind: 'code' | 'html' }>
  /** Controls supplied by a host whose width this panel can change. */
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
          {/* 서식을 고른 대가가 발표를 못 하는 것이어서는 안 된다. 덱이면
              JSON 덱과 같은 자리에서 같은 이름으로 무대에 오른다. */}
          {isDeck && <PagePresent artifact={artifact} />}
          <PageExport artifact={artifact} />
          {/* 저장 시점. 블록 하나를 다시 쓰거나 그림을 넣는 것도 편집이라
              판이 쌓이는데, 여기에는 그 판으로 돌아갈 길이 없었다. 덱 패널과
              같은 차례로 세운다 — 두 화면이 같은 줄을 읽게 하려는 것이 이
              두 가지를 한 자리에 놓은 이유이므로. */}
          <VersionHistory artifact={artifact} />
        </header>
      )}
      <div className="min-h-0 flex-1">
        {tab === 'preview' && artifact.kind === 'html' ? (
          isDeck ? (
            /* A slide is 16:9, and the deck seed says so as `min-height:
               100vh` — one slide, one viewport. The preview's viewport is this
               panel, which is a tall column, so a slide was drawn the panel's
               shape: a 960×540 design adrift in a box half again as tall, with
               the cover title stranded at the bottom. It matched neither 발표,
               nor the `.pptx`, nor the printed page — all three of which put
               the slide in a 16:9 frame. The same frame, here.

               No `max-h`: an aspect box given a height cap keeps its width and
               loses its ratio, which is the bug again in the other direction.
               The column scrolls instead — what the deck panel's own stage
               does. */
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
  // The frame is the shape of the file, not of the request: a square picture
  // answered to a 16:9 request was drawn into a 16:9 box and cropped top and
  // bottom — the very thing the 비율 line below was warning about.
  const shape =
    artifact.kind === 'image'
      ? artifact.actualAspect || artifact.aspect
      : artifact.kind === 'video'
        ? artifact.aspect
        : ''
  const frame = artifact.kind === 'audio' ? 'h-28' : (aspectClass[shape] ?? 'aspect-square')

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
        <div
          className={cn('bg-elevated', artifact.kind === 'image' && artifact.width && artifact.height ? undefined : frame)}
          style={
            artifact.kind === 'image' && artifact.width && artifact.height
              ? { aspectRatio: `${artifact.width} / ${artifact.height}` }
              : undefined
          }
        >
          {artifact.kind === 'image' ? (
            // Whole, never cropped: the panel is where the picture is judged.
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
// Left navigation plus a chat column wide enough for a prompt and its controls.
//
// 700 left the panel no wider than 740px on a 1440px screen — narrower than
// an A4 page, so the page view could never show a page at its own size and
// the separator moved twenty pixels however far it was dragged. The sidebar
// is 268px and the composer reads fine at 320px; the rest is the document's.
const CHAT_EDGE_MIN = 560

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
    // Held on the body: the pointer leaves the 6px handle immediately, and
    // without this the cursor flickers and the drag selects the transcript.
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  /** Steps for the keyboard, which cannot drag. */
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
  // The application navigation still fits on a laptop, but navigation + chat
  // + an A4/slide editor do not. Only the result panel becomes an overlay at
  // this wider breakpoint; the rest of the app keeps its normal layout.
  const narrow = useMediaQuery('(max-width: 1359px)')
  //: Set by whichever panel is inside — a document asks for the room it needs
  //: and this is what has it. Declared above the early return: this panel
  //: renders once with no artifact, and a hook below `return null` changes the
  //: hook count between renders (React #300).
  const [mode, setMode] = useState<PanelMode>('wide')
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

  /**
   * The mode changing, and the dragged width getting out of its way.
   *
   * Both controls set this panel's width and the drag won whenever it had a
   * number — which is always, because that number is remembered across
   * reloads. So anybody who had once pulled the split found 넓게 보기 ·
   * 문서만 보기 · 패널 좁히기 dead for good: the icon walked round its three
   * positions and the panel never moved. Pressing the button is a width the
   * reader is asking for now, so it takes the drag's place.
   *
   * Only when the position actually changes. A panel announces `wide` as it
   * mounts — see `usePanelWidth` — and an announcement of the mode already in
   * effect must not throw away a width somebody set.
   */
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
              // The document alone, laid over the row rather than pushing the
              // transcript to nothing. Squeezed instead, the transcript keeps
              // its padding and leaves a 32px sliver of composer down the side
              // — a thing too narrow to use and too wide to ignore. The same
              // treatment the narrow screen already uses, for the same reason.
              // The way back is the same button, still in this panel's header.
              ? '!absolute inset-0 z-20 w-full min-w-0'
              : mode === 'wide'
                // Editing needs source and preview side by side, and the
                // document column is only ~350px — so the panel borrows width
                // while an editor is open, or while the reader is reading.
                ? 'w-[60%] min-w-[720px]'
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
