import { BookPlus, Download, MessageSquareQuote, NotebookPen, Pencil, Plus, TextSelect, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Markdown } from '@/components/chat/Markdown'
import { Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, Modal, Textarea } from '@/components/ui'
import { ApiError, errorMessage, shelfApi } from '@/lib/api'
import { saveBlob, type Note } from '@/lib/shelf'
import { SHELF_NAMES, withNames } from '@/lib/shelfNames'
import { useMarkUnsaved } from '@/lib/unsaved'
import { useT } from '@/lib/useT'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'

const ORIGINS: Record<Note['origin'], { label: string; icon: typeof NotebookPen }> = {
  manual: { label: '직접 쓴 메모', icon: NotebookPen },
  answer: { label: '답변에서', icon: MessageSquareQuote },
  selection: { label: '고른 대목에서', icon: TextSelect },
}

type Draft = { id: string | null; title: string; body: string; expected: string | null }

/**
 * A note open for editing. Edits stay here until 저장 (or Ctrl+S); closing with unsaved edits
 * asks 저장 / 버리기 / 취소, and leaving the page asks too. A save is conditional on the version
 * the edit started from: a lost race offers the person's own text and the latest version.
 */
function NoteEditor({
  projectId,
  draft,
  onClose,
  onSaved,
}: {
  projectId: string
  draft: Draft
  onClose: () => void
  onSaved: (note: Note) => void
}) {
  const t = useT()
  const [title, setTitle] = useState(draft.title)
  const [body, setBody] = useState(draft.body)
  const [expected, setExpected] = useState(draft.expected)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [asking, setAsking] = useState(false)
  const dirty = title !== draft.title || body !== draft.body
  useMarkUnsaved(`note:${draft.id ?? 'new'}`, dirty)

  const save = async (): Promise<boolean> => {
    if (!body.trim() && !title.trim()) {
      setError(t('메모 내용을 입력하세요.'))
      return false
    }
    setBusy(true)
    setError(null)
    try {
      const saved = draft.id
        ? await shelfApi.editNote(draft.id, { title, body, expected: expected ?? undefined })
        : await shelfApi.addNote(projectId, { title, body, origin: 'manual' })
      onSaved(saved)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.detail === 'note_conflict') setConflict(true)
      else setError(errorMessage(err, t('저장하지 못했습니다.')))
      return false
    } finally {
      setBusy(false)
    }
  }
  const latest = async () => {
    if (!draft.id) return
    const notes = await shelfApi.notes(projectId)
    const found = notes.find((n) => n.id === draft.id)
    if (found) {
      setTitle(found.title)
      setBody(found.body)
      setExpected(found.updatedAt)
    }
    setConflict(false)
  }
  const close = () => (dirty ? setAsking(true) : onClose())

  return (
    <Modal
      open
      onClose={close}
      title={draft.id ? t('메모 고치기') : t('새 메모')}
      width="max-w-2xl"
      footer={
        <>
          <Button onClick={close} disabled={busy}>{t('닫기')}</Button>
          <Button variant="primary" disabled={busy || !dirty} onClick={() => void save().then((ok) => ok && onClose())}>
            {t('저장')}
          </Button>
        </>
      }
    >
      <div
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
            event.preventDefault()
            if (dirty) void save().then((ok) => ok && onClose())
          }
        }}
        className="space-y-3"
      >
        {dirty && <Badge tone="warn">{t('저장 안 됨')}</Badge>}
        <Field label={t('제목')}>
          <Input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder={t('비우면 첫 줄로 정합니다')} />
        </Field>
        <Field label={t('내용')} error={error}>
          <Textarea rows={12} value={body} onChange={(e) => { setBody(e.target.value); setError(null) }} />
        </Field>
        {conflict && (
          <div role="alert" className="space-y-2 rounded-control border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
            <p>{t('다른 곳에서 이 메모를 먼저 저장했습니다. 내 편집을 내려받아 두고 최신 판을 불러올 수 있습니다.')}</p>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => saveBlob(new Blob([`${title}\n\n${body}`], { type: 'text/plain' }), `${title || 'note'}.txt`)}>
                <Download size={13} />
                {t('내 편집 내려받기')}
              </Button>
              <Button size="sm" onClick={() => void latest()}>{t('최신 판 불러오기')}</Button>
            </div>
          </div>
        )}
        {asking && (
          <div role="alertdialog" aria-label={t('저장하지 않은 메모')} className="space-y-2 rounded-control border border-line-strong bg-elevated px-3 py-2 text-sm">
            <p>{t('저장하지 않은 편집이 있습니다.')}</p>
            <div className="flex gap-2">
              <Button size="sm" variant="primary" onClick={() => void save().then((ok) => ok && onClose())}>{t('저장')}</Button>
              <Button size="sm" onClick={onClose}>{t('버리기')}</Button>
              <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>{t('취소')}</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

/** A project's 메모함: notes pinned from answers or selections, or written by hand; edit them,
 * and make one a source (indexed and cited like a file). */
export function NotesTab({ projectId }: { projectId: string }) {
  const t = useT()
  const addProjectFile = useStore((s) => s.addProjectFile)
  const [notes, setNotes] = useState<Note[] | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [removing, setRemoving] = useState<Note | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    () => shelfApi.notes(projectId).then(setNotes).catch((err) => setError(errorMessage(err, t('메모를 불러오지 못했습니다.')))),
    [projectId, t],
  )
  useEffect(() => {
    void load()
  }, [load])

  const toSource = async (note: Note) => {
    setError(null)
    try {
      const row = await shelfApi.noteToSource(note.id)
      addProjectFile(projectId, row)
      await load()
    } catch (err) {
      setError(errorMessage(err, t('자료로 만들지 못했습니다.')))
    }
  }

  return (
    <div className="space-y-3" data-testid="notes">
      <div className="flex items-center justify-between gap-3 rounded-card border border-dashed border-line-strong px-4 py-3">
        <div>
          <p className="text-base font-medium">{t(SHELF_NAMES.notes)}</p>
          <p className="text-sm text-muted">
            {withNames(t('답변이나 고른 대목을 {notes}에 모읍니다. 메모를 {sources}로 만들면 다음 질문부터 파일처럼 찾아 인용합니다.'), t)}
          </p>
        </div>
        <Button size="sm" onClick={() => setDraft({ id: null, title: '', body: '', expected: null })}>
          <Plus size={14} />
          {t('새 메모')}
        </Button>
      </div>
      {error && <p role="alert" className="text-base text-danger">{error}</p>}
      {notes?.length === 0 && (
        <EmptyState
          icon={<NotebookPen size={18} />}
          title={t('메모가 없습니다')}
          description={t('대화의 답변 아래 메모 단추를 누르거나, 답변의 글을 골라 「메모함에 저장」을 누르세요.')}
        />
      )}
      {(notes ?? []).map((note) => {
        const origin = ORIGINS[note.origin]
        const Icon = origin.icon
        return (
          <Card key={note.id} className="px-4 py-3" data-note={note.id}>
            <div className="flex items-center gap-2">
              <Icon size={14} className="shrink-0 text-accent" />
              <p className="min-w-0 flex-1 truncate text-base font-medium">{note.title || t('제목 없음')}</p>
              <Badge>{t(origin.label)}</Badge>
              {note.fileId && <Badge tone="success">{withNames(t('{sources}로 쓰는 중'), t)}</Badge>}
              <span className="shrink-0 text-xs text-faint">{relativeTime(note.updatedAt)}</span>
            </div>
            <div className="mt-1.5 line-clamp-4 text-sm text-muted">
              <Markdown>{note.body}</Markdown>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Button size="sm" variant="ghost" onClick={() => setDraft({ id: note.id, title: note.title, body: note.body, expected: note.updatedAt })}>
                <Pencil size={13} />
                {t('고치기')}
              </Button>
              {!note.fileId && (
                <Button size="sm" variant="ghost" onClick={() => void toSource(note)}>
                  <BookPlus size={13} />
                  {withNames(t('{sources}로 만들기'), t)}
                </Button>
              )}
              <Button size="sm" variant="ghost" aria-label={t('{name} 삭제').replace('{name}', note.title)} onClick={() => setRemoving(note)}>
                <Trash2 size={13} />
                {t('삭제')}
              </Button>
            </div>
          </Card>
        )
      })}
      {draft && (
        <NoteEditor
          projectId={projectId}
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={() => void load()}
        />
      )}
      <ConfirmDialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const target = removing
          setRemoving(null)
          if (!target) return
          shelfApi.removeNote(target.id).then(() => load()).catch((err) => setError(errorMessage(err, t('삭제하지 못했습니다.'))))
        }}
        title={t('{name} 삭제').replace('{name}', removing?.title ?? '')}
        description={withNames(t('메모를 지웁니다. 이 메모로 만든 자료는 지식 탭에서 따로 지울 때까지 남습니다.'), t)}
      />
    </div>
  )
}
