import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { ApiError, artifactsApi, errorMessage, type OfficeKind, type SheetModel, type SheetOp } from '@/lib/api'
import { guardEdits, type AskReason } from '@/lib/officeGuard'
import { useMarkUnsaved } from '@/lib/unsaved'
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
  /** Editor's own change count (HWP); else the panel counts `noteEdit` calls. A save clears the unsaved mark only if the count did not move meanwhile. */
  revision?: () => Promise<number>
  /** Asks the editor whether it holds unsaved edits, where its state is otherwise polled. */
  isDirty?: () => Promise<boolean>
  /** The sheet grid: it shows the server's model, and its edits go back as cell changes, not as a file. */
  sheet?: {
    show: (model: SheetModel) => Promise<void>
    /** What changed since it was shown or last saved; `commit` makes the state sent the new baseline. */
    changes: () => { ops: SheetOp[]; commit: () => void }
  }
}

export interface Doc {
  id: string
  title: string
  version: number
  sessionId?: string | null
}

type Ask = { resolve: (go: boolean) => void; reason: AskReason }

/** Office document state beside the chat; server-side agent versions are loaded unless that would drop hand edits. */
/** A save the server refused for a reason the person can act on, in words. */
export function saveFailure(err: unknown, t: (text: string) => string): string {
  if (err instanceof ApiError && err.status === 422) {
    const reasons: Record<string, string> = {
      bad_range: '시트의 범위를 벗어난 변경이 있어 저장하지 못했습니다. 표 안에서 다시 편집해 주세요.',
      bad_op: '저장할 수 없는 변경이 들어 있습니다. 새로고침해 다시 편집해 주세요.',
      not_a_xlsx_file: '이 파일은 엑셀 시트로 읽을 수 없어 저장하지 못했습니다.',
      last_sheet: '시트는 하나 이상 있어야 합니다.',
    }
    const reason = reasons[err.detail] ?? (/^(no_sheet|sheet_exists|bad_sheet_name)/.test(err.detail)
      ? '시트 이름이 맞지 않아 저장하지 못했습니다. 이름을 확인해 주세요.'
      : '')
    if (reason) return t(reason)
  }
  return errorMessage(err, t('저장하지 못했습니다. 잠시 뒤 다시 시도하세요.'))
}

export function useOfficeDocument(
  kind: OfficeKind | 'xlsx',
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
  /** A load or a save is running. */
  const busy = useRef(false)
  /** A load is running: what the editor reports meanwhile is the new file, not an edit. */
  const loadingRef = useRef(false)
  /** Edits reported via `noteEdit`; compared before and after a save */
  const edits = useRef(0)
  const [error, setError] = useState('')
  /** A sheet too large to draw whole: shown read only, since a save would not hold what is not drawn. */
  const [cut, setCut] = useState(false)
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

  /** An edit in the editor; one made while a save runs stays unsaved after it. */
  const noteEdit = useCallback(() => {
    if (loadingRef.current) return
    edits.current += 1
    markDirty(true)
  }, [markDirty])

  const revision = useCallback(async () => {
    const own = editor.current?.revision
    return own ? `${await own()}` : `${edits.current}`
  }, [editor])

  // A tab close or reload asks while edits are unsaved (read as the page goes: an answer to the
  // panel's question may have just dropped them).
  useMarkUnsaved(`office:${doc.id}`, dirty, () => dirtyRef.current)

  /** The saved version into the editor; another format is saved back once as the editable one. */
  const load = useCallback(async (): Promise<boolean> => {
    const instance = editor.current
    if (!instance || busy.current) return false
    busy.current = true
    loadingRef.current = true
    setLoading(true)
    setError('')
    try {
      let version: number
      let convert = false
      if (kind === 'xlsx' && instance.sheet) {
        const model = await artifactsApi.sheetModel(doc.id)
        setCut(Boolean(model.truncated))
        await instance.sheet.show(model)
        version = model.version
      } else {
        const file = await artifactsApi.documentFile(kind, doc.id)
        convert = await instance.load(file.bytes)
        version = file.version
      }
      if (convert && kind !== 'xlsx') {
        // what the tools and the next save work on
        const converted = await instance.export()
        const row = await artifactsApi.saveDocument(
          kind, doc.id, converted, version, convertedSummary,
        )
        await instance.saved().catch(() => undefined)
        version = row.version
        void refreshArtifact(doc.id)
      }
      setLoaded(version)
      markDirty(false)
      setNotice(null)
      return true
    } catch (err) {
      setError(errorMessage(err, t('문서를 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.')))
      return false
    } finally {
      busy.current = false
      loadingRef.current = false
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
      let row
      let exported: string
      if (kind === 'xlsx' && instance.sheet) {
        // the cells that changed since the version shown, not the whole file
        const change = instance.sheet.changes()
        exported = await revision()
        row = change.ops.length
          ? await artifactsApi.changeSheetCells(doc.id, loaded, change.ops, t('편집기에서 저장'))
          : { version: loaded }
        change.commit()
      } else {
        const bytes = await instance.export()
        // what the export itself sets off in the editor settles first: it is not an edit
        await new Promise((resolve) => window.setTimeout(resolve, 0))
        exported = await revision()
        row = await artifactsApi.saveDocument(kind as OfficeKind, doc.id, bytes, loaded, t('편집기에서 저장'))
      }
      setLoaded(row.version)
      setNotice(null)
      if ((await revision()) === exported) {
        await instance.saved().catch(() => undefined)
        markDirty(false)
      } else {
        // typed while the save ran: in the editor, not in the version just saved
        markDirty(true)
      }
      noteArtifactVersion(doc.id, row.version)
      void refreshArtifact(doc.id)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setNotice('conflict')
      else setError(saveFailure(err, t))
      return false
    } finally {
      busy.current = false
      setSaving(false)
    }
  }, [kind, doc.id, editor, loaded, markDirty, noteArtifactVersion, refreshArtifact, revision, t])

  /** The editor's own unsaved state, where the panel only polls it (HWP). */
  const refresh = useCallback(async () => {
    const ask = editor.current?.isDirty
    if (!ask || busy.current || loaded === null) return
    const found = await ask()
    if (found !== dirtyRef.current && !busy.current) markDirty(found)
  }, [editor, loaded, markDirty])

  // Before an agent turn (`Composer`) or leaving the document (`leaveOffice`): unsaved hand
  // edits are saved or dropped.
  const latest = useRef({ save, refresh })
  useEffect(() => {
    latest.current = { save, refresh }
  })
  useEffect(() => {
    return guardEdits(doc.sessionId, doc.id, {
      dirty: () => dirtyRef.current,
      refresh: () => latest.current.refresh(),
      ask: (reason) => new Promise<boolean>((resolve) => setAsking({ resolve, reason })),
      save: () => latest.current.save(),
    })
  }, [doc.sessionId, doc.id])

  const answer = async (choice: 'save' | 'discard' | 'cancel') => {
    const ask = asking
    setAsking(null)
    if (!ask) return
    const { resolve, reason } = ask
    if (choice === 'cancel') return resolve(false)
    if (choice === 'save') return resolve(await save())
    if (reason !== 'send') {
      // the document goes away: nothing to load back
      markDirty(false)
      return resolve(true)
    }
    // the agent works on the saved file: the editor shows it again, or the turn waits
    resolve(await load())
  }

  return {
    loaded, loading, dirty, dirtyRef, markDirty, noteEdit, saving, busy, loadingRef, error, cut,
    setError, notice, load, save, refresh, asking, answer, agentWorking,
  }
}

export type OfficeDocument = ReturnType<typeof useOfficeDocument>

/** Phone width is read-only, unless edits made at a wider width are still unsaved. */
export function readOnlyAt(phone: boolean, dirty: boolean) {
  return phone && !dirty
}

/** A file name from a document title. */
export function fileStem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'document'
}
