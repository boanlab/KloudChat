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
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArtifactPreview, MediaPanel } from '@/components/artifacts/ArtifactPanel'
import { PanelControls } from '@/components/artifacts/PanelControls'
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
  LoadingState,
  Modal,
  ReloadNotice,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { relativeTime } from '@/lib/utils'
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

type Filter = ArtifactKind | 'all'

export function ArtifactsPage() {
  const t = useT()
  const navigate = useNavigate()
  const { artifacts, deleteArtifact, projects, sessions, loadArtifacts, artifactsLoading, artifactsFailed, refreshArtifact } =
    useStore()

  useEffect(() => {
    void loadArtifacts()
  }, [loadArtifacts])
  const [filter, setFilter] = useState<Filter>('all')
  const [preview, setPreview] = useState<Artifact | null>(null)
  //: Set by the report panel when it opens an editor or focus mode. Both need
  //: the room, and a dialog that cannot grow makes the control that asks for
  //: it a button that does nothing.
  const [widePreview, setWidePreview] = useState(false)
  const [confirming, setConfirming] = useState<Artifact | null>(null)

  const visible = filter === 'all' ? artifacts : artifacts.filter((a) => a.kind === filter)
  const count = (k: ArtifactKind) => artifacts.filter((a) => a.kind === k).length

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('아티팩트')}</span>} />
      <PageBody>
        <PageHeader
          title={t('아티팩트')}
          description={t('만든 결과물이 모두 여기 모입니다. 수정할 때마다 버전이 쌓이고, 만든 대화로 바로 돌아갈 수 있습니다.')}
        />

        <Tabs<Filter>
          value={filter}
          onChange={setFilter}
          tabs={[
            { id: 'all', label: t('전체'), count: artifacts.length },
            // Code and HTML are what chat actually produces today. Without
            // tabs they existed only under "all", so the one kind the app can
            // make was the one kind you could not filter to.
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
          {artifactsLoading && artifacts.length === 0 ? (
            <LoadingState />
          ) : visible.length === 0 ? (
            <EmptyState
              icon={<Layers size={18} />}
              title={t('아직 아티팩트가 없습니다')}
              description={t('챗·보고서·슬라이드·이미지·오디오/동영상에서 만든 결과물이 여기에 저장됩니다.')}
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((a) => {
                const Icon = kindIcon[a.kind]
                const project = projects.find((p) => p.id === a.projectId)
                const session = sessions.find((s) => s.id === a.sessionId)
                return (
                  <Card key={a.id} className="overflow-hidden">
                    <button
                      // Named, because for a picture or a clip the thumbnail is
                      // the whole button — no text inside it, so a screen
                      // reader announced "button" and nothing else.
                      aria-label={t('{name} 열기').replace('{name}', a.title)}
                      // Opened on the server's copy, not the list's: two
                      // loaders write that list and the later reply can be the
                      // older one, which is invisible until an edit is refused.
                      onClick={() => {
                        setPreview(a)
                        void refreshArtifact(a.id).then((fresh) => fresh && setPreview(fresh))
                      }}
                      className="block aspect-video w-full overflow-hidden border-b border-line bg-elevated text-left"
                    >
                      {a.kind === 'image' || a.kind === 'audio' || a.kind === 'video' || a.kind === 'chart' ? (
                        <ArtifactPreview artifact={a} />
                      ) : (
                        <div className="pointer-events-none origin-top-left scale-[0.45] [height:222%] [width:222%]">
                          <ArtifactPreview artifact={a} />
                        </div>
                      )}
                    </button>
                    <div className="p-3.5">
                      <div className="flex items-center gap-2">
                        <Icon size={14} className="shrink-0 text-accent" />
                        <p className="min-w-0 flex-1 truncate text-base font-medium">{a.title}</p>
                        <Badge>v{a.version}</Badge>
                        {/* `deleteArtifact` has been in the store since the
                            screen was built; nothing ever called it, so the one
                            list that now fills up on its own had no way to be
                            cleared. */}
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
                        <Badge>{t(kindLabel[a.kind])}</Badge>
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
                          onClick={() => navigate(`/s/${session.id}`)}
                        >
                          {t('원본 작업 열기 →')}
                        </Button>
                      )}
                    </div>
                  </Card>
                )
              })}
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
        onClose={() => {
          setPreview(null)
          setWidePreview(false)
        }}
        title={preview?.title ?? ''}
        description={preview ? `${t(kindLabel[preview.kind])} · v${preview.version}` : undefined}
        width={widePreview ? 'max-w-7xl' : 'max-w-4xl'}
      >
        {preview && (
          <div className="flex h-[64vh] flex-col overflow-hidden rounded-xl border border-line">
            {/* 보고서·슬라이드·차트는 자기 머리말에 이 버튼을 갖고 있다. 나머지
                종류에는 머리말이 없어서, 넓혀 보는 일만 할 수 없었다. */}
            {!(preview.kind === 'report' || preview.kind === 'deck' || preview.kind === 'chart') && (
              <header className="flex shrink-0 justify-end border-b border-line px-2 py-1.5">
                <PanelControls
                  wide={widePreview}
                  onToggleWide={() => setWidePreview(!widePreview)}
                />
              </header>
            )}
            <div className="min-h-0 flex-1 overflow-hidden">
            {preview.kind === 'report' ? (
              <ReportPanel report={preview} onWideChange={setWidePreview} />
            ) : preview.kind === 'deck' ? (
              <DeckPanel deck={preview} onWideChange={setWidePreview} />
            ) : preview.kind === 'chart' ? (
              <ChartPanel chart={preview} onWideChange={setWidePreview} />
            ) : preview.kind === 'image' ||
              preview.kind === 'audio' ||
              preview.kind === 'video' ? (
                            // The panel, not a thumbnail: a clip opened from
                            // the gallery has to be playable.
              <MediaPanel artifact={preview} />
            ) : (
              <ArtifactPreview artifact={preview} />
            )}
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}
