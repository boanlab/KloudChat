import {
  AudioLines,
  BarChart3,
  FileText,
  Image as ImageIcon,
  Layers,
  Presentation,
  Video,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArtifactPreview, CodePanel, MediaPanel } from '@/components/artifacts/ArtifactPanel'
import {
  PanelControls,
  nextMode,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { ChartPanel } from '@/components/chart/ChartPanel'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { DeckPanel } from '@/components/slides/DeckPanel'
import { ReportPanel } from '@/components/report/ReportPanel'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Input,
  LoadingState,
  Modal,
  ReloadNotice,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { templateText, type DesignTemplateRow } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { relativeTime } from '@/lib/utils'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import type { Artifact, ArtifactKind } from '@/types'
import { useT } from '@/lib/useT'

const kindIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: FileText,
  html: FileText,
}

const kindLabel: Record<ArtifactKind, string> = {
  report: '보고서',
  deck: '슬라이드',
  chart: '차트',
  image: '이미지',
  audio: '오디오',
  video: '동영상',
  code: '코드',
  html: 'HTML',
}

/** Icon by template kind; unknown kinds fall back to the artifact's icon. */
const templateIcon: Record<string, typeof FileText> = {
  deck: Presentation,
  document: FileText,
}

/** The template a document was written into, if still in the catalogue. */
function templateOf(artifact: Artifact, templates: DesignTemplateRow[]) {
  const id = artifact.kind === 'html' || artifact.kind === 'code' ? artifact.templateId : undefined
  return id ? templates.find((row) => row.id === id) : undefined
}

type Filter = ArtifactKind | 'all'

/** Card thumbnail; partial HTML/code bodies are fetched when the card nears the viewport. */
function CardThumb({ artifact }: { artifact: Artifact }) {
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const ref = useRef<HTMLDivElement>(null)
  const needsBody = artifact.partial && (artifact.kind === 'html' || artifact.kind === 'code')

  useEffect(() => {
    const node = ref.current
    if (!needsBody || !node) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer.disconnect()
          void refreshArtifact(artifact.id)
        }
      },
      { rootMargin: '600px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [needsBody, artifact.id, refreshArtifact])

  const media =
    artifact.kind === 'image' ||
    artifact.kind === 'audio' ||
    artifact.kind === 'video' ||
    artifact.kind === 'chart'

  return (
    <div ref={ref} className="size-full">
      {needsBody ? (
        <div className="grid size-full place-items-center bg-elevated text-xs text-faint">
          {'…'}
        </div>
      ) : media ? (
        <ArtifactPreview artifact={artifact} />
      ) : (
        <div className="pointer-events-none origin-top-left scale-[0.45] [height:222%] [width:222%]">
          <ArtifactPreview artifact={artifact} />
        </div>
      )}
    </div>
  )
}

export function ArtifactsPage() {
  const t = useT()
  const navigate = useNavigate()
  const {
    artifacts,
    deleteArtifact,
    deleteMany,
    projects,
    sessions,
    loadArtifacts,
    loadMoreArtifacts,
    artifactsLoading,
    artifactsLoadingMore,
    artifactsHasMore,
    artifactCounts,
    artifactsFailed,
    refreshArtifact,
    designTemplates,
  } = useStore()
  const english = currentLang() === 'en'

  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')

  // Server-side filter and search; debounced while typing.
  useEffect(() => {
    const timer = setTimeout(
      () => void loadArtifacts({ kind: filter === 'all' ? undefined : filter, q: query }),
      query ? 300 : 0,
    )
    return () => clearTimeout(timer)
  }, [loadArtifacts, filter, query])
  // By id, not by copy: the panel edits the artifact in place.
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [previewMode, setPreviewMode] = useState<PanelMode>('wide')
  const [previewDirty, setPreviewDirty] = useState(false)
  const [confirmPreviewClose, setConfirmPreviewClose] = useState(false)
  const [confirming, setConfirming] = useState<Artifact | null>(null)

  const preview = artifacts.find((a) => a.id === previewId) ?? null
  const closePreview = () => {
    setPreviewId(null)
    setPreviewMode('wide')
    setPreviewDirty(false)
  }
  const requestPreviewClose = () => previewDirty ? setConfirmPreviewClose(true) : closePreview()
  // Undefined until counts arrive, so tabs show no number rather than 0.
  const count = (k: ArtifactKind) => (artifactCounts ? (artifactCounts[k] ?? 0) : undefined)
  const total = artifactCounts
    ? Object.values(artifactCounts).reduce((sum, n) => sum + n, 0)
    : undefined
  const pick = useBulkSelect(artifacts)

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('아티팩트')}</span>} />
      <PageBody>
        <PageHeader
          title={t('아티팩트')}
          description={t('만든 결과물이 모두 여기 모입니다. 수정할 때마다 버전이 쌓이고, 만든 대화로 바로 돌아갈 수 있습니다.')}
        />

        <div className="mb-3 max-w-sm">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t('아티팩트 검색')}
            placeholder={t('제목으로 찾기')}
          />
        </div>

        <Tabs<Filter>
          value={filter}
          onChange={setFilter}
          tabs={[
            { id: 'all', label: t('전체'), count: total },
            { id: 'code', label: t('코드'), count: count('code') },
            { id: 'html', label: 'HTML', count: count('html') },
            { id: 'report', label: t('보고서'), count: count('report') },
            { id: 'deck', label: t('슬라이드'), count: count('deck') },
            { id: 'chart', label: t('차트'), count: count('chart') },
            { id: 'image', label: t('이미지'), count: count('image') },
            { id: 'audio', label: t('오디오'), count: count('audio') },
            { id: 'video', label: t('동영상'), count: count('video') },
          ]}
        />

        {artifactsFailed && <ReloadNotice onRetry={() => void loadArtifacts()} />}

        <div className="pt-4">
          {artifactsLoading ? (
            <LoadingState />
          ) : artifacts.length === 0 ? (
            <EmptyState
              icon={<Layers size={18} />}
              title={query ? t('찾는 결과물이 없습니다') : t('아직 아티팩트가 없습니다')}
              description={
                query
                  ? t('제목의 다른 부분으로 찾아보세요.')
                  : t('챗·보고서·슬라이드·이미지·오디오/동영상에서 만든 결과물이 여기에 저장됩니다.')
              }
            />
          ) : (
            <>
            <BulkBar
              count={pick.count}
              allPicked={pick.allPicked}
              onToggleAll={pick.toggleAll}
              onClear={pick.clear}
              title={t('결과물')}
              note={t('결과물을 만든 대화는 그대로 남습니다.')}
              onDelete={async () => {
                await deleteMany('artifacts', pick.ids)
                pick.clear()
              }}
            />
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {artifacts.map((a) => {
                const template = templateOf(a, designTemplates)
                const Icon = (template && templateIcon[template.kind]) ?? kindIcon[a.kind]
                const label = template
                  ? templateText(template, english).name
                  : t(kindLabel[a.kind])
                const project = projects.find((p) => p.id === a.projectId)
                const session = sessions.find((s) => s.id === a.sessionId)
                return (
                  <Card key={a.id} className="overflow-hidden">
                    <button
                      aria-label={t('{name} 열기').replace('{name}', a.title)}
                      // Refetch on open: the list copy may be stale.
                      onClick={() => {
                        setPreviewId(a.id)
                        void refreshArtifact(a.id)
                      }}
                      className="block aspect-video w-full overflow-hidden border-b border-line bg-elevated text-left"
                    >
                      <CardThumb artifact={a} />
                    </button>
                    <div className="p-3.5">
                      <div className="flex items-center gap-2">
                        <PickBox
                          checked={pick.picked.has(a.id)}
                          onChange={() => pick.toggle(a.id)}
                          label={t('{name} 선택').replace('{name}', a.title)}
                        />
                        <Icon size={14} className="shrink-0 text-accent" />
                        <p className="min-w-0 flex-1 truncate text-base font-medium">{a.title}</p>
                        <Badge>v{a.version}</Badge>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 삭제').replace('{name}', a.title)}
                          title={t('이 결과물을 삭제합니다')}
                          onClick={() => setConfirming(a)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
                        <Badge>{label}</Badge>
                        {project && (
                          <span>
                            {project.emoji} {project.name}
                          </span>
                        )}
                        <span>{relativeTime(a.updatedAt)}</span>
                      </div>
                      {session && (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="mt-2 -ml-2"
                          onClick={() => navigate(`/s/${session.id}?artifact=${a.id}`)}
                        >
                          {t('원본 작업 열기 →')}
                        </Button>
                      )}
                    </div>
                  </Card>
                )
              })}
            </div>
            </>
          )}

          {artifactsHasMore && (
            <div className="mt-4 flex justify-center">
              <Button
                variant="secondary"
                disabled={artifactsLoadingMore}
                onClick={() => void loadMoreArtifacts()}
              >
                {artifactsLoadingMore
                  ? t('불러오는 중…')
                  : t('{n}개 더 보기').replace(
                      '{n}',
                      String(Math.max(0, (total ?? artifacts.length) - artifacts.length)),
                    )}
              </Button>
            </div>
          )}
        </div>
      </PageBody>

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void deleteArtifact(confirming.id)}
        title={t('{name} 삭제').replace('{name}', confirming?.title ?? '')}
        description={t('되돌릴 수 없습니다. 버전 기록도 함께 사라집니다.')}
      />

      <Modal
        open={!!preview}
        onClose={requestPreviewClose}
        title={preview?.title ?? ''}
        description={preview ? `${t(kindLabel[preview.kind])} · v${preview.version}` : undefined}
        width={previewMode === 'narrow' ? 'max-w-4xl' : 'max-w-7xl'}
        bare={preview?.kind === 'report' || preview?.kind === 'deck'}
      >
        {preview && (
          <div className="flex h-[64vh] flex-col overflow-hidden rounded-card border border-line">
            {/* Report, deck, chart and HTML panels carry their own width control. */}
            {!(
              preview.kind === 'report' ||
              preview.kind === 'deck' ||
              preview.kind === 'chart' ||
              preview.kind === 'html'
            ) && (
              <header className="flex shrink-0 justify-end border-b border-line px-2 py-1.5">
                <PanelControls
                  mode={previewMode}
                  onCycle={() => setPreviewMode(nextMode(previewMode))}
                />
              </header>
            )}
            <div className="min-h-0 flex-1 overflow-hidden">
            {preview.kind === 'report' ? (
              <ReportPanel report={preview} onModeChange={setPreviewMode} onClose={closePreview} onDirtyChange={setPreviewDirty} />
            ) : preview.kind === 'deck' ? (
              <DeckPanel deck={preview} onModeChange={setPreviewMode} onClose={closePreview} onDirtyChange={setPreviewDirty} />
            ) : preview.kind === 'chart' ? (
              <ChartPanel chart={preview} onModeChange={setPreviewMode} />
            ) : preview.kind === 'image' ||
              preview.kind === 'audio' ||
              preview.kind === 'video' ? (
              <MediaPanel artifact={preview} />
            ) : preview.kind === 'html' || preview.kind === 'code' ? (
              <CodePanel
                artifact={preview}
                headerControls={
                  preview.kind === 'html' ? (
                    <PanelControls
                      mode={previewMode}
                      onCycle={() => setPreviewMode(nextMode(previewMode))}
                    />
                  ) : undefined
                }
              />
            ) : (
              <ArtifactPreview artifact={preview} />
            )}
            </div>
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={confirmPreviewClose}
        onClose={() => setConfirmPreviewClose(false)}
        title={t('저장하지 않은 변경 내용이 있습니다')}
        description={t('계속하면 결과물에서 바꾼 내용이 사라집니다.')}
        confirmLabel={t('저장하지 않고 닫기')}
        onConfirm={closePreview}
      />
    </>
  )
}
