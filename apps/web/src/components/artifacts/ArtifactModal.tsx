import { FilePenLine, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArtifactPreview, CodePanel, MediaPanel } from '@/components/artifacts/ArtifactPanel'
import { PanelControls, nextMode, type PanelMode } from '@/components/artifacts/PanelControls'
import { ChartPanel } from '@/components/chart/ChartPanel'
import { OfficeView } from '@/components/files/OfficeView'
import { ReportPanel } from '@/components/report/ReportPanel'
import { DeckPanel } from '@/components/slides/DeckPanel'
import { Button, ConfirmDialog, LoadingState, Modal } from '@/components/ui'
import { artifactsApi, errorMessage, type OfficeKind } from '@/lib/api'
import { leaveOffice, officeDirty } from '@/lib/officeGuard'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Artifact, ArtifactKind } from '@/types'

/** A kind's name, as a card or a modal says it. */
const KIND_LABEL: Record<ArtifactKind, string> = {
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

/**
 * One artifact in a modal, in the panel it has beside a chat: the 아티팩트 page's HTML, code,
 * charts and diagrams, and a web report or web slides with no conversation (파일함 › 작업 문서).
 * `id` null: closed. It is fetched whole on open; the store holds the copy the panel edits.
 */
export function ArtifactModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const t = useT()
  const artifacts = useStore((s) => s.artifacts)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const [mode, setMode] = useState<PanelMode>('wide')
  const [dirty, setDirty] = useState(false)
  const [confirmClose, setConfirmClose] = useState(false)

  // a listing copy may be stale or trimmed: the whole one on open
  useEffect(() => {
    if (id) void refreshArtifact(id)
  }, [id, refreshArtifact])
  const preview = id ? (artifacts.find((a) => a.id === id) ?? null) : null
  // the store's list was reloaded without it meanwhile: fetched and joined again
  const held = !!preview
  useEffect(() => {
    if (id && !held) void refreshArtifact(id)
  }, [id, held, refreshArtifact])
  const close = () => {
    setMode('wide')
    setDirty(false)
    onClose()
  }
  // a document on the shared unsaved guard (the web slides, office files) asks its own
  // 저장 / 버리기 / 취소; any other unsaved preview asks here
  const requestClose = () => {
    if (officeDirty()) {
      void leaveOffice().then((go) => go && close())
      return
    }
    if (dirty) setConfirmClose(true)
    else close()
  }

  return (
    <>
      <Modal
        open={!!preview}
        onClose={requestClose}
        title={preview?.title ?? ''}
        description={preview ? `${t(KIND_LABEL[preview.kind])} · v${preview.version}` : undefined}
        width={mode === 'narrow' ? 'max-w-4xl' : 'max-w-7xl'}
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
                <PanelControls mode={mode} onCycle={() => setMode(nextMode(mode))} />
              </header>
            )}
            <div className="min-h-0 flex-1 overflow-hidden">
              {preview.kind === 'report' ? (
                <ReportPanel report={preview} onModeChange={setMode} onClose={close} onDirtyChange={setDirty} />
              ) : preview.kind === 'deck' ? (
                <DeckPanel deck={preview} onModeChange={setMode} onClose={requestClose} onDirtyChange={setDirty} />
              ) : preview.kind === 'chart' ? (
                <ChartPanel chart={preview} onModeChange={setMode} />
              ) : preview.kind === 'image' || preview.kind === 'audio' || preview.kind === 'video' ? (
                <MediaPanel artifact={preview} />
              ) : isOffice(preview) ? (
                <OfficeArtifactView artifact={preview} />
              ) : preview.kind === 'html' || preview.kind === 'code' ? (
                <CodePanel
                  artifact={preview}
                  headerControls={
                    preview.kind === 'html' ? (
                      <PanelControls mode={mode} onCycle={() => setMode(nextMode(mode))} />
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
        open={confirmClose}
        onClose={() => setConfirmClose(false)}
        title={t('저장하지 않은 변경 내용이 있습니다')}
        description={t('계속하면 아티팩트에서 바꾼 내용이 사라집니다.')}
        confirmLabel={t('저장하지 않고 닫기')}
        onConfirm={close}
      />
    </>
  )
}
