import { useState } from 'react'
import { fileUrl } from '@/lib/api'
import { fileSrc } from '@/lib/imageEdit'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { ImageArtifact } from '@/types'

/** The source beside the edit under one frame: the divider drags (or steps by key) between them. */
export function BeforeAfter({ image }: { image: ImageArtifact }) {
  const t = useT()
  const [at, setAt] = useState(50)
  if (!image.editOf) return null
  const ratio = image.width && image.height ? `${image.width} / ${image.height}` : '1 / 1'
  return (
    <div
      data-testid="image-compare"
      className="relative w-full max-w-md overflow-hidden rounded-card border border-line bg-elevated select-none"
      style={{ aspectRatio: ratio }}
    >
      <img src={fileUrl(image.src)} alt={t('고친 뒤')} className="absolute inset-0 size-full object-contain" />
      <img
        src={fileUrl(image.editOf.src)}
        alt={t('고치기 전')}
        className="absolute inset-0 size-full object-contain"
        style={{ clipPath: `inset(0 ${100 - at}% 0 0)` }}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.25)]"
        style={{ left: `${at}%` }}
      />
      <span className="pointer-events-none absolute top-2 left-2 rounded-control bg-black/55 px-1.5 py-0.5 text-xs text-white">
        {t('전')}
      </span>
      <span className="pointer-events-none absolute top-2 right-2 rounded-control bg-black/55 px-1.5 py-0.5 text-xs text-white">
        {t('후')}
      </span>
      <input
        type="range"
        min={0}
        max={100}
        value={at}
        onChange={(e) => setAt(Number(e.target.value))}
        aria-label={t('전후 비교')}
        className="absolute inset-0 size-full cursor-ew-resize opacity-0"
      />
    </div>
  )
}

type Version = { key: string; number: number; src: string; id: string | null }

/** Every picture of an edit line this conversation holds, the source first; one opens on a tap. */
export function ImageVersions({ image }: { image: ImageArtifact }) {
  const t = useT()
  const artifacts = useStore((s) => s.artifacts)
  const openArtifact = useStore((s) => s.openArtifact)
  const root = image.lineage ?? image.id
  const line = artifacts.filter(
    (a): a is ImageArtifact =>
      a.kind === 'image' && a.sessionId === image.sessionId && (a.id === root || a.lineage === root),
  )
  const versions: Version[] = line.map((a) => ({
    key: a.id,
    number: a.id === root ? 1 : (a.editVersion ?? 2),
    src: a.src,
    id: a.id,
  }))
  // a line started from a file (an attachment, a file-box picture): the file is version 1
  if (root.startsWith('file:') && !versions.some((v) => v.number === 1)) {
    versions.push({ key: root, number: 1, src: fileSrc(root.slice(5)), id: null })
  }
  if (versions.length < 2) return null
  versions.sort((a, b) => a.number - b.number)
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="image-versions" aria-label={t('버전')}>
      <span className="text-xs text-faint">{t('버전')}</span>
      {versions.map((v) => {
        const current = v.id === image.id
        const thumb = (
          <>
            <img src={fileUrl(v.src)} alt="" className="size-7 rounded-[4px] object-cover" />
            <span>{v.number === 1 ? t('원본') : `v${v.number}`}</span>
          </>
        )
        const shell = cn(
          'flex items-center gap-1 rounded-control border px-1 py-0.5 text-xs',
          current ? 'border-accent text-fg' : 'border-line text-muted',
        )
        return v.id ? (
          <button
            key={v.key}
            type="button"
            aria-current={current ? 'true' : undefined}
            title={current ? t('지금 보는 버전') : t('크게 보기')}
            className={cn(shell, !current && 'hover:border-strong hover:text-fg')}
            onClick={() => openArtifact(v.id!)}
          >
            {thumb}
          </button>
        ) : (
          <span key={v.key} className={shell}>
            {thumb}
          </span>
        )
      })}
    </div>
  )
}
