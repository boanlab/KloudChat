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
import { ChartPanel } from '@/components/chart/ChartPanel'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { DeckPanel } from '@/components/slides/DeckPanel'
import { ReportPanel } from '@/components/report/ReportPanel'
import { Badge, Button, Card, EmptyState, Modal, PageHeader, Tabs } from '@/components/ui'
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
  const { artifacts, deleteArtifact, projects, sessions, loadArtifacts } = useStore()

  useEffect(() => {
    void loadArtifacts()
  }, [loadArtifacts])
  const [filter, setFilter] = useState<Filter>('all')
  const [preview, setPreview] = useState<Artifact | null>(null)

  const visible = filter === 'all' ? artifacts : artifacts.filter((a) => a.kind === filter)
  const count = (k: ArtifactKind) => artifacts.filter((a) => a.kind === k).length

  return (
    <>
      <TopBar left={<span className="text-[13px] font-medium">{t('아티팩트')}</span>} />
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

        <div className="pt-4">
          {visible.length === 0 ? (
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
                      onClick={() => setPreview(a)}
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
                        <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{a.title}</p>
                        <Badge>v{a.version}</Badge>
                        {/* `deleteArtifact` has been in the store since the
                            screen was built; nothing ever called it, so the one
                            list that now fills up on its own had no way to be
                            cleared. */}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 삭제').replace('{name}', a.title)}
                          onClick={() => void deleteArtifact(a.id)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-faint">
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

      <Modal
        open={!!preview}
        onClose={() => setPreview(null)}
        title={preview?.title ?? ''}
        description={preview ? `${t(kindLabel[preview.kind])} · v${preview.version}` : undefined}
        width="max-w-4xl"
      >
        {preview && (
          <div className="h-[64vh] overflow-hidden rounded-xl border border-line">
            {preview.kind === 'report' ? (
              <ReportPanel report={preview} />
            ) : preview.kind === 'deck' ? (
              <DeckPanel deck={preview} />
            ) : preview.kind === 'chart' ? (
              <ChartPanel chart={preview} />
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
        )}
      </Modal>
    </>
  )
}
