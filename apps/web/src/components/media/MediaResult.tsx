import { Code2, Download, PencilLine, RefreshCw } from 'lucide-react'
import { useRef } from 'react'
import { Button, ButtonLink } from '@/components/ui'
import { fileUrl } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
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

/** Static amplitude bars. */
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

/** Inline media reply: pictures, clips and players, with download and regenerate actions. */
export function MediaResult({
  artifacts,
  credits,
  sessionId,
}: {
  artifacts: (ImageArtifact | AudioArtifact | VideoArtifact)[]
  /** Credits charged for the turn; zero prints nothing. */
  credits: number
  /** Session for regenerate actions. */
  sessionId?: string
}) {
  const t = useT()
  const { models, openArtifact } = useStore()
  const generateImages = useStore((s) => s.generateImages)
  const setComposerRestore = useStore((s) => s.setComposerRestore)
  // Edited composed prompt; a ref so the textarea is uncontrolled.
  const edited = useRef('')
  const images = artifacts.filter((a): a is ImageArtifact => a.kind === 'image')
  const audios = artifacts.filter((a): a is AudioArtifact => a.kind === 'audio')
  const videos = artifacts.filter((a): a is VideoArtifact => a.kind === 'video')

  const modelLabel = (id: string) => models.find((m) => m.id === id)?.label ?? id
  const charge = credits > 0 ? t('{n} 크레딧 차감됨').replace('{n}', credits.toLocaleString()) : ''
  const caption = (parts: string[]) => (
    <p className="mt-1.5 text-xs text-faint">{[...parts, charge].filter(Boolean).join(' · ')}</p>
  )

  return (
    <div className="space-y-3">
      {images.length > 0 && (
        <div>
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
                  // Frame follows the file's own pixel ratio; nothing is cropped.
                  img.source ? 'bg-white' : img.width && img.height ? undefined : (aspectClass[img.actualAspect || img.aspect] ?? 'aspect-square'),
                )}
                style={img.width && img.height ? { aspectRatio: `${img.width} / ${img.height}` } : undefined}
              >
                <img
                  src={fileUrl(img.src)}
                  alt={img.caption || img.prompt}
                  className={cn(
                    'size-full transition-transform duration-300 group-hover:scale-[1.03]',
                    img.source ? 'object-contain p-2' : 'object-contain',
                  )}
                />
              </button>
            ))}
          </div>
          {/* Figures carry a caption and their mermaid source. */}
          {images.some((img) => img.source) && (
            <div className="mt-2 max-w-md space-y-1">
              {images.filter((img) => img.caption).map((img) => (
                <p key={img.id} className="text-sm text-muted">{t('그림')}. {img.caption}</p>
              ))}
              <div className="flex flex-wrap items-center gap-1">
                {images.filter((img) => img.source).map((img) => (
                  <Button
                    key={img.id}
                    variant="ghost"
                    size="sm"
                    onClick={() => void copyText(img.source ?? '')}
                    aria-label={t('도식 소스(mermaid) 복사')}
                    title={t('보고서의 mermaid 블록에 붙여 넣거나 직접 고칠 수 있습니다')}
                  >
                    <Code2 size={13} />
                    {t('소스 복사')}
                  </Button>
                ))}
              </div>
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1">
            {images.map((img, i) => (
              <ButtonLink
                key={img.id}
                variant="ghost"
                size="sm"
                href={fileUrl(img.src) ?? '#'}
                download={`${(img.prompt || 'image').slice(0, 40)}${i > 0 ? `-${i + 1}` : ''}.png`}
                aria-label={
                  images.length > 1
                    ? t('{n}번째 그림 내려받기').replace('{n}', String(i + 1))
                    : t('그림 내려받기')
                }
              >
                <Download size={13} />
                {images.length > 1 ? String(i + 1) : t('내려받기')}
              </ButtonLink>
            ))}
            {sessionId && images[0].prompt && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void generateImages(sessionId, images[0].prompt)}
                >
                  <RefreshCw size={13} />
                  {t('다시 만들기')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    // The composer's only way in from outside: the refused-turn restore channel.
                    setComposerRestore({
                      sessionId,
                      value: images[0].prompt,
                      attachments: [],
                      activatedSkillIds: [],
                      startingTemplate: null,
                      error: '',
                    })
                  }
                >
                  <PencilLine size={13} />
                  {t('프롬프트 고치기')}
                </Button>
              </>
            )}
          </div>
          {caption([
            t('{n}장').replace('{n}', String(images.length)),
            modelLabel(images[0].model),
          ])}
          {/* The composed prompt actually sent; editable and resendable as-is. */}
          {images[0].composedPrompt && images[0].composedPrompt !== images[0].prompt && (
            <details className="mt-2 max-w-2xl rounded-control border border-line bg-panel px-3 py-2 text-xs">
              <summary className="cursor-pointer font-medium text-muted">
                {images[0].engine === 'matplotlib' ? t('그린 코드 보기') : t('보낸 프롬프트 보기')}
              </summary>
              <textarea
                className="mt-2 w-full resize-y rounded-control border border-line bg-bg p-2 font-mono text-xs leading-relaxed text-fg"
                rows={images[0].engine === 'matplotlib' ? 16 : 10}
                defaultValue={images[0].composedPrompt}
                aria-label={t('보낸 프롬프트')}
                onChange={(e) => {
                  edited.current = e.target.value
                }}
              />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {sessionId && (
                  <Button
                    size="sm"
                    onClick={() =>
                      void generateImages(sessionId, edited.current || images[0].composedPrompt || '', {
                        raw: true,
                      })
                    }
                  >
                    <RefreshCw size={13} />
                    {images[0].engine === 'matplotlib' ? t('이 코드로 다시 그리기') : t('이 프롬프트로 다시 만들기')}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void copyText(edited.current || images[0].composedPrompt || '')}
                >
                  {t('복사')}
                </Button>
              </div>
            </details>
          )}
        </div>
      )}

      {videos.map((v) => (
        <div key={v.id}>
          <div className="max-w-md overflow-hidden rounded-panel border border-line bg-panel">
            {/* No thumbnailer: `posterSrc` is empty and the first frame shows. */}
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
