import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
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

export interface Doc {
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

/** A file name from a document title. */
export function fileStem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'document'
}
