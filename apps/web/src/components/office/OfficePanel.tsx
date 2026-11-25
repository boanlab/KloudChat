import { FileType, Loader2, Table2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { usePanelWidth, type PanelMode } from '@/components/artifacts/PanelControls'
import {
  DocumentAsk,
  DocumentHeader,
  DocumentLock,
  DocumentNotice,
  DocumentReadOnlyNote,
} from '@/components/document/DocumentChrome'
import { ReadAloudButton } from '@/components/chat/ReadAloudButton'
import type { OfficeKind } from '@/lib/api'
import { documentUnits } from '@/lib/readUnits'
import { useT } from '@/lib/useT'
import { readOnlyAt, type Doc, type OfficeDocument } from './useOfficeDocument'

/** Shared chrome of an office editor (title, versions, save, download, share, agent lock, unsaved-edit prompt); `children` is the editor area. */
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
  readText,
  children,
}: {
  kind: OfficeKind | 'xlsx'
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
  /** The saved document's text as the server read it; with it the header offers 「읽어 주기」. */
  readText?: string
  children: ReactNode
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)
  const { dirty, saving, agentWorking, loaded, loading, notice, error, asking, answer } = state
  const shown = loaded ?? doc.version
  const cut = kind === 'xlsx' && state.cut
  const readOnly = readOnlyAt(phone, dirty) || cut
  // the first load failed: nothing is shown, so it is offered again
  const reload = !failed && !notice && !!error && loaded === null
  const panel = { [`data-${kind}-panel`]: doc.id }
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden" {...panel}>
      <DocumentHeader
        icon={kind === 'xlsx'
          ? <Table2 size={15} className="shrink-0 text-accent" />
          : <FileType size={15} className="shrink-0 text-accent" />}
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
        extra={readText ? <ReadAloudButton id={`artifact:${doc.id}`} units={() => documentUnits(doc.title, readText)} compact={phone} /> : undefined}
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

      {cut && (
        <DocumentReadOnlyNote
          readOnly
          testId="sheet-cut-note"
          text={t('칸이 아주 많아 일부만 보여 드립니다. 보이지 않는 칸이 어긋나지 않도록 읽기만 합니다. 고칠 내용은 대화에서 에이전트에게 부탁하세요.')}
        />
      )}
      {!cut && kind === 'xlsx' && dirty && (
        <DocumentReadOnlyNote
          readOnly={false}
          testId="sheet-unsaved-kinds-note"
          text={t('행 높이·시트 순서·숨김·틀 고정을 바꾼 것은 이 편집기에서 저장되지 않습니다.')}
        />
      )}
      {phone && !cut && <DocumentReadOnlyNote readOnly={readOnly} testId="office-read-only" />}

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
