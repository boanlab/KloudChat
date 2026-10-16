import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, LoadingState, Modal } from '@/components/ui'
import { downloadFile, errorMessage, fileBlob, filesApi, type FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'
import { fileSize } from './fileBox'

type Shown =
  | { kind: 'loading' }
  | { kind: 'image' | 'pdf' | 'audio' | 'video'; url: string }
  | { kind: 'text'; text: string; truncated: boolean }
  | { kind: 'failed'; message: string }

/** What the browser can show from the bytes; the server serves only these inline. */
function mediaOf(type: string): 'image' | 'pdf' | 'audio' | 'video' | null {
  if (type === 'application/pdf') return 'pdf'
  if (type.startsWith('image/') && type !== 'image/svg+xml') return 'image'
  if (type.startsWith('audio/')) return 'audio'
  if (type.startsWith('video/')) return 'video'
  return null
}

/** A file's preview: pictures, PDFs and media as themselves, every other file as its extracted text. */
export function FilePreview({ row, onClose }: { row: FileRow | null; onClose: () => void }) {
  const t = useT()
  const [shown, setShown] = useState<Shown>({ kind: 'loading' })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!row) return
    let alive = true
    let url: string | null = null
    setShown({ kind: 'loading' })
    setError(null)
    const asText = async () => {
      const { text, truncated } = await filesApi.text(row.id)
      if (alive) setShown({ kind: 'text', text, truncated })
    }
    const load = async () => {
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
      await asText()
    }
    load().catch((err) => alive && setShown({ kind: 'failed', message: errorMessage(err, t('미리보기를 불러오지 못했습니다.')) }))
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [row, t])

  if (!row) return null
  return (
    <Modal
      open
      onClose={onClose}
      title={row.name}
      description={fileSize(row.size)}
      width="max-w-4xl"
      footer={
        <>
          {error && <p className="mr-auto self-center text-base text-danger">{error}</p>}
          <Button
            variant="secondary"
            onClick={() => void downloadFile(row.id, row.name).catch((err) => setError(errorMessage(err, t('내려받지 못했습니다.'))))}
          >
            <Download size={14} /> {t('내려받기')}
          </Button>
        </>
      }
    >
      <div data-testid="file-preview" className="flex max-h-[70vh] min-h-40 justify-center overflow-auto">
        {shown.kind === 'loading' && <LoadingState />}
        {shown.kind === 'image' && <img src={shown.url} alt={row.name} className="max-h-[68vh] max-w-full object-contain" />}
        {shown.kind === 'pdf' && <iframe src={shown.url} title={row.name} className="h-[68vh] w-full rounded-control border border-line" />}
        {shown.kind === 'audio' && <audio src={shown.url} controls className="w-full self-center" />}
        {shown.kind === 'video' && <video src={shown.url} controls className="max-h-[68vh] max-w-full" />}
        {shown.kind === 'text' && (
          shown.text.trim() ? (
            <div className="w-full">
              <pre className="whitespace-pre-wrap break-words font-sans text-base text-fg">{shown.text}</pre>
              {shown.truncated && <p className="pt-3 text-sm text-faint">{t('앞부분만 보여 줍니다. 전체는 내려받아 보세요.')}</p>}
            </div>
          ) : (
            <p className="self-center text-base text-muted">{t('이 파일은 미리볼 내용이 없습니다. 내려받아 열어 보세요.')}</p>
          )
        )}
        {shown.kind === 'failed' && <p className="self-center text-base text-danger">{shown.message}</p>}
      </div>
    </Modal>
  )
}
