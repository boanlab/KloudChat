import { useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import type { PanelMode } from '@/components/artifacts/PanelControls'
import { OfficePanel } from '@/components/office/OfficePanel'
import {
  fileStem, useOfficeDocument, type OfficeEditor,
} from '@/components/office/useOfficeDocument'
import { MenuItem } from '@/components/ui'
import { errorMessage, OFFICE_MIME } from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import type { PptxArtifact } from '@/types'
import { SlidesEditor, type SlidesApi } from './SlidesEditor'

/**
 * A PowerPoint deck beside the chat: pptx-react-viewer's slide canvas under this app's own
 * Google-Slides-like chrome (`SlidesEditor`); versions, saves and the agent's turns as every
 * office document has them (`OfficePanel`). Its own chunk: the editor loads only when a deck
 * opens. Ctrl+S is the header's 저장. On a phone it is read only.
 */
export function PptxPanel({
  doc,
  onClose,
  onModeChange,
}: {
  doc: PptxArtifact
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const phone = useMediaQuery('(max-width: 40rem)')
  const host = useRef<HTMLDivElement>(null)
  const slides = useRef<SlidesApi | null>(null)
  const office = useRef<OfficeEditor | null>(null)
  /** The file the editor shows, and a number that changes with each one. */
  const [file, setFile] = useState<{ bytes: Uint8Array; version: number; slide: number } | null>(
    null,
  )
  /** The slide the person is on: a new version opens there. */
  const slideAt = useRef(0)
  const [failed, setFailed] = useState('')
  /** Settles the load in progress once the editor has drawn the deck (or failed to). */
  const opened = useRef<{ resolve: () => void; reject: (error: Error) => void } | null>(null)
  const [ready, setReady] = useState(false)
  const state = useOfficeDocument('pptx', doc, office, ready)
  const { busy, dirtyRef, markDirty, setError } = state
  const fileName = `${fileStem(doc.title)}.pptx`
  const latest = useRef({ save: state.save, agentWorking: state.agentWorking, phone, t })
  useEffect(() => {
    latest.current = { save: state.save, agentWorking: state.agentWorking, phone, t }
  })

  useEffect(() => {
    let loads = 0
    office.current = {
      load: async (bytes) => {
        const focused = document.activeElement
        const done = new Promise<void>((resolve, reject) => {
          opened.current = { resolve, reject }
        })
        loads += 1
        setFailed('')
        setFile({ bytes: new Uint8Array(bytes), version: loads, slide: slideAt.current })
        await done
        if (focused instanceof HTMLElement && focused.isConnected &&
          !host.current?.contains(focused)) {
          // the agent's version came in while the person was in the chat: keep their focus there
          focused.focus()
        }
        return false
      },
      export: async () => {
        if (!slides.current) throw new Error(latest.current.t('편집기가 아직 열리지 않았습니다.'))
        return slides.current.getContent()
      },
      saved: async () => slides.current?.markClean(),
    }
    setReady(true)
    return () => {
      office.current = null
    }
  }, [])

  useEffect(() => {
    const el = host.current
    if (!el) return undefined
    // Ctrl+S in the editor is the header's 저장, not the browser's 「save page」
    const saveKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 's') return
      event.preventDefault()
      const now = latest.current
      if (!now.phone && !now.agentWorking && dirtyRef.current) void now.save()
    }
    el.addEventListener('keydown', saveKey)
    return () => el.removeEventListener('keydown', saveKey)
  }, [dirtyRef])

  const download = async () => {
    try {
      const bytes = await office.current!.export()
      saveBlob(new Blob([bytes as BlobPart], { type: OFFICE_MIME.pptx }), fileName)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다.')))
    }
  }

  return (
    <OfficePanel
      kind="pptx"
      doc={doc}
      state={state}
      ready={ready && file !== null && !failed}
      failed={failed}
      phone={phone}
      onDownloadEdits={() => void download()}
      onClose={onClose}
      onModeChange={onModeChange}
      downloads={
        <MenuItem hint="PPTX" onClick={() => void download()}>
          {t('PowerPoint 문서')}
        </MenuItem>
      }
    >
      <div ref={host} data-pptx-editor="" className="absolute inset-0">
        {file && (
          <SlidesEditor
            content={file.bytes}
            version={file.version}
            initialSlide={file.slide}
            onSlide={(index) => {
              slideAt.current = index
            }}
            canEdit={!phone}
            label={t('PowerPoint 편집기')}
            apiRef={slides}
            onDirty={(dirty) => {
              // a load is not an edit
              if (!busy.current || !dirty) markDirty(dirty)
            }}
            onReady={() => {
              opened.current?.resolve()
              opened.current = null
            }}
            onError={(message) => {
              setFailed(message)
              opened.current?.reject(new Error(message))
              opened.current = null
            }}
          />
        )}
      </div>
    </OfficePanel>
  )
}
