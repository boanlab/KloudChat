import { mountDocxEditor, type DocxEditorApi } from 'open-docx-editor'
import 'open-docx-editor/style.css'
import 'open-docx-editor/fonts.css'
// stand-in for Korean sans fonts the computer lacks (koreanFonts.ts)
import '@fontsource/nanum-gothic/400.css'
import '@fontsource/nanum-gothic/700.css'
import { useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import type { PanelMode } from '@/components/artifacts/PanelControls'
import { OfficePanel } from '@/components/office/OfficePanel'
import {
  fileStem, readOnlyAt, useOfficeDocument, type OfficeEditor,
} from '@/components/office/useOfficeDocument'
import { MenuItem } from '@/components/ui'
import { errorMessage, OFFICE_MIME } from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { DocxArtifact } from '@/types'
import { aliasKoreanFonts } from './koreanFonts'

/** A Word document in open-docx-editor beside the chat; read only on a phone unless unsaved edits exist. Own chunk. */
export function DocxPanel({
  doc,
  onClose,
  onModeChange,
}: {
  doc: DocxArtifact
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const author = useStore((s) => s.user?.name ?? '')
  // ribbon language (open-docx-editor `locale`, patched); set on mount
  const lang = useStore((s) => s.lang)
  const phone = useMediaQuery('(max-width: 40rem)')
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<DocxEditorApi | null>(null)
  const office = useRef<OfficeEditor | null>(null)
  const [ready, setReady] = useState(false)
  const state = useOfficeDocument('docx', doc, office, ready)
  const { dirtyRef, noteEdit, setError } = state
  const readOnly = readOnlyAt(phone, state.dirty)
  const fileName = `${fileStem(doc.title)}.docx`
  // the editor's callbacks are set once; this keeps what they read current
  const latest = useRef({ save: state.save, agentWorking: state.agentWorking, readOnly, t })
  useEffect(() => {
    latest.current = { save: state.save, agentWorking: state.agentWorking, readOnly, t }
  })

  useEffect(() => {
    const el = host.current
    if (!el) return
    aliasKoreanFonts()
    const api = mountDocxEditor(el, {
      author,
      fileName,
      readOnly,
      locale: lang === 'en' ? 'en' : 'ko',
      // one artifact per panel: no File > New or Open
      features: { newDocument: false, openFile: false },
      // a load is not an edit (`noteEdit` knows); one made while a save runs stays unsaved
      onChange: () => noteEdit(),
      // File > Create a Copy: a download, as the header's menu gives
      onSave: (bytes, name) =>
        saveBlob(new Blob([bytes as BlobPart], { type: OFFICE_MIME.docx }), name),
      onError: (err) =>
        setError(errorMessage(err, latest.current.t('문서를 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.'))),
    })
    api.setZoom('pageWidth')
    editor.current = api
    // Ctrl+S in the editor is the header's 저장, not the browser's 「save page」
    const saveKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 's') return
      event.preventDefault()
      const now = latest.current
      if (!now.readOnly && !now.agentWorking && dirtyRef.current) void now.save()
    }
    el.addEventListener('keydown', saveKey)
    // File > Export / Print: the app tree is not printed (index.css), so print-laid-out pages are copied beside it until the dialog closes.
    let printed: HTMLElement | null = null
    const afterPrint = () => {
      printed?.remove()
      printed = null
    }
    const beforePrint = () => {
      afterPrint() // one copy, however often it is asked for
      const sheet = el.querySelector('.odx-page')
      if (!sheet || document.querySelector('body > .odx-print-pages')) return
      printed = document.createElement('div')
      printed.className = 'odx-canvas'
      printed.setAttribute('data-print-docx', '')
      printed.append(sheet.cloneNode(true))
      document.body.append(printed)
    }
    /** After the editor's own listeners, which lay the pages out for print: each open adds them anew. */
    const armPrint = () => {
      window.removeEventListener('beforeprint', beforePrint)
      window.addEventListener('beforeprint', beforePrint)
    }
    armPrint()
    window.addEventListener('afterprint', afterPrint)
    let opened = false
    office.current = {
      load: async (bytes) => {
        const focused = document.activeElement
        await api.open(new Uint8Array(bytes), fileName)
        armPrint()
        api.markSaved()
        if (focused instanceof HTMLElement && focused.isConnected && !el.contains(focused)) {
          // keep the focus in the chat when the agent's version arrives
          focused.focus()
        } else if (opened) {
          // a new version reset the caret to the top, where pending keystrokes would land
          const active = document.activeElement
          if (active instanceof HTMLElement && el.contains(active)) active.blur()
        }
        opened = true
        return false
      },
      export: async () => api.save(),
      saved: async () => api.markSaved(),
    }
    setReady(true)
    return () => {
      el.removeEventListener('keydown', saveKey)
      window.removeEventListener('beforeprint', beforePrint)
      window.removeEventListener('afterprint', afterPrint)
      afterPrint()
      office.current = null
      editor.current = null
      api.destroy()
    }
    // One editor per document; the panel is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const api = editor.current
    if (api && api.readOnly !== readOnly) api.setReadOnly(readOnly)
  }, [readOnly, ready])

  const download = async () => {
    try {
      const bytes = await office.current!.export()
      const blob = new Blob([bytes as BlobPart], { type: OFFICE_MIME.docx })
      saveBlob(blob, fileName)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다. 잠시 뒤 다시 시도하세요.')))
    }
  }

  return (
    <OfficePanel
      kind="docx"
      doc={doc}
      state={state}
      ready={ready}
      failed=""
      phone={phone}
      onDownloadEdits={() => void download()}
      onClose={onClose}
      onModeChange={onModeChange}
      downloads={
        <MenuItem hint="DOCX" onClick={() => void download()}>
          {t('Word 문서')}
        </MenuItem>
      }
    >
      {/* the ribbon on top, the pages scrolling under it */}
      <div
        ref={host}
        role="region"
        aria-label={t('Word 문서 편집기')}
        data-docx-editor=""
        className="absolute inset-0 flex flex-col bg-white [&>.odx-canvas]:min-h-0 [&>.odx-canvas]:flex-1"
      />
    </OfficePanel>
  )
}
