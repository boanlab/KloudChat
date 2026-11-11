import { createEditor, type RhwpEditor } from '@rhwp/editor'
import { useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import type { PanelMode } from '@/components/artifacts/PanelControls'
import {
  fileStem, OfficePanel, useOfficeDocument, type OfficeEditor,
} from '@/components/office/OfficePanel'
import { MenuItem } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
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

/**
 * An HWP document in the rhwp editor (menus, toolbar and all), beside the chat; versions,
 * saves and the agent's turns as every office document has them (`OfficePanel`). The iframe
 * is mounted once per document: moving it in the DOM would reload it and lose its state.
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
  const state = useOfficeDocument('hwp', doc, office, ready, 'HWPX로 변환')
  const { busy, dirtyRef, markDirty, loaded, setError } = state

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
        }
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
      office.current = null
    }
    // One editor per document; the panel is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!ready || loaded === null) return
    const timer = window.setInterval(() => {
      const instance = editor.current
      if (!instance || busy.current) return
      instance
        .getDocumentState()
        .then((found) => {
          if (found.dirty !== dirtyRef.current) markDirty(found.dirty)
        })
        .catch(() => undefined)
    }, DIRTY_POLL_MS)
    return () => window.clearInterval(timer)
  }, [ready, loaded, busy, dirtyRef, markDirty])

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
      <div ref={host} className="absolute inset-0" />
    </OfficePanel>
  )
}
