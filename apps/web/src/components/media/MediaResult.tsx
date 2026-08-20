import { Download } from 'lucide-react'
import { ButtonLink } from '@/components/ui'
import { fileUrl } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { AudioArtifact, ImageArtifact, VideoArtifact } from '@/types'
import { useT } from '@/lib/useT'

const aspectClass: Record<string, string> = {
  '1:1': 'aspect-square',
  '16:9': 'aspect-video',
  '9:16': 'aspect-[9/16]',
  '4:3': 'aspect-[4/3]',
}

const audioKindLabel: Record<AudioArtifact['audioKind'], string> = {
  narration: '내레이션',
  music: '음악',
}

/** Static amplitude envelope. Rendered as bars so it stays crisp at any width. */
function Waveform({ peaks }: { peaks: number[] }) {
  return (
    <div className="flex h-10 flex-1 items-center gap-[2px]" aria-hidden>
      {peaks.map((p, i) => (
        <span
          key={i}
          className="flex-1 rounded-full bg-accent/45"
          style={{ height: `${Math.max(8, p * 100)}%` }}
        />
      ))}
    </div>
  )
}

/**
 * What a media turn answered with, in the conversation.
 *
 * The picture, the clip and the player themselves — not a chip naming them and
 * not a sentence about them. On these surfaces the artifact *is* the reply, so
 * anything written here in its place would be the product speaking for a model
 * that said nothing; and a link to a panel is a reply you have to go and fetch.
 *
 * The panel keeps what a panel is for: full size, versions, export, and the
 * pictures a document borrows. It opens on a click, on the picture or on the
 * button in the top bar, rather than over the conversation by itself.
 */
export function MediaResult({
  artifacts,
  credits,
}: {
  artifacts: (ImageArtifact | AudioArtifact | VideoArtifact)[]
  /** What the turn was charged, from the message. Zero prints nothing. */
  credits: number
}) {
  const t = useT()
  const { models, openArtifact } = useStore()
  const images = artifacts.filter((a): a is ImageArtifact => a.kind === 'image')
  const audios = artifacts.filter((a): a is AudioArtifact => a.kind === 'audio')
  const videos = artifacts.filter((a): a is VideoArtifact => a.kind === 'video')

  const modelLabel = (id: string) => models.find((m) => m.id === id)?.label ?? id
  // What it cost, beside what it made. On a shared allowance where one clip is
  // twelve thousand credits, this is not a detail somebody goes looking for.
  const charge = credits > 0 ? t('{n} 크레딧 차감됨').replace('{n}', credits.toLocaleString()) : ''
  const caption = (parts: string[]) => (
    <p className="mt-1.5 text-xs text-faint">{[...parts, charge].filter(Boolean).join(' · ')}</p>
  )

  return (
    <div className="space-y-3">
      {images.length > 0 && (
        <div>
          {/* One picture fills the turn; a batch is a grid of four, because
              four pictures are one answer to one prompt rather than four. */}
          <div
            className={cn(
              'grid gap-2',
              images.length === 1 ? 'max-w-md grid-cols-1' : 'max-w-lg grid-cols-2',
            )}
          >
            {images.map((img) => (
              <button
                key={img.id}
                onClick={() => openArtifact(img.id)}
                title={t('크게 보기')}
                className={cn(
                  'group relative overflow-hidden rounded-card border border-line bg-elevated',
                  aspectClass[img.actualAspect || img.aspect] ?? 'aspect-square',
                )}
              >
                <img
                  src={fileUrl(img.src)}
                  alt={img.prompt}
                  className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                />
              </button>
            ))}
          </div>
          {caption([
            t('{n}장').replace('{n}', String(images.length)),
            modelLabel(images[0].model),
          ])}
        </div>
      )}

      {videos.map((v) => (
        <div key={v.id}>
          <div className="max-w-md overflow-hidden rounded-panel border border-line bg-panel">
            {/* `posterSrc` is written empty — nothing makes thumbnails — so the
                <video> shows its own first frame instead of a broken image. */}
            <video
              controls
              src={fileUrl(v.src)}
              poster={fileUrl(v.posterSrc)}
              preload="metadata"
              playsInline
              className={cn('w-full bg-black', aspectClass[v.aspect] ?? 'aspect-video')}
            />
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <span className="min-w-0 flex-1 text-xs text-faint">
              {[
                t('{n}초').replace('{n}', String(v.durationSec)),
                v.aspect,
                modelLabel(v.model),
                charge,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <ButtonLink
              variant="ghost"
              size="icon"
              aria-label={t('다운로드')}
              href={fileUrl(v.src)}
              download={v.title}
            >
              <Download size={15} />
            </ButtonLink>
          </div>
        </div>
      ))}

      {audios.map((a) => (
        <div key={a.id}>
          <div className="flex max-w-md items-center gap-3 rounded-panel border border-line bg-panel px-3.5 py-3">
            <div className="min-w-0 flex-1">
              {/* 브라우저 기본 플레이어. `waveform` 은 비어 있는 채로 저장되므로
                  자체 파형 UI 로는 이미 결제된 클립을 들을 수 없다. */}
              <audio controls src={fileUrl(a.src)} className="w-full" preload="metadata">
                <track kind="captions" />
              </audio>
              {a.waveform.length > 0 && <Waveform peaks={a.waveform} />}
            </div>
            <ButtonLink
              variant="ghost"
              size="icon"
              aria-label={t('다운로드')}
              href={fileUrl(a.src)}
              download={a.title}
            >
              <Download size={15} />
            </ButtonLink>
          </div>
          {caption([
            t(audioKindLabel[a.audioKind]),
            a.durationSec > 0 ? t('{n}초').replace('{n}', String(a.durationSec)) : '',
            modelLabel(a.model),
          ].filter(Boolean))}
        </div>
      ))}
    </div>
  )
}
