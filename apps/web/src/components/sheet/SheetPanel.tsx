import { useEffect, useRef, useState } from 'react'
import { saveBlob } from '@/components/artifacts/artifactExport'
import type { PanelMode } from '@/components/artifacts/PanelControls'
import { OfficePanel } from '@/components/office/OfficePanel'
import {
  fileStem, readOnlyAt, useOfficeDocument, type OfficeEditor,
} from '@/components/office/useOfficeDocument'
import { MenuItem } from '@/components/ui'
import { artifactsApi, errorMessage, XLSX_MIME } from '@/lib/api'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { XlsxArtifact } from '@/types'
import { mountSheetEditor, type SheetEditorApi } from './SheetEditor'

/** Whether the app is drawn dark (the `dark` class of the page, which the theme toggle and the system setting set). */
function useDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    const root = document.documentElement
    const watch = new MutationObserver(() => setDark(root.classList.contains('dark')))
    watch.observe(root, { attributes: true, attributeFilter: ['class'] })
    return () => watch.disconnect()
  }, [])
  return dark
}

/** An Excel sheet beside the chat in the Univer grid; read only on a phone unless unsaved edits exist. Own chunk. */
export function SheetPanel({
  doc,
  onClose,
  onModeChange,
}: {
  doc: XlsxArtifact
  onClose?: () => void
  onModeChange?: (mode: PanelMode) => void
}) {
  const t = useT()
  const lang = useStore((s) => s.lang)
  const dark = useDark()
  const phone = useMediaQuery('(max-width: 40rem)')
  const host = useRef<HTMLDivElement>(null)
  const grid = useRef<SheetEditorApi | null>(null)
  const office = useRef<OfficeEditor | null>(null)
  const [ready, setReady] = useState(false)
  /** The grid has drawn a version (set after each load); tests wait for it before they type. */
  const [drawn, setDrawn] = useState(false)
  const state = useOfficeDocument('xlsx', doc, office, ready)
  const { dirtyRef, noteEdit, setError } = state
  const readOnly = readOnlyAt(phone, state.dirty)
  const fileName = `${fileStem(doc.title)}.xlsx`
  // the grid's callbacks are set once; this keeps what they read current
  const latest = useRef({ save: state.save, agentWorking: state.agentWorking, readOnly, dark, t })
  useEffect(() => {
    latest.current = { save: state.save, agentWorking: state.agentWorking, readOnly, dark, t }
  })

  useEffect(() => {
    const el = host.current
    if (!el) return undefined
    const editor = mountSheetEditor(el, {
      lang: lang === 'en' ? 'en' : 'ko',
      editable: !latest.current.readOnly,
      dark: latest.current.dark,
      // a load is not an edit (`noteEdit` knows)
      onEdit: () => noteEdit(),
      onDrawn: () => setDrawn(true),
    })
    grid.current = editor
    // Ctrl+S in the grid is the header's 저장, not the browser's 「save page」
    const saveKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 's') return
      event.preventDefault()
      const now = latest.current
      if (!now.readOnly && !now.agentWorking && dirtyRef.current) void now.save()
    }
    el.addEventListener('keydown', saveKey)
    office.current = {
      load: async () => {
        throw new Error('a sheet is shown from its model')
      },
      export: async () => {
        throw new Error('a sheet is saved as cell changes')
      },
      saved: async () => undefined,
      sheet: {
        show: (model) => editor.show(model),
        changes: () => editor.changes(),
      },
    }
    setReady(true)
    return () => {
      el.removeEventListener('keydown', saveKey)
      office.current = null
      grid.current = null
      editor.destroy()
    }
    // One grid per document; the panel is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    grid.current?.setEditable(!readOnly)
  }, [readOnly, ready])
  useEffect(() => {
    grid.current?.setDark(dark)
  }, [dark, ready])

  /** The saved file, or (with unsaved edits, or after a lost save) the version read with the edits on it. */
  const download = async (withEdits: boolean) => {
    try {
      const base = state.loaded ?? doc.version
      const ops = withEdits ? grid.current?.changes().ops : undefined
      const blob = ops?.length
        ? await artifactsApi.sheetEditsFile(doc.id, base, ops)
        : new Blob([(await artifactsApi.documentFile('xlsx', doc.id, base)).bytes], { type: XLSX_MIME })
      saveBlob(blob, fileName)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다. 잠시 뒤 다시 시도하세요.')))
    }
  }

  return (
    <OfficePanel
      kind="xlsx"
      doc={doc}
      state={state}
      ready={ready}
      failed=""
      phone={phone}
      onDownloadEdits={() => void download(true)}
      onClose={onClose}
      onModeChange={onModeChange}
      downloads={
        <MenuItem hint="XLSX" onClick={() => void download(state.dirty)}>
          {t('Excel 문서')}
        </MenuItem>
      }
    >
      <div
        ref={host}
        role="region"
        aria-label={t('시트 편집기')}
        data-sheet-editor=""
        data-sheet-ready={String(drawn)}
        className="absolute inset-0 bg-white"
      />
    </OfficePanel>
  )
}
