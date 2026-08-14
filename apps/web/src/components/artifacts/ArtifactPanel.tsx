import { AudioLines, Code2, Copy, Download, Eye, X } from 'lucide-react'
import { useState } from 'react'
import { ChartPanel, ChartThumb } from '@/components/chart/ChartPanel'
import { DeckPanel } from '@/components/slides/DeckPanel'
import { ReportPanel } from '@/components/report/ReportPanel'
import { Badge, Button, ButtonLink } from '@/components/ui'
import { fileUrl } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { useNarrowLayout } from '@/lib/useMediaQuery'
import { useStore } from '@/store/useStore'
import type { Artifact } from '@/types'
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
              <span className="min-w-0 flex-1 truncate text-[12px]">{artifact.title}</span>
              <span className="shrink-0 text-[11px] text-faint">
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
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-[13px] leading-relaxed">
          <code className="font-mono">
            {artifact.sections.map((s) => `## ${s.heading}\n${s.content}`).join('\n\n')}
          </code>
        </pre>
      )
    case 'deck':
      return (
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-[13px] leading-relaxed">
          <code className="font-mono">
            {artifact.slides.map((s, i) => `${i + 1}. ${s.title}`).join('\n')}
          </code>
        </pre>
      )
    case 'chart':
      return <ChartThumb chart={artifact} />
    default:
      return (
        <pre className="size-full overflow-auto bg-elevated px-4 py-3 text-[13px] leading-relaxed">
          <code className="font-mono">{artifact.content}</code>
        </pre>
      )
  }
}

function CodePanel({ artifact }: { artifact: Extract<Artifact, { kind: 'code' | 'html' }> }) {
  const t = useT()
  const [tab, setTab] = useState<'preview' | 'source'>(
    artifact.kind === 'html' ? 'preview' : 'source',
  )
  return (
    <div className="flex h-full min-h-0 flex-col">
      {artifact.kind === 'html' && (
        <div className="flex gap-1 border-b border-line px-3 py-1.5">
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
                'flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] transition-colors',
                tab === t.id ? 'bg-elevated text-fg' : 'text-muted hover:text-fg',
              )}
            >
              <t.icon size={13} />
              {t.label}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1">
        {tab === 'preview' && artifact.kind === 'html' ? (
          <ArtifactPreview artifact={artifact} />
        ) : (
          <pre className="h-full overflow-auto bg-elevated px-4 py-3 text-[13px] leading-relaxed">
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
          [t('비율'), artifact.aspect],
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
          <div key={k} className="flex gap-3 text-[13px]">
            <dt className="w-16 shrink-0 text-faint">{k}</dt>
            <dd className="min-w-0 flex-1 break-words">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * Right-hand panel, branching by artifact kind: a table of contents for
 * reports, a thumbnail grid for decks, a viewer plus parameters for media.
 */
export function ArtifactPanel() {
  const t = useT()
  const narrow = useNarrowLayout()
  //: Set by ReportPanel while a section is open for editing. Declared above
  //: the early return: this panel renders once with no artifact, and a hook
  //: below `return null` changes the hook count between renders (React #300).
  const [editingReport, setEditingReport] = useState(false)
  const { artifacts, openArtifactId, openArtifact } = useStore()
  const artifact = artifacts.find((a) => a.id === openArtifactId)
  if (!artifact) return null

  const wide =
    artifact.kind === 'report' || artifact.kind === 'deck' || artifact.kind === 'chart'
  // Reports and decks own their whole panel chrome; the rest share a header.
  const selfChrome = wide

  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col border-l border-line bg-panel',
        narrow
          ? 'absolute inset-0 z-20 w-full min-w-0'
          : editingReport
            // Editing needs source and preview side by side, and the document
            // column is only ~350px — so the panel borrows width while an
            // editor is open.
            ? 'w-[72%] min-w-[720px]'
            : wide
              ? 'w-[52%] min-w-[460px]'
              : 'w-[38%] min-w-[340px]',
      )}
    >
      {!selfChrome && (
        <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{artifact.title}</p>
            <p className="text-[11px] text-faint">
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
              href={fileUrl(artifact.src)}
              download={artifact.title}
            >
              <Download size={15} />
            </ButtonLink>
          )}
          <Button variant="ghost" size="icon" aria-label={t('닫기')} onClick={() => openArtifact(null)}>
            <X size={15} />
          </Button>
        </header>
      )}

      {selfChrome && (
        <div className="min-h-0 flex-1">
          {artifact.kind === 'report' ? (
            <ReportPanel
              report={artifact}
              onClose={() => openArtifact(null)}
              onEditingChange={setEditingReport}
            />
          ) : artifact.kind === 'deck' ? (
            <DeckPanel deck={artifact} onClose={() => openArtifact(null)} />
          ) : artifact.kind === 'chart' ? (
            <ChartPanel chart={artifact} />
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
