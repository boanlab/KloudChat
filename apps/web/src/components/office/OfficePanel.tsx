import { FileType, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { usePanelWidth, type PanelMode } from '@/components/artifacts/PanelControls'
import {
  DocumentAsk,
  DocumentHeader,
  DocumentLock,
  DocumentNotice,
  DocumentReadOnlyNote,
} from '@/components/document/DocumentChrome'
import type { OfficeKind } from '@/lib/api'
import { useT } from '@/lib/useT'
import { readOnlyAt, type Doc, type OfficeDocument } from './useOfficeDocument'

/**
 * The panel around an office editor: title, version, unsaved mark, 저장, the download menu,
 * sharing and version history, the notices (with a retry when the editor or the file did not
 * open), the lock while the agent works, and the question over unsaved edits before a turn
 * is sent or the document is left. `children` is the editor's area.
 */
export function OfficePanel({
  kind,
  doc,
  state,
  ready,
  failed,
  phone,
  onRetry,
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
  /** Starts the editor again after it failed to (`failed`). */
  onRetry?: () => void
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
  const { dirty, saving, agentWorking, loaded, loading, notice, error, asking, answer } = state
  const shown = loaded ?? doc.version
  const readOnly = readOnlyAt(phone, dirty)
  // the first load failed: nothing is shown, so it is offered again
  const reload = !failed && !notice && !!error && loaded === null
  const panel = { [`data-${kind}-panel`]: doc.id }
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden" {...panel}>
      <DocumentHeader
        icon={<FileType size={15} className="shrink-0 text-accent" />}
        title={doc.title}
        version={shown}
        dirty={dirty}
        saving={saving}
        canSave={!agentWorking && ready}
        showSave={!readOnly}
        compact={phone}
        onSave={() => void state.save()}
        downloads={downloads}
        downloadDisabled={!ready || loaded === null}
        artifact={{ id: doc.id, title: doc.title, version: doc.version }}
        history={{ hasUnsavedChanges: dirty, onRestored: () => state.markDirty(false) }}
        mode={width.mode}
        onCycle={phone ? undefined : width.cycle}
        onClose={onClose}
      />

      <DocumentNotice
        notice={failed ? null : notice}
        message={failed ? t('편집기를 띄우지 못했습니다: {reason}').replace('{reason}', failed) : error}
        onDownloadEdits={onDownloadEdits}
        onLoadLatest={() => void state.load()}
        action={
          failed && onRetry
            ? { label: t('다시 시도'), run: onRetry }
            : reload
              ? { label: t('다시 시도'), run: () => void state.load() }
              : undefined
        }
      />

      {phone && <DocumentReadOnlyNote readOnly={readOnly} testId="office-read-only" />}

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
        {agentWorking && ready && <DocumentLock testId={`${kind}-agent-lock`} />}
      </div>

      <DocumentAsk reason={asking?.reason ?? null} onAnswer={(choice) => void answer(choice)} />
    </div>
  )
}
