import { Download, FileType, History, Loader2, MoreHorizontal, Save } from 'lucide-react'
import { createEditor, type RhwpEditor } from '@rhwp/editor'
import { useCallback, useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import { PanelControls, usePanelWidth, type PanelMode } from '@/components/artifacts/PanelControls'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal } from '@/components/ui'
import { ApiError, artifactsApi, errorMessage } from '@/lib/api'
import { guardHwpEdits } from '@/lib/hwpGuard'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { HwpArtifact } from '@/types'

/** rhwp-studio, built into this image (`rhwp/build-studio.sh`): the file never leaves the site. */
export const STUDIO_URL = '/rhwp/'
/** How often the editor is asked whether it holds unsaved edits. */
const DIRTY_POLL_MS = 1500

const ZIP = [0x50, 0x4b, 0x03, 0x04]

function isHwpx(bytes: ArrayBuffer): boolean {
  const head = new Uint8Array(bytes, 0, Math.min(4, bytes.byteLength))
  return ZIP.every((byte, i) => head[i] === byte)
}

function stem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'document'
}

type Ask = (go: boolean) => void

/**
 * An HWP document in the rhwp editor (menus, toolbar and all), beside the chat. The editor
 * loads the saved version; the person's 저장 adds a version from the one loaded, and the
 * agent's tools add theirs on the server, which the editor then loads. The iframe is
 * mounted once per document: moving it in the DOM would reload it and lose its state.
 */
export function HwpPanel({
  doc,
  onClose,
  onModeChange,
}: {
  doc: HwpArtifact
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)
  const phone = useMediaQuery('(max-width: 40rem)')
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<RhwpEditor | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState('')
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
  const [history, setHistory] = useState<{ nonce: number } | null>(null)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const noteArtifactVersion = useStore((s) => s.noteArtifactVersion)
  const agentWorking = useStore((s) => !!doc.sessionId && !!s.running[doc.sessionId])

  const markDirty = (value: boolean) => {
    dirtyRef.current = value
    setDirty(value)
  }

  useEffect(() => {
    let gone = false
    let made: RhwpEditor | null = null
    createEditor(host.current!, { studioUrl: STUDIO_URL })
      .then((instance) => {
        if (gone) {
          instance.destroy()
          return
        }
        made = instance
        editor.current = instance
        instance.element.title = t('한글 문서 편집기')
        setReady(true)
      })
      .catch((err: unknown) => {
        if (!gone) setFailed(err instanceof Error ? err.message : String(err))
      })
    return () => {
      gone = true
      made?.destroy()
      editor.current = null
    }
    // One editor per document; the panel is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** The saved version into the editor; an `.hwp` is saved back once as HWPX. */
  const load = useCallback(async () => {
    const instance = editor.current
    if (!instance || busy.current) return
    busy.current = true
    setLoading(true)
    setError('')
    try {
      const file = await artifactsApi.hwpFile(doc.id)
      const hwpx = isHwpx(file.bytes)
      await instance.loadFile(file.bytes, hwpx ? 'document.hwpx' : 'document.hwp', {
        skipUnsavedGuard: true,
      })
      let version = file.version
      if (!hwpx) {
        // what the tools and the next save work on
        const converted = await instance.exportHwpx()
        const row = await artifactsApi.saveHwp(doc.id, converted, version, 'HWPX로 변환')
        await instance.notifySaved().catch(() => undefined)
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
  }, [doc.id, refreshArtifact, t])

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

  useEffect(() => {
    if (!ready || loaded === null) return
    const timer = window.setInterval(() => {
      const instance = editor.current
      if (!instance || busy.current) return
      instance
        .getDocumentState()
        .then((state) => {
          if (state.dirty !== dirtyRef.current) markDirty(state.dirty)
        })
        .catch(() => undefined)
    }, DIRTY_POLL_MS)
    return () => window.clearInterval(timer)
  }, [ready, loaded])

  const save = useCallback(async (): Promise<boolean> => {
    const instance = editor.current
    if (!instance || loaded === null || busy.current) return false
    busy.current = true
    setSaving(true)
    setError('')
    try {
      const bytes = await instance.exportHwpx()
      const row = await artifactsApi.saveHwp(doc.id, bytes, loaded, '편집기에서 저장')
      await instance.notifySaved().catch(() => undefined)
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
  }, [doc.id, loaded, noteArtifactVersion, refreshArtifact, t])

  // Before an agent turn: unsaved hand edits are saved or dropped (`Composer`).
  useEffect(() => {
    if (!doc.sessionId) return
    return guardHwpEdits(doc.sessionId, {
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

  const download = async (format: 'hwpx' | 'hwp') => {
    const instance = editor.current
    if (!instance) return
    try {
      const bytes = format === 'hwpx' ? await instance.exportHwpx() : await instance.exportHwp()
      const type = format === 'hwpx' ? 'application/hwp+zip' : 'application/x-hwp'
      saveBlob(new Blob([bytes as BlobPart], { type }), `${stem(doc.title)}.${format}`)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다.')))
    }
  }

  const shown = loaded ?? doc.version
  return (
    <div className="flex h-full min-h-0 flex-col" data-hwp-panel={doc.id}>
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
            onClick={() => void save()}
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
          <MenuItem hint="HWPX" onClick={() => void download('hwpx')}>
            {t('한글 문서')}
          </MenuItem>
          <MenuItem hint="HWP" onClick={() => void download('hwp')}>
            {t('한글 97~2022 문서')}
          </MenuItem>
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
          onRestored={() => markDirty(false)}
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
              <Button size="sm" onClick={() => void download('hwpx')}>
                {t('내 편집 내려받기')}
              </Button>
              <Button size="sm" variant="primary" onClick={() => void load()}>
                {t('최신 판 불러오기')}
              </Button>
            </>
          )}
        </div>
      )}

      <div className="relative min-h-0 flex-1 bg-elevated">
        {/* The SDK appends its iframe here; nothing else may render inside this node. */}
        <div ref={host} className="absolute inset-0" />
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
            data-testid="hwp-agent-lock"
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
