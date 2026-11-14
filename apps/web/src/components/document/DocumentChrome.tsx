import { Download, Eye, History, Loader2, MoreHorizontal, RotateCcw, Save, Share2 } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { PanelControls, type PanelMode } from '@/components/artifacts/PanelControls'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { ShareButton } from '@/components/share/ShareButton'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal } from '@/components/ui'
import type { AskReason } from '@/lib/officeGuard'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'

/*
 * The chrome every document panel shares (웹 보고서, 한글, Word; the web slides next):
 * one header in one order — title · vN · 「저장 안 됨」 · 저장 · 내려받기 · 공유 · 더보기
 * (버전 기록) · expand · close — the conflict notice, the lock while the agent works,
 * the phone's read-only note and the question over unsaved edits. Editors bring their
 * own body; the state (dirty, saving, version, notice) is theirs and passed in.
 */

/** What the unsaved-edits question says, by why it is asked. */
export const ASK = {
  send: {
    description: '에이전트는 저장된 문서를 고칩니다. 지금 편집을 저장할까요, 버릴까요?',
    discard: '버리고 보내기',
    save: '저장하고 보내기',
  },
  leave: {
    description: '이 문서를 떠나면 저장하지 않은 편집이 사라집니다. 저장할까요?',
    discard: '버리기',
    save: '저장',
  },
  reload: {
    description: '새 버전을 쓰려면 페이지를 새로 고쳐야 합니다. 그 전에 지금 편집을 저장할까요?',
    discard: '버리기',
    save: '저장',
  },
} as const

/** `conflict`: a save lost to a newer version; `behind`: a newer version came in under edits. */
export type DocumentNoticeKind = 'conflict' | 'behind'

/**
 * The document header. Required: `icon`, `title`, `version`, `dirty`, `onSave`, `artifact`
 * (sharing and version history). `download` replaces the plain menu built from `downloads`;
 * `extra` sits after the unsaved mark (view switches, an 편집 toggle); `more` adds items to
 * 더보기 above 버전 기록; `children` (a ribbon) goes under the row.
 */
export function DocumentHeader({
  icon,
  title,
  version,
  dirty,
  saving = false,
  canSave = true,
  showSave = true,
  compact = false,
  onSave,
  downloads,
  downloadDisabled = false,
  download,
  artifact,
  historyRequest,
  history,
  extra,
  more,
  mode,
  onCycle,
  onClose,
  children,
  className,
}: {
  icon: ReactNode
  title: string
  version: number
  dirty: boolean
  saving?: boolean
  /** 저장 is enabled: something to save and nothing in the way. */
  canSave?: boolean
  /** False where the document is read only (a phone). */
  showSave?: boolean
  /** Phone width: icons keep their labels as accessible names. */
  compact?: boolean
  onSave: () => void
  /** The download menu's items (under 「형식 선택」). */
  downloads?: ReactNode
  downloadDisabled?: boolean
  /** A whole download control instead of `downloads`. */
  download?: ReactNode
  artifact: { id: string; title: string; version: number }
  /** Opens the version history from outside (a result card). */
  historyRequest?: { nonce: number; version?: number } | null
  /** Version history: unsaved edits, the current data, what to do after a restore. */
  history: { hasUnsavedChanges: boolean; onRestored?: () => void; currentData?: unknown }
  extra?: ReactNode
  more?: ReactNode
  mode: PanelMode
  onCycle?: () => void
  onClose?: () => void
  children?: ReactNode
  className?: string
}) {
  const t = useT()
  const [request, setRequest] = useState<{ nonce: number; version?: number } | null>(null)
  const [sharing, setSharing] = useState(false)
  // a read-only header at phone width (no 저장, icons only) fits one row
  const oneRow = Boolean(compact && !showSave)
  useEffect(() => {
    if (historyRequest) setRequest(historyRequest)
  }, [historyRequest])
  return (
    <header className={cn('relative z-40 flex items-center gap-1.5 border-b border-line bg-panel px-4 py-2.5 max-sm:px-2', oneRow ? 'flex-nowrap max-sm:gap-1' : 'flex-wrap', className)}>
      {icon}
      {/* keeps room to be read: the actions wrap to a second row first; at phone width they
          stay on one row (icons only) and the title gives way */}
      <p className={cn('flex-1 truncate text-base font-medium', oneRow ? 'min-w-0' : 'min-w-20 basis-20')} title={title}>{title}</p>
      <Badge>v{version}</Badge>
      {/* at phone width the line under the header says so */}
      {dirty && !compact && <span className="shrink-0 text-xs text-warn">{t('저장 안 됨')}</span>}
      {extra}
      {showSave && (
        <Button
          size="sm"
          variant={dirty ? 'primary' : 'secondary'}
          disabled={!dirty || saving || !canSave}
          aria-label={t('저장')}
          aria-keyshortcuts="Control+S Meta+S"
          onClick={onSave}
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          {!compact && t('저장')}
        </Button>
      )}
      {download ?? (
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={downloadDisabled} aria-label={t('내려받기')}>
              <Download size={14} />
              {!compact && t('내려받기')}
            </Button>
          )}
        >
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          {downloads}
        </Dropdown>
      )}
      {/* at phone width the header is full: sharing is in the 더보기 menu */}
      {!compact && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('이 아티팩트만 공유')}
          title={t('이 아티팩트만 공유')}
          onClick={() => setSharing(true)}
        >
          <Share2 size={15} />
        </Button>
      )}
      <ShareButton artifact={{ id: artifact.id, title: artifact.title }} open={sharing} onClose={() => setSharing(false)} />
      <Dropdown
        align="right"
        trigger={() => (
          <Button variant="ghost" size="icon" aria-label={t('더보기')}>
            <MoreHorizontal size={15} />
          </Button>
        )}
      >
        {compact && (
          <MenuItem onClick={() => setSharing(true)}>
            <Share2 size={14} />
            {t('이 아티팩트만 공유')}
          </MenuItem>
        )}
        {more}
        <MenuItem onClick={() => setRequest({ nonce: Date.now() })}>
          <History size={14} />
          {t('버전 기록')}
        </MenuItem>
      </Dropdown>
      <VersionHistory
        bare
        request={request}
        artifact={artifact}
        hasUnsavedChanges={history.hasUnsavedChanges}
        currentData={history.currentData}
        onRestored={history.onRestored}
      />
      <PanelControls mode={mode} onCycle={onCycle} onClose={onClose} />
      {children}
    </header>
  )
}

/** A save that lost, a version that came in under edits, or a failure, with what can be done. */
export function DocumentNotice({
  notice,
  message,
  onDownloadEdits,
  onLoadLatest,
  action,
}: {
  notice: DocumentNoticeKind | null
  /** A failure to show instead (`notice` empty). */
  message?: string
  onDownloadEdits: () => void
  onLoadLatest: () => void
  /** A retry for `message`. */
  action?: { label: string; run: () => void }
}) {
  const t = useT()
  if (!notice && !message) return null
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 border-b border-line bg-elevated px-4 py-2 text-sm">
      <span className="min-w-0 flex-1">
        {notice === 'conflict'
          ? t('그사이 다른 판이 저장되어 저장하지 않았습니다.')
          : notice === 'behind'
            ? t('에이전트가 새 판을 저장했습니다. 지금 편집은 아직 저장되지 않았습니다.')
            : message}
      </span>
      {action && (
        <Button size="sm" variant="primary" onClick={action.run}>
          <RotateCcw size={14} />
          {action.label}
        </Button>
      )}
      {notice && (
        <>
          <Button size="sm" onClick={onDownloadEdits}>
            {t('내 편집 내려받기')}
          </Button>
          <Button size="sm" variant="primary" onClick={onLoadLatest}>
            {t('최신 판 불러오기')}
          </Button>
        </>
      )}
    </div>
  )
}

/** The phone's note: read only, or read only once the unsaved edits are dealt with. */
export function DocumentReadOnlyNote({ readOnly, testId }: { readOnly: boolean; testId?: string }) {
  const t = useT()
  return (
    <p className="flex items-center gap-2 border-b border-line bg-elevated px-4 py-1.5 text-xs text-muted" data-testid={testId}>
      <Eye size={13} className="shrink-0" />
      {readOnly
        ? t('휴대폰에서는 읽기만 합니다. 고칠 내용은 대화에서 에이전트에게 부탁하세요.')
        : t('이 폭에서는 읽기만 합니다. 저장하지 않은 편집을 먼저 저장하거나 버리세요.')}
    </p>
  )
}

/** Over the editor while the agent works on the document. */
export function DocumentLock({ testId }: { testId?: string }) {
  const t = useT()
  return (
    <div className="absolute inset-0 z-10 grid place-items-center bg-panel/60 backdrop-blur-[1px]" data-testid={testId}>
      <span className="flex items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-sm shadow-sm">
        <Loader2 size={15} className="animate-spin text-accent" />
        {t('에이전트가 문서를 고치는 중…')}
      </span>
    </div>
  )
}

/** 저장 / 버리기 / 취소 over unsaved edits, worded by why it is asked. */
export function DocumentAsk({
  reason,
  onAnswer,
}: {
  /** Null: not asking. */
  reason: AskReason | null
  onAnswer: (choice: 'save' | 'discard' | 'cancel') => void
}) {
  const t = useT()
  const ask = ASK[reason ?? 'send']
  return (
    <Modal
      open={reason !== null}
      onClose={() => onAnswer('cancel')}
      title={t('저장하지 않은 편집이 있습니다')}
      description={t(ask.description)}
      width="max-w-md"
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={() => onAnswer('cancel')}>{t('취소')}</Button>
          <Button onClick={() => onAnswer('discard')}>{t(ask.discard)}</Button>
          <Button variant="primary" onClick={() => onAnswer('save')}>
            {t(ask.save)}
          </Button>
        </div>
      }
    />
  )
}
