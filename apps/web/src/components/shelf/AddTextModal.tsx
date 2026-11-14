import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Field, Input, Modal, Textarea } from '@/components/ui'
import { errorMessage, shelfApi, type FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'

/** Pasted text as a project source (kept like an uploaded `.txt`, searched like any file). */
export function AddTextModal({
  projectId,
  open,
  onClose,
  onAdded,
}: {
  projectId: string
  open: boolean
  onClose: () => void
  onAdded: (row: FileRow) => void
}) {
  const t = useT()
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!text.trim()) {
      setError(t('붙여 넣을 내용을 입력하세요.'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      onAdded(await shelfApi.addText(projectId, title, text))
      setTitle('')
      setText('')
      onClose()
    } catch (err) {
      setError(errorMessage(err, t('자료로 넣지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title={t('텍스트 붙여 넣기')}
      description={t('복사한 글을 이 프로젝트의 자료로 보관합니다. 파일처럼 검색되고 인용됩니다.')}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>{t('취소')}</Button>
          <Button variant="primary" onClick={() => void save()} aria-busy={busy || undefined}>
            {busy && <Loader2 size={14} className="animate-spin" aria-hidden />}
            {t('자료로 넣기')}
          </Button>
        </>
      }
    >
      <Field label={t('제목')}>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('비우면 첫 줄로 정합니다')} />
      </Field>
      <Field label={t('내용')} required error={error}>
        <Textarea rows={10} value={text} onChange={(e) => { setText(e.target.value); setError(null) }} />
      </Field>
    </Modal>
  )
}
