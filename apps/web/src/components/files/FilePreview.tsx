import { Download, FilePenLine, Loader2 } from 'lucide-react'
import { Suspense, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, LoadingState, Modal } from '@/components/ui'
import {
  artifactsApi, downloadFile, errorMessage, fileBlob, filesApi,
  type ArtifactRow, type FileRow, type OfficeKind,
} from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { fileSize, officeKindOf } from './fileBox'
import { MediaPlayer } from './MediaPlayer'
import { PREVIEW_LABEL, PREVIEWS } from './OfficeView'

/** What each kind's preview is called, and what is said when its editor cannot open a copy. */
const EDITOR: Record<OfficeKind, { label: string; failed: string }> = {
  hwp: { label: PREVIEW_LABEL.hwp, failed: '이 파일을 한글 편집기로 열지 못했습니다.' },
  docx: { label: PREVIEW_LABEL.docx, failed: '이 파일을 Word 편집기로 열지 못했습니다.' },
  pptx: { label: PREVIEW_LABEL.pptx, failed: '이 파일을 PowerPoint 편집기로 열지 못했습니다.' },
}

type Shown =
  | { kind: 'loading' }
  | { kind: 'image' | 'pdf' | 'audio' | 'video'; url: string }
  | { kind: 'office'; office: OfficeKind; bytes: ArrayBuffer }
  | { kind: 'player' }
  | { kind: 'text'; text: string; truncated: boolean; note?: string }
  | { kind: 'failed'; message: string }

/** What the browser can show from the bytes; the server serves only these inline. */
function mediaOf(type: string): 'image' | 'pdf' | 'audio' | 'video' | null {
  if (type === 'application/pdf') return 'pdf'
  if (type.startsWith('image/') && type !== 'image/svg+xml') return 'image'
  if (type.startsWith('audio/')) return 'audio'
  if (type.startsWith('video/')) return 'video'
  return null
}

/**
 * A file's preview: pictures, PDFs and media as themselves; HWP, Word and PowerPoint files
 * as their editor draws them (read only), with 「편집」, which opens a copy in that editor
 * beside a new chat (the file itself is never written); every other file as its text.
 */
export function FilePreview({ row, onClose, start }: { row: FileRow | null; onClose: () => void; start?: number }) {
  const t = useT()
  const navigate = useNavigate()
  const phone = useMediaQuery('(max-width: 40rem)')
  const newSession = useStore((s) => s.newSession)
  const ensureSession = useStore((s) => s.ensureSession)
  const [shown, setShown] = useState<Shown>({ kind: 'loading' })
  const [drawn, setDrawn] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Documents already opened from this file; the newest is offered before another copy. */
  const [copies, setCopies] = useState<ArtifactRow[]>([])
  const [opening, setOpening] = useState<'new' | 'reopen' | null>(null)
  const office = row ? officeKindOf(row.name) : null

  useEffect(() => {
    if (!row) return
    let alive = true
    let url: string | null = null
    setShown({ kind: 'loading' })
    setDrawn(false)
    setError(null)
    setCopies([])
    setOpening(null)
    const load = async () => {
      const kind = officeKindOf(row.name)
      if (kind) {
        const bytes = await (await fileBlob(row.id)).arrayBuffer()
        if (alive) setShown({ kind: 'office', office: kind, bytes })
        return
      }
      if (row.mime.startsWith('video/') || row.mime.startsWith('audio/')) {
        // streamed with seeking, with what was read from it: never fetched whole
        if (alive) setShown({ kind: 'player' })
        return
      }
      if (mediaOf(row.mime)) {
        const blob = await fileBlob(row.id)
        // the server types by the bytes: a 「picture」 that is not one comes back as an attachment
        const kind = mediaOf(blob.type)
        if (kind) {
          url = URL.createObjectURL(blob)
          if (alive) setShown({ kind, url })
          else URL.revokeObjectURL(url)
          return
        }
      }
      const { text, truncated } = await filesApi.text(row.id)
      if (alive) setShown({ kind: 'text', text, truncated })
    }
    load().catch((err) => alive && setShown({ kind: 'failed', message: errorMessage(err, t('미리보기를 불러오지 못했습니다.')) }))
    if (officeKindOf(row.name)) {
      filesApi.documents(row.id)
        // a copy whose conversation is gone has nowhere to reopen
        .then((found) => alive && setCopies(found.filter((doc) => doc.sessionId)))
        .catch(() => undefined)
    }
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [row, t])

  /** The editor could not draw the file: its text, and a word saying so. */
  const fallBack = () => {
    if (!row) return
    const id = row.id
    filesApi.text(id)
      .then(({ text, truncated }) => setShown({
        kind: 'text', text, truncated, note: t('이 파일은 모양대로 그리지 못해 글자만 보여 줍니다.'),
      }))
      .catch((err) => setShown({ kind: 'failed', message: errorMessage(err, t('미리보기를 불러오지 못했습니다.')) }))
  }

  /** A copy of the file in its editor beside a new chat; the file stays as it is. */
  const edit = async () => {
    if (!row || !office || opening) return
    setOpening('new')
    setError(null)
    try {
      const id = await ensureSession(await newSession('chat', { projectId: row.projectId }))
      const made = await artifactsApi.createDocument(office, { sessionId: id, fileId: row.id })
      navigate(`/s/${id}?artifact=${made.id}`)
    } catch (err) {
      setError(errorMessage(err, t(EDITOR[office].failed)))
      setOpening(null)
    }
  }

  /** The copy made earlier, beside its own chat. */
  const reopen = (doc: ArtifactRow) => {
    setOpening('reopen')
    navigate(`/s/${doc.sessionId}?artifact=${doc.id}`)
  }

  if (!row) return null
  const latest = copies[0]
  const Preview = shown.kind === 'office' ? PREVIEWS[shown.office] : null
  return (
    <Modal
      open
      onClose={onClose}
      title={row.name}
      description={fileSize(row.size)}
      width={office ? 'max-w-6xl' : 'max-w-4xl'}
      footer={
        <div className="flex w-full flex-wrap items-center justify-end gap-2">
          {error && <p className="mr-auto self-center text-base text-danger">{error}</p>}
          {!error && office && (
            <p className="mr-auto min-w-0 flex-1 basis-60 text-sm text-muted" data-testid="file-edit-note">
              {latest
                ? t('이 파일로 만든 문서가 있습니다: {title} · v{version} · {when}')
                  .replace('{title}', latest.title)
                  .replace('{version}', String(latest.version))
                  .replace('{when}', relativeTime(latest.updatedAt))
                : phone
                  ? t('휴대폰에서는 읽기만 합니다. 편집하면 대화에서 에이전트에게 고쳐 달라고 할 수 있습니다.')
                  : t('편집하면 사본을 새 대화 옆 편집기에서 엽니다. 이 파일은 그대로 남습니다.')}
            </p>
          )}
          <Button
            variant="secondary"
            onClick={() => void downloadFile(row.id, row.name).catch((err) => setError(errorMessage(err, t('내려받지 못했습니다.'))))}
          >
            <Download size={14} /> {t('내려받기')}
          </Button>
          {office && latest && (
            <Button variant="secondary" disabled={!!opening} onClick={() => void edit()}>
              {opening === 'new' ? <Loader2 size={14} className="animate-spin" /> : <FilePenLine size={14} />}
              {t('새 사본으로 편집')}
            </Button>
          )}
          {office && (
            <Button
              variant="primary"
              disabled={!!opening}
              onClick={() => (latest ? reopen(latest) : void edit())}
            >
              {opening === (latest ? 'reopen' : 'new') ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <FilePenLine size={14} />
              )}
              {latest ? t('이어서 편집') : t('편집')}
            </Button>
          )}
        </div>
      }
    >
      <div
        data-testid="file-preview"
        data-preview-ready={shown.kind === 'office' ? String(drawn) : undefined}
        className={
          shown.kind === 'office'
            ? 'relative h-[72vh] w-full overflow-hidden rounded-control border border-line max-sm:h-[56dvh]'
            : 'flex max-h-[70vh] min-h-40 justify-center overflow-auto'
        }
      >
        {shown.kind === 'loading' && <LoadingState />}
        {shown.kind === 'image' && <img src={shown.url} alt={row.name} className="max-h-[68vh] max-w-full object-contain" />}
        {shown.kind === 'pdf' && <iframe src={shown.url} title={row.name} className="h-[68vh] w-full rounded-control border border-line" />}
        {shown.kind === 'audio' && <audio src={shown.url} controls className="w-full self-center" />}
        {shown.kind === 'video' && <video src={shown.url} controls className="max-h-[68vh] max-w-full" />}
        {shown.kind === 'player' && <MediaPlayer fileId={row.id} start={start} audio={row.mime.startsWith('audio/')} />}
        {shown.kind === 'office' && Preview && (
          <Suspense fallback={<LoadingState />}>
            <Preview
              key={row.id}
              bytes={shown.bytes}
              name={row.name}
              label={t(EDITOR[shown.office].label)}
              onReady={() => setDrawn(true)}
              onFail={fallBack}
            />
            {!drawn && (
              <div className="pointer-events-none absolute inset-0 grid place-items-center bg-panel/70 text-sm text-muted">
                <span className="flex items-center gap-2">
                  <Loader2 size={15} className="animate-spin" />
                  {t('문서를 여는 중…')}
                </span>
              </div>
            )}
          </Suspense>
        )}
        {shown.kind === 'text' && (
          <div className="w-full">
            {shown.note && <p className="pb-3 text-sm text-muted" data-testid="file-preview-note">{shown.note}</p>}
            {shown.text.trim() ? (
              <>
                <pre className="whitespace-pre-wrap break-words font-sans text-base text-fg">{shown.text}</pre>
                {shown.truncated && <p className="pt-3 text-sm text-faint">{t('앞부분만 보여 줍니다. 전체는 내려받아 보세요.')}</p>}
              </>
            ) : (
              <p className="py-12 text-center text-base text-muted">{t('이 파일은 미리볼 내용이 없습니다. 내려받아 열어 보세요.')}</p>
            )}
          </div>
        )}
        {shown.kind === 'failed' && <p className="self-center text-base text-danger">{shown.message}</p>}
      </div>
    </Modal>
  )
}
