import { Download, FileType, History, Loader2, MoreHorizontal, Save } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { PanelControls, usePanelWidth, type PanelMode } from '@/components/artifacts/PanelControls'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal } from '@/components/ui'
import { ApiError, artifactsApi, errorMessage, type OfficeKind } from '@/lib/api'
import { guardEdits } from '@/lib/officeGuard'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** What the HWP and Word panels ask of their editor. */
export interface OfficeEditor {
  /** Shows a file; true when it came in another format and is saved back as the editable one. */
  load: (bytes: ArrayBuffer) => Promise<boolean>
  /** The document in the editable format (HWPX, DOCX). */
  export: () => Promise<Uint8Array>
  /** Told after a save, so the editor's own unsaved flag clears. */
  saved: () => Promise<unknown>
}

interface Doc {
  id: string
  title: string
  version: number
  sessionId?: string | null
}

type Ask = (go: boolean) => void

/**
 * An office document beside the chat, whatever the editor: the editor loads the saved
 * version; the person's 저장 adds a version from the one loaded, and the agent's tools add
 * theirs on the server, which the editor then loads, unless that would drop hand edits.
 */
export function useOfficeDocument(
  kind: OfficeKind,
  doc: Doc,
  editor: RefObject<OfficeEditor | null>,
  ready: boolean,
  convertedSummary = '',
) {
  const t = useT()
  /** Version shown in the editor. */
  const [loaded, setLoaded] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const [saving, setSaving] = useState(false)
  const busy = useRef(false)
  const [error, setError] = useState('')
  /** `conflict`: a save lost to a newer version; `behind`: a newer version came in under edits. */
  const [notice, setNotice] = useState<'conflict' | 'behind' | null>(null)
  const [asking, setAsking] = useState<Ask | null>(null)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const noteArtifactVersion = useStore((s) => s.noteArtifactVersion)
  const agentWorking = useStore((s) => !!doc.sessionId && !!s.running[doc.sessionId])

  const markDirty = useCallback((value: boolean) => {
    dirtyRef.current = value
    setDirty(value)
  }, [])

  /** The saved version into the editor; another format is saved back once as the editable one. */
  const load = useCallback(async () => {
    const instance = editor.current
    if (!instance || busy.current) return
    busy.current = true
    setLoading(true)
    setError('')
    try {
      const file = await artifactsApi.documentFile(kind, doc.id)
      const convert = await instance.load(file.bytes)
      let version = file.version
      if (convert) {
        // what the tools and the next save work on
        const converted = await instance.export()
        const row = await artifactsApi.saveDocument(kind, doc.id, converted, version, convertedSummary)
        await instance.saved().catch(() => undefined)
        version = row.version
        void refreshArtifact(doc.id)
      }
      setLoaded(version)
      markDirty(false)
      setNotice(null)
    } catch (err) {
      setError(errorMessage(err, t('문서를 불러오지 못했습니다.')))
    } finally {
      busy.current = false
      setLoading(false)
    }
  }, [kind, doc.id, editor, convertedSummary, markDirty, refreshArtifact, t])

  useEffect(() => {
    if (!ready) return
    if (loaded === null) {
      void load()
      return
    }
    // newer only: our own save moves `loaded` ahead of the store's copy for a moment
    if (doc.version > loaded && !busy.current) {
      // the agent (or another tab) saved: load it, unless that would drop hand edits
      if (dirtyRef.current) setNotice('behind')
      else void load()
    }
  }, [ready, loaded, doc.version, load])

  const save = useCallback(async (): Promise<boolean> => {
    const instance = editor.current
    if (!instance || loaded === null || busy.current) return false
    busy.current = true
    setSaving(true)
    setError('')
    try {
      const bytes = await instance.export()
      const row = await artifactsApi.saveDocument(kind, doc.id, bytes, loaded, '편집기에서 저장')
      await instance.saved().catch(() => undefined)
      setLoaded(row.version)
      markDirty(false)
      setNotice(null)
      noteArtifactVersion(doc.id, row.version)
      void refreshArtifact(doc.id)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setNotice('conflict')
      else setError(errorMessage(err, t('저장하지 못했습니다.')))
      return false
    } finally {
      busy.current = false
      setSaving(false)
    }
  }, [kind, doc.id, editor, loaded, markDirty, noteArtifactVersion, refreshArtifact, t])

  // Before an agent turn: unsaved hand edits are saved or dropped (`Composer`).
  useEffect(() => {
    if (!doc.sessionId) return
    return guardEdits(doc.sessionId, {
      dirty: () => dirtyRef.current,
      ask: () => new Promise<boolean>((resolve) => setAsking(() => resolve)),
    })
  }, [doc.sessionId])

  const answer = async (choice: 'save' | 'discard' | 'cancel') => {
    const resolve = asking
    setAsking(null)
    if (!resolve) return
    if (choice === 'cancel') return resolve(false)
    if (choice === 'save') return resolve(await save())
    await load()
    resolve(true)
  }

  return {
    loaded, loading, dirty, dirtyRef, markDirty, saving, busy, error, setError, notice,
    load, save, asking, answer, agentWorking,
  }
}

export type OfficeDocument = ReturnType<typeof useOfficeDocument>

/**
 * The panel around an office editor: title, version, unsaved mark, 저장, the download menu
 * and version history, the notices, the lock while the agent works, and the question before
 * a turn is sent over unsaved edits. `children` is the editor's area.
 */
export function OfficePanel({
  kind,
  doc,
  state,
  ready,
  failed,
  phone,
  downloads,
  onDownloadEdits,
  onClose,
  onModeChange,
  children,
}: {
  kind: OfficeKind
  doc: Doc
  state: OfficeDocument
  ready: boolean
  failed: string
  phone: boolean
  /** The download menu's items. */
  downloads: ReactNode
  /** The editor's current file, kept when a save lost or a newer version came in. */
  onDownloadEdits: () => void
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
  children: ReactNode
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)
  const [history, setHistory] = useState<{ nonce: number } | null>(null)
  const { dirty, saving, agentWorking, loaded, loading, notice, error, asking, answer } = state
  const shown = loaded ?? doc.version
  const panel = { [`data-${kind}-panel`]: doc.id }
  return (
    <div className="flex h-full min-h-0 flex-col" {...panel}>
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <FileType size={15} className="shrink-0 text-accent" />
        <p className="min-w-0 flex-1 truncate text-base font-medium">{doc.title}</p>
        <Badge>v{shown}</Badge>
        {dirty && <span className="shrink-0 text-xs text-warn">{t('저장 안 됨')}</span>}
        {!phone && (
          <Button
            size="sm"
            variant={dirty ? 'primary' : 'secondary'}
            disabled={!dirty || saving || agentWorking || !ready}
            onClick={() => void state.save()}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {t('저장')}
          </Button>
        )}
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={!ready || loaded === null} aria-label={t('내려받기')}>
              <Download size={14} />
              {!phone && t('내려받기')}
            </Button>
          )}
        >
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          {downloads}
        </Dropdown>
        <Dropdown
          align="right"
          trigger={() => (
            <Button variant="ghost" size="icon" aria-label={t('더보기')}>
              <MoreHorizontal size={15} />
            </Button>
          )}
        >
          <MenuItem onClick={() => setHistory({ nonce: Date.now() })}>
            <History size={14} />
            {t('버전 기록')}
          </MenuItem>
        </Dropdown>
        <VersionHistory
          bare
          request={history}
          artifact={{ id: doc.id, title: doc.title, version: doc.version }}
          hasUnsavedChanges={dirty}
          onRestored={() => state.markDirty(false)}
        />
        <PanelControls mode={width.mode} onCycle={phone ? undefined : width.cycle} onClose={onClose} />
      </header>

      {(notice || error || failed) && (
        <div role="status" className="flex flex-wrap items-center gap-2 border-b border-line bg-elevated px-4 py-2 text-sm">
          <span className="min-w-0 flex-1">
            {failed
              ? t('편집기를 띄우지 못했습니다: {reason}').replace('{reason}', failed)
              : notice === 'conflict'
                ? t('그사이 다른 판이 저장되어 저장하지 않았습니다.')
                : notice === 'behind'
                  ? t('에이전트가 새 판을 저장했습니다. 지금 편집은 아직 저장되지 않았습니다.')
                  : error}
          </span>
          {notice && (
            <>
              <Button size="sm" onClick={onDownloadEdits}>
                {t('내 편집 내려받기')}
              </Button>
              <Button size="sm" variant="primary" onClick={() => void state.load()}>
                {t('최신 판 불러오기')}
              </Button>
            </>
          )}
        </div>
      )}

      <div className="relative min-h-0 flex-1 bg-elevated">
        {children}
        {(!ready || loading) && !failed && (
          <div className="absolute inset-0 grid place-items-center bg-panel/70 text-sm text-muted">
            <span className="flex items-center gap-2">
              <Loader2 size={15} className="animate-spin" />
              {t('문서를 여는 중…')}
            </span>
          </div>
        )}
        {agentWorking && ready && (
          <div
            className="absolute inset-0 z-10 grid place-items-center bg-panel/60 backdrop-blur-[1px]"
            data-testid={`${kind}-agent-lock`}
          >
            <span className="flex items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-sm shadow-sm">
              <Loader2 size={15} className="animate-spin text-accent" />
              {t('에이전트가 문서를 고치는 중…')}
            </span>
          </div>
        )}
      </div>

      <Modal
        open={asking !== null}
        onClose={() => void answer('cancel')}
        title={t('저장하지 않은 편집이 있습니다')}
        description={t('에이전트는 저장된 문서를 고칩니다. 지금 편집을 저장할까요, 버릴까요?')}
        width="max-w-md"
        footer={
          <div className="flex justify-end gap-2">
            <Button onClick={() => void answer('cancel')}>{t('취소')}</Button>
            <Button onClick={() => void answer('discard')}>{t('버리고 보내기')}</Button>
            <Button variant="primary" onClick={() => void answer('save')}>
              {t('저장하고 보내기')}
            </Button>
          </div>
        }
      />
    </div>
  )
}

/** A file name from a document title. */
export function fileStem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'document'
}
