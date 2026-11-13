import { createEditor, type RhwpEditor } from '@rhwp/editor'
import { useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import type { PanelMode } from '@/components/artifacts/PanelControls'
import { safePage } from '@/components/files/officePreview'
import { lazyPages, type LazyPages } from '@/components/hwp/lazyPages'
import { OfficePanel } from '@/components/office/OfficePanel'
import {
  fileStem, readOnlyAt, useOfficeDocument, type OfficeEditor,
} from '@/components/office/useOfficeDocument'
import { MenuItem } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { noteActivity } from '@/store/useStore'
import type { HwpArtifact } from '@/types'

/** rhwp-studio, built into this image (`rhwp/build-studio.sh`): the file never leaves the site. */
export const STUDIO_URL = '/rhwp/'
/** How often the editor is asked whether it holds unsaved edits (a send or a leave asks anew). */
const DIRTY_POLL_MS = 1500
/** A key or click in the editor asks again this soon after, so the unsaved mark follows typing. */
const DIRTY_AFTER_INPUT_MS = 200
/** Longer than this, and the editor is taken as not starting (said so, with a retry). */
export const START_TIMEOUT_MS = 60_000

const ZIP = [0x50, 0x4b, 0x03, 0x04]

export function isHwpx(bytes: ArrayBuffer): boolean {
  const head = new Uint8Array(bytes, 0, Math.min(4, bytes.byteLength))
  return ZIP.every((byte, i) => head[i] === byte)
}

/** `createEditor`, or a failure once `ms` pass; an editor that comes up later is closed. */
function startEditor(host: HTMLElement, ms: number, late: string): Promise<RhwpEditor> {
  return new Promise((resolve, reject) => {
    let settled = false
    const timer = window.setTimeout(() => {
      settled = true
      reject(new Error(late))
    }, ms)
    createEditor(host, { studioUrl: STUDIO_URL }).then(
      (editor) => {
        window.clearTimeout(timer)
        if (settled) editor.destroy()
        else resolve(editor)
      },
      (err: unknown) => {
        window.clearTimeout(timer)
        if (!settled) reject(err)
      },
    )
  })
}

/**
 * The document's pages as the editor draws them, to read on a phone: the editor itself stays
 * mounted (loads, downloads and the agent's versions go through it) but takes no input. Every
 * page is there, each drawn as the reader scrolls to it (`lazyPages`).
 */
function HwpPages({ editor, version }: { editor: RhwpEditor; version: number }) {
  const t = useT()
  const pages = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const sheets = pages.current!
    let gone = false
    let laid: LazyPages | null = null
    const draw = async () => {
      const count = await editor.pageCount()
      if (gone) return
      laid = lazyPages(
        sheets,
        scroller.current,
        count,
        async (page) => safePage(await editor.getPageSvg(page, { profile: 'screen' })),
        (n) => t('{n}쪽').replace('{n}', String(n)),
      )
    }
    draw().catch(() => undefined)
    return () => {
      gone = true
      laid?.stop()
    }
  }, [editor, version, t])
  return (
    <div ref={scroller} className="absolute inset-0 z-[1] overflow-auto bg-elevated" data-hwp-pages="">
      <div ref={pages} className="flex flex-col items-center gap-3 p-2" />
    </div>
  )
}

/**
 * An HWP document in the rhwp editor (menus, toolbar and all), beside the chat; versions,
 * saves and the agent's turns as every office document has them (`OfficePanel`). The iframe
 * is mounted once per document: moving it in the DOM would reload it and lose its state.
 * Ctrl+S in the editor is the header's 저장. On a phone it is read only: its pages, drawn.
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
  const phone = useMediaQuery('(max-width: 40rem)')
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<RhwpEditor | null>(null)
  const office = useRef<OfficeEditor | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState('')
  /** Each start of the editor; a retry starts another. */
  const [attempt, setAttempt] = useState(0)
  const state = useOfficeDocument('hwp', doc, office, ready, t('HWPX로 변환'))
  const { busy, dirtyRef, markDirty, loaded, setError, dirty } = state
  const readOnly = readOnlyAt(phone, dirty)
  // the editor's listeners are set once; this keeps what they read current
  const latest = useRef({
    save: state.save, refresh: state.refresh, agentWorking: state.agentWorking, readOnly,
  })
  useEffect(() => {
    latest.current = {
      save: state.save, refresh: state.refresh, agentWorking: state.agentWorking, readOnly,
    }
  })

  useEffect(() => {
    let gone = false
    let made: RhwpEditor | null = null
    let unlisten = () => {}
    setFailed('')

    /**
     * The editor's frame is this site's own page: what happens in it is seen here. A key or
     * click there is activity (the idle sign-out watches only this window), asks the editor
     * soon after whether it holds unsaved edits, and Ctrl+S is the header's 저장.
     */
    const listenInside = (frame: HTMLIFrameElement) => {
      let soon = 0
      let inner: Window | null = null
      const input = () => {
        noteActivity()
        window.clearTimeout(soon)
        soon = window.setTimeout(
          () => void latest.current.refresh().catch(() => undefined),
          DIRTY_AFTER_INPUT_MS,
        )
      }
      const key = (event: KeyboardEvent) => {
        input()
        if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 's') return
        // before the studio's own handlers: not its download, not the browser's 「save page」
        event.preventDefault()
        event.stopImmediatePropagation()
        const now = latest.current
        if (now.readOnly || now.agentWorking) return
        void now.refresh().then(() => (dirtyRef.current ? now.save() : false))
      }
      const detach = () => {
        try {
          inner?.removeEventListener('keydown', key, true)
          inner?.removeEventListener('pointerdown', input, true)
          inner?.removeEventListener('wheel', noteActivity, true)
        } catch {
          /* gone with its page */
        }
        inner = null
      }
      const attach = () => {
        detach()
        try {
          inner = frame.contentWindow
          inner?.addEventListener('keydown', key, true)
          inner?.addEventListener('pointerdown', input, true)
          inner?.addEventListener('wheel', noteActivity, { capture: true, passive: true })
        } catch {
          inner = null // not this site's page: nothing to hear
        }
      }
      attach()
      // the studio reloading its page drops the listeners: put them back
      frame.addEventListener('load', attach)
      return () => {
        window.clearTimeout(soon)
        frame.removeEventListener('load', attach)
        detach()
      }
    }

    startEditor(
      host.current!,
      START_TIMEOUT_MS,
      t('편집기가 {n}초 안에 열리지 않았습니다.').replace('{n}', String(START_TIMEOUT_MS / 1000)),
    )
      .then((instance) => {
        if (gone) {
          instance.destroy()
          return
        }
        made = instance
        editor.current = instance
        office.current = {
          // an `.hwp` is turned into HWPX by the editor, and saved back so
          load: async (bytes) => {
            const hwpx = isHwpx(bytes)
            await instance.loadFile(bytes, hwpx ? 'document.hwpx' : 'document.hwp', {
              skipUnsavedGuard: true,
            })
            return !hwpx
          },
          export: () => instance.exportHwpx(),
          saved: () => instance.notifySaved(),
          // the editor counts its own changes: a save knows of typing while it ran
          revision: async () => {
            const found = await instance.getDocumentState()
            return found.documentEpoch * 1e9 + found.changeSeq
          },
          isDirty: async () => (await instance.getDocumentState()).dirty,
        }
        instance.element.title = t('한글 문서 편집기')
        unlisten = listenInside(instance.element)
        setReady(true)
      })
      .catch((err: unknown) => {
        if (!gone) setFailed(err instanceof Error ? err.message : String(err))
      })

    return () => {
      gone = true
      unlisten()
      made?.destroy()
      editor.current = null
      office.current = null
      setReady(false)
    }
    // One editor per document (the panel is keyed by it), and another on each retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])

  useEffect(() => {
    if (!ready || loaded === null) return
    const timer = window.setInterval(() => {
      const instance = editor.current
      if (!instance || busy.current) return
      instance
        .getDocumentState()
        .then((found) => {
          if (found.dirty !== dirtyRef.current && !busy.current) markDirty(found.dirty)
        })
        .catch(() => undefined)
    }, DIRTY_POLL_MS)
    return () => window.clearInterval(timer)
  }, [ready, loaded, busy, dirtyRef, markDirty])

  // read only: the frame under the drawn pages takes no keys or clicks
  useEffect(() => {
    const frame = editor.current?.element
    if (!frame) return
    frame.inert = readOnly
    frame.setAttribute('aria-hidden', String(readOnly))
  }, [readOnly, ready])

  const download = async (format: 'hwpx' | 'hwp') => {
    const instance = editor.current
    if (!instance) return
    try {
      const bytes = format === 'hwpx' ? await instance.exportHwpx() : await instance.exportHwp()
      const type = format === 'hwpx' ? 'application/hwp+zip' : 'application/x-hwp'
      saveBlob(new Blob([bytes as BlobPart], { type }), `${fileStem(doc.title)}.${format}`)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다.')))
    }
  }

  return (
    <OfficePanel
      kind="hwp"
      doc={doc}
      state={state}
      ready={ready}
      failed={failed}
      phone={phone}
      onRetry={() => setAttempt((n) => n + 1)}
      onDownloadEdits={() => void download('hwpx')}
      onClose={onClose}
      onModeChange={onModeChange}
      downloads={
        <>
          <MenuItem hint="HWPX" onClick={() => void download('hwpx')}>
            {t('한글 문서')}
          </MenuItem>
          <MenuItem hint="HWP" onClick={() => void download('hwp')}>
            {t('한글 97~2022 문서')}
          </MenuItem>
        </>
      }
    >
      {/* The SDK appends its iframe here; nothing else may render inside this node. */}
      <div ref={host} key={attempt} className="absolute inset-0" />
      {readOnly && ready && loaded !== null && editor.current && (
        <HwpPages editor={editor.current} version={loaded} />
      )}
    </OfficePanel>
  )
}
