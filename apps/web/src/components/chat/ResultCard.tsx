import { FileText, MoreHorizontal, PanelRight, ShieldQuestion } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button, Dropdown } from '@/components/ui'
import { DownloadMenu, ReuseItems, type DownloadCheck } from '@/components/artifacts/ResultActions'
import { exportChecks } from '@/components/artifacts/artifactExport'
import { reusable } from '@/lib/reuse'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { artifactIcon, artifactLabel, sizeLine } from '@/lib/resultMeta'
import type { Artifact, ArtifactKind } from '@/types'

/** The card's frame and its first line; shared with the read-only card on a shared page. */
function CardFace({
  kind,
  title,
  meta,
  children,
}: {
  kind: ArtifactKind
  title: string
  meta: string
  children?: ReactNode
}) {
  const Icon = artifactIcon[kind] ?? FileText
  return (
    <div
      data-result-card=""
      className="mt-3 flex w-full max-w-xl flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-line bg-panel px-3 py-2.5"
    >
      <span className="flex min-w-0 flex-1 basis-56 items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
          <Icon size={16} />
        </span>
        <span className="min-w-0 flex-1 max-sm:basis-32">
          <span className="block truncate text-base font-medium" title={title}>{title}</span>
          <span className="block truncate text-xs text-faint">{meta}</span>
        </span>
      </span>
      {children && <span className="ml-auto flex shrink-0 items-center gap-1">{children}</span>}
    </div>
  )
}

/** What a turn made, under its answer: kind, size, version; open, take away, or start again from it. */
export function ResultCard({ artifact, version }: { artifact: Artifact; version?: number }) {
  const t = useT()
  const openArtifactAt = useStore((s) => s.openArtifactAt)
  // A panel saves in place; the version it saved is noted beside the artifact.
  const saved = useStore((s) => s.artifactVersions[artifact.id])
  const current = Math.max(artifact.version, saved ?? 0)
  // `version` is what this turn wrote; the artifact may have moved on since.
  const older = version !== undefined && version < current
  const meta = [
    t(artifactLabel[artifact.kind]),
    sizeLine(artifact.kind, artifact as unknown as Record<string, unknown>, t),
    older
      ? t('v{n} · 지금 v{m}').replace('{n}', String(version)).replace('{m}', String(current))
      : `v${current}`,
  ].filter(Boolean).join(' · ')
  const checks: DownloadCheck[] = exportChecks(artifact).map((check) => ({
    label: t(check.label).replace('{count}', String(check.count)).replace('{n}', String(check.count)),
    icon: <ShieldQuestion size={14} />,
    onClick: () =>
      openArtifactAt(artifact.id, typeof check.target === 'number' ? { slide: check.target } : { pane: 'review' }),
  }))
  return (
    <CardFace kind={artifact.kind} title={artifact.title || t('제목 없음')} meta={meta}>
      <Button
        size="sm"
        variant="primary"
        aria-label={t('{name} 열기').replace('{name}', artifact.title)}
        onClick={() => openArtifactAt(artifact.id, older ? { version } : {})}
      >
        <PanelRight size={14} />
        {t('열기')}
      </Button>
      <DownloadMenu
        artifact={artifact}
        checks={checks}
        checksHeading={t('내려받기 전 확인')}
        label={t('{name} 내려받기').replace('{name}', artifact.title)}
        disabled={artifact.draft}
      />
      {reusable(artifact) && (
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" variant="ghost" className="size-8 px-0" aria-label={t('{name} 더보기').replace('{name}', artifact.title)} title={t('더보기')}>
              <MoreHorizontal size={15} />
            </Button>
          )}
        >
          <ReuseItems artifact={artifact} />
        </Dropdown>
      )}
    </CardFace>
  )
}

/** The same card on a shared page: what was made, nothing to change it with. */
export function SharedResultCard({
  kind,
  title,
  data,
  onOpen,
}: {
  kind: string
  title: string
  data: unknown
  onOpen?: () => void
}) {
  const t = useT()
  const known = (kind in artifactLabel ? kind : 'report') as ArtifactKind
  const meta = [t(artifactLabel[known]), sizeLine(kind, data as Record<string, unknown>, t)]
    .filter(Boolean)
    .join(' · ')
  return (
    <CardFace kind={known} title={title || t('제목 없음')} meta={meta}>
      {onOpen && (
        <Button size="sm" onClick={onOpen}>
          {t('보기')}
        </Button>
      )}
    </CardFace>
  )
}
