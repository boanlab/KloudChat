import { DocxEditor, type DocxEditorRef } from '@stll/folio-react'
import { messages as FOLIO_EN } from '@stll/folio-react/messages/en'
import '@stll/folio-react/editor.css'
import { useEffect, useRef, useState } from 'react'
import { IntlProvider } from 'use-intl'
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
import { useStore } from '@/store/useStore'
import type { DocxArtifact } from '@/types'
import { FOLIO_KO } from './folioKo'

const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

/**
 * A Word document in the folio editor (pages, toolbar, tables, headers and footers), beside
 * the chat; versions, saves and the agent's turns as every office document has them
 * (`OfficePanel`). Its own chunk: the editor loads only when a Word document opens. On a
 * phone it is read only.
 */
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
  const lang = useStore((s) => s.lang)
  const author = useStore((s) => s.user?.name ?? '')
  const phone = useMediaQuery('(max-width: 40rem)')
  const editor = useRef<DocxEditorRef>(null)
  const office = useRef<OfficeEditor | null>(null)
  const [ready, setReady] = useState(false)
  const state = useOfficeDocument('docx', doc, office, ready)
  const { busy, markDirty, setError } = state

  useEffect(() => {
    const instance = editor.current
    if (!instance) return
    office.current = {
      load: async (bytes) => {
        await instance.loadDocumentBuffer(bytes)
        return false
      },
      export: async () => {
        const bytes = await instance.save()
        if (!bytes) throw new Error(t('문서를 만들지 못했습니다.'))
        return new Uint8Array(bytes)
      },
      saved: async () => undefined,
    }
    setReady(true)
    return () => {
      office.current = null
    }
    // One editor per document; the panel is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const download = async () => {
    try {
      const bytes = await office.current!.export()
      const blob = new Blob([bytes as BlobPart], { type: OFFICE_MIME.docx })
      saveBlob(blob, `${fileStem(doc.title)}.docx`)
    } catch (err) {
      setError(errorMessage(err, t('내려받지 못했습니다.')))
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
      <div className="absolute inset-0 bg-white">
        <IntlProvider
          locale={lang}
          messages={lang === 'en' ? FOLIO_EN : FOLIO_KO}
          timeZone={TIME_ZONE}
        >
          <DocxEditor
            ref={editor}
            accessibleName={t('Word 문서 편집기')}
            author={author}
            readOnly={phone}
            initialZoom="fit-width"
            style={{ height: '100%' }}
            onChange={() => {
              // a load is not an edit
              if (!busy.current) markDirty(true)
            }}
            onError={(err) => setError(errorMessage(err, t('문서를 불러오지 못했습니다.')))}
          />
        </IntlProvider>
      </div>
    </OfficePanel>
  )
}
