import {
  AudioLines,
  BarChart3,
  FilePenLine,
  FileText,
  Loader2,
  Image as ImageIcon,
  Layers,
  MoreHorizontal,
  Presentation,
  Video,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
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
import { ReuseItems } from '@/components/artifacts/ResultActions'
import { reusable } from '@/lib/reuse'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dropdown,
  EmptyState,
  Input,
  LoadingState,
  Modal,
  ReloadNotice,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { OfficeView } from '@/components/files/OfficeView'
import {
  artifactsApi, errorMessage, templateText, type DesignTemplateRow, type OfficeKind,
} from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import type { Artifact, ArtifactKind } from '@/types'
import { useT } from '@/lib/useT'
import { RelTime } from '@/components/ui/RelTime'

const kindIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: FileText,
  html: FileText,
  hwp: FileText,
  docx: FileText,
  pptx: FileText,
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
  hwp: '한글 문서',
  docx: 'Word 문서',
  pptx: 'PowerPoint 문서',
}

/** Unknown kinds: the artifact's icon. */
const templateIcon: Record<string, typeof FileText> = {
  deck: Presentation,
  document: FileText,
}

/** Null once gone from the catalogue. */
function templateOf(artifact: Artifact, templates: DesignTemplateRow[]) {
  const id = artifact.kind === 'html' || artifact.kind === 'code' ? artifact.templateId : undefined
  return id ? templates.find((row) => row.id === id) : undefined
}

type Filter = ArtifactKind | 'all'

const isOffice = (artifact: Artifact): artifact is Extract<Artifact, { kind: OfficeKind }> =>
  artifact.kind === 'hwp' || artifact.kind === 'docx' || artifact.kind === 'pptx'

/**
 * An office document (HWP, Word, PowerPoint) as its editor draws it, read only, with 「편집기에서
 * 열기」: beside its conversation, or beside a new chat when it has none (it joins that chat).
 */
function OfficeArtifactView({ artifact }: { artifact: Extract<Artifact, { kind: OfficeKind }> }) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const ensureSession = useStore((s) => s.ensureSession)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const [bytes, setBytes] = useState<ArrayBuffer | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { kind, id, version } = artifact
  useEffect(() => {
    let live = true
    artifactsApi
      .documentFile(kind, id)
      .then((file) => live && setBytes(file.bytes))
      .catch((err: unknown) => live && setFailed(errorMessage(err, t('문서를 불러오지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [kind, id, version, t])

  const openInEditor = async () => {
    setOpening(true)
    setError(null)
    try {
      let sessionId = artifact.sessionId
      if (!sessionId) {
        // no conversation: a new chat to edit it beside, which it joins
        sessionId = await ensureSession(await newSession('chat', { projectId: artifact.projectId }))
        await artifactsApi.attachDocument(kind, id, sessionId)
        await refreshArtifact(id)
      }
      navigate(`/s/${sessionId}?artifact=${id}`)
    } catch (err) {
      setError(errorMessage(err, t('편집기에서 열지 못했습니다.')))
      setOpening(false)
    }
  }

  const text = artifact.text ?? ''
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <p className="min-w-0 flex-1 text-sm text-muted">
          {error ?? (artifact.sessionId
            ? t('편집하면 이 문서를 만든 대화 옆 편집기에서 엽니다.')
            : t('이 문서의 대화가 없습니다. 편집하면 새 대화 옆 편집기에서 엽니다.'))}
        </p>
        <Button size="sm" variant="primary" disabled={opening} onClick={() => void openInEditor()}>
          {opening ? <Loader2 size={14} className="animate-spin" /> : <FilePenLine size={14} />}
          {t('편집기에서 열기')}
        </Button>
      </div>
      <div className="relative min-h-0 flex-1 bg-elevated">
        {failed ? (
          <div className="absolute inset-0 overflow-auto p-4">
            <p className="pb-3 text-sm text-muted">{failed}</p>
            <pre className="whitespace-pre-wrap break-words font-sans text-base text-fg">{text}</pre>
          </div>
        ) : bytes ? (
          <OfficeView
            key={version}
            kind={kind}
            bytes={bytes}
            name={artifact.title}
            onFail={() => setFailed(t('이 파일은 모양대로 그리지 못해 글자만 보여 줍니다.'))}
          />
        ) : (
          <LoadingState />
        )}
      </div>
    </div>
  )
}

/** Partial HTML/code bodies fetched near the viewport. */
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
    artifactsMoreFailed,
    artifactCounts,
    artifactsFailed,
    refreshArtifact,
    designTemplates,
  } = useStore()
  const english = currentLang() === 'en'

  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')

  // Server-side filter and search; debounced.
  useEffect(() => {
    const timer = setTimeout(
      () => void loadArtifacts({ kind: filter === 'all' ? undefined : filter, q: query }),
      query ? 300 : 0,
    )
    return () => clearTimeout(timer)
  }, [loadArtifacts, filter, query])
  // By id: the panel edits in place.
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [previewMode, setPreviewMode] = useState<PanelMode>('wide')
  const [previewDirty, setPreviewDirty] = useState(false)
  const [confirmPreviewClose, setConfirmPreviewClose] = useState(false)
  const [confirming, setConfirming] = useState<Artifact | null>(null)

  // `?open=<id>`: a result made outside any conversation, opened in the panel.
  const [params, setParams] = useSearchParams()
  const openId = params.get('open')
  useEffect(() => {
    if (!openId) return
    setParams({}, { replace: true })
    setPreviewId(openId)
  }, [openId, setParams])
  // Not on the current page: fetched and joined.
  const previewListed = artifacts.some((a) => a.id === previewId)
  useEffect(() => {
    if (previewId && !previewListed && !artifactsLoading) void refreshArtifact(previewId)
  }, [previewId, previewListed, artifactsLoading, refreshArtifact])

  const preview = artifacts.find((a) => a.id === previewId) ?? null
  const closePreview = () => {
    setPreviewId(null)
    setPreviewMode('wide')
    setPreviewDirty(false)
  }
  const requestPreviewClose = () => previewDirty ? setConfirmPreviewClose(true) : closePreview()
  // Undefined until counts arrive: no number rather than 0.
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
          description={t('만든 아티팩트가 모두 여기 모입니다. 수정할 때마다 버전이 쌓이고, 만든 대화로 바로 돌아갈 수 있습니다.')}
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
          tabs={([
            { id: 'all', label: t('전체'), count: total },
            { id: 'code', label: t('코드'), count: count('code') },
            { id: 'html', label: 'HTML', count: count('html') },
            { id: 'report', label: t('보고서'), count: count('report') },
            { id: 'deck', label: t('슬라이드'), count: count('deck') },
            { id: 'chart', label: t('차트'), count: count('chart') },
            { id: 'image', label: t('이미지'), count: count('image') },
            { id: 'audio', label: t('오디오'), count: count('audio') },
            { id: 'video', label: t('동영상'), count: count('video') },
            { id: 'hwp', label: t('한글'), count: count('hwp') },
            { id: 'docx', label: 'Word', count: count('docx') },
            { id: 'pptx', label: 'PowerPoint', count: count('pptx') },
            // Empty kinds hidden, unless open.
          ] as { id: Filter; label: string; count?: number }[]).filter((tab) => tab.id === 'all' || tab.id === filter || tab.count !== 0)}
        />

        {artifactsFailed && <ReloadNotice onRetry={() => void loadArtifacts()} />}

        <div className="pt-4">
          {artifactsLoading ? (
            <LoadingState />
          ) : artifacts.length === 0 ? (
            <EmptyState
              icon={<Layers size={18} />}
              title={query ? t('찾는 아티팩트가 없습니다') : t('아직 아티팩트가 없습니다')}
              description={
                query
                  ? t('제목의 다른 부분으로 찾아보세요.')
                  : t('챗·보고서·슬라이드·이미지·오디오/동영상에서 만든 아티팩트가 여기에 저장됩니다.')
              }
              action={
                query ? undefined : (
                  <Button variant="primary" onClick={() => navigate('/')}>
                    {t('새 대화 시작')}
                  </Button>
                )
              }
            />
          ) : (
            <>
            <BulkBar
              count={pick.count}
              allPicked={pick.allPicked}
              onToggleAll={pick.toggleAll}
              onClear={pick.clear}
              title={t('아티팩트')}
              note={t('아티팩트를 만든 대화는 그대로 남습니다.')}
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
                      // Refetch on open: list copy may be stale.
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
                        {reusable(a) && (
                          <Dropdown
                            align="right"
                            trigger={() => (
                              <Button variant="ghost" size="icon" aria-label={t('{name} 더보기').replace('{name}', a.title)} title={t('더보기')}>
                                <MoreHorizontal size={14} />
                              </Button>
                            )}
                          >
                            <ReuseItems artifact={a} />
                          </Dropdown>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 삭제').replace('{name}', a.title)}
                          title={t('이 아티팩트를 삭제합니다')}
                          onClick={() => setConfirming(a)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-faint">
                        <Badge>{label}</Badge>
                        <Badge>v{a.version}</Badge>
                        {project && (
                          <span>
                            {project.emoji} {project.name}
                          </span>
                        )}
                        <RelTime iso={a.updatedAt} />
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
            <div className="mt-4 flex flex-col items-center gap-2">
              {artifactsMoreFailed && (
                <p role="alert" className="text-base text-danger">
                  {t('다음 아티팩트를 불러오지 못했습니다. 다시 시도하세요.')}
                </p>
              )}
              <Button
                variant="secondary"
                disabled={artifactsLoadingMore}
                onClick={() => void loadMoreArtifacts()}
              >
                {artifactsLoadingMore
                  ? t('불러오는 중…')
                  : artifactsMoreFailed
                    ? t('다시 시도')
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
            ) : isOffice(preview) ? (
              <OfficeArtifactView artifact={preview} />
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
        description={t('계속하면 아티팩트에서 바꾼 내용이 사라집니다.')}
        confirmLabel={t('저장하지 않고 닫기')}
        onConfirm={closePreview}
      />
    </>
  )
}
