import { Download, FileText, Loader2, Pin, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Markdown } from '@/components/chat/Markdown'
import { StageList } from '@/components/chat/StageList'
import { Badge, Button } from '@/components/ui'
import { issuesApi, type IssueSnapshot, type MessageRow } from '@/lib/api'
import { ACTION_LABEL } from '@/lib/auditLabels'
import { CUT_LABEL } from '@/lib/issues'
import { kindMeta } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import { cn, fileSize } from '@/lib/utils'
import { SharedArtifact } from '@/pages/SharedPage'
import type { PipelineRun, SessionKind } from '@/types'

const FAILURE_LABEL: Record<string, string> = {
  no_answer: '답을 만들지 못함',
  interrupted: '중간에 끊김',
  stopped: '사용자가 멈춤',
}

/** Saves a blob under its name; the token travels in a header, not the URL. */
function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

/**
 * A reported session as it stood when it was reported: the transcript, each answer's stage
 * records (evidence included), the artifacts it made and its files. Read-only: nothing here
 * calls the reporter's session.
 */
export function SnapshotView({ reportId, snapshot }: { reportId: string; snapshot: IssueSnapshot }) {
  const t = useT()
  const pinnedRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const runs = new Map(snapshot.runs.map((run) => [run.id, run]))
  const kind = snapshot.session.kind as SessionKind
  const pinned = snapshot.pinned ?? { messageId: null, artifactId: null }

  useEffect(() => {
    pinnedRef.current?.scrollIntoView?.({ block: 'center' })
  }, [])

  const download = async (index: number, name: string, artifact = false) => {
    setError(null)
    try {
      save(await issuesApi.adminFile(reportId, index, artifact), name)
    } catch {
      setError(t('파일을 받지 못했습니다.'))
    }
  }

  return (
    <div data-testid="issue-snapshot" className="space-y-5">
      <div className="rounded-card border border-line bg-elevated/40 p-3 text-sm">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge>{t('읽기 전용')}</Badge>
          <Badge>{t('신고 시점 사본')}</Badge>
          {kindMeta[kind] && <Badge>{t(kindMeta[kind].label)}</Badge>}
        </div>
        <p className="mt-2 text-md font-semibold">{snapshot.session.title || t('제목 없는 대화')}</p>
        <p className="mt-0.5 text-muted">
          {[snapshot.session.model, snapshot.session.agent, snapshot.session.project, snapshot.session.pipeline]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {snapshot.truncated.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-warn">
            {snapshot.truncated.map((cut, i) => (
              <li key={i} className="flex items-start gap-1">
                <TriangleAlert size={12} className="mt-0.5 shrink-0" />
                {t('용량 한도로 {what}').replace('{what}', t(CUT_LABEL[cut.code] ?? cut.code).replace('{n}', String(cut.count ?? 0)))}
              </li>
            ))}
          </ul>
        )}
      </div>

      <section aria-label={t('대화 기록')}>
        <h3 className="mb-2 text-base font-semibold">{t('대화 기록')}</h3>
        <ol className="space-y-3">
          {snapshot.messages.map((message) => {
            const isPinned = message.id === pinned.messageId
            return (
              <li key={message.id}>
                <div
                  ref={isPinned ? pinnedRef : undefined}
                  data-pinned={isPinned || undefined}
                  className={cn(
                    'rounded-card border p-3',
                    message.role === 'user' ? 'border-line bg-elevated/50' : 'border-line bg-panel',
                    isPinned && 'border-accent ring-2 ring-accent/40',
                  )}
                >
                  <SnapshotMessage message={message} runs={runs} sessionId={snapshot.session.id} pinned={isPinned} />
                </div>
              </li>
            )
          })}
        </ol>
      </section>

      {snapshot.artifacts.length > 0 && (
        <section aria-label={t('결과물')} className="space-y-4">
          <h3 className="text-base font-semibold">{t('결과물')}</h3>
          {snapshot.artifacts.map((artifact, index) => {
            const isPinned = artifact.id === pinned.artifactId
            return (
              <div
                key={artifact.id}
                data-pinned={isPinned || undefined}
                className={cn('space-y-2 rounded-card border border-line p-3', isPinned && 'border-accent ring-2 ring-accent/40')}
              >
                <div className="flex flex-wrap items-center gap-1.5 text-sm">
                  <FileText size={14} className="text-faint" />
                  <span className="font-medium">{artifact.title || t('제목 없음')}</span>
                  <Badge>v{artifact.version}</Badge>
                  {isPinned && <Badge tone="accent"><Pin size={11} />{t('신고한 결과물')}</Badge>}
                  {artifact.versions.length > 0 && (
                    <span className="text-faint">
                      {t('판 기록 {n}개').replace('{n}', String(artifact.versions.length))}
                    </span>
                  )}
                  {artifact.blobCopied && (
                    <Button size="sm" variant="ghost" onClick={() => void download(index, artifact.title || 'artifact', true)}>
                      <Download size={13} />
                      {t('원본 받기')}
                    </Button>
                  )}
                </div>
                {artifact.data ? (
                  <div className="max-h-[36rem] overflow-auto rounded-control bg-elevated/30 p-2">
                    <SharedArtifact payload={{ data: artifact.data, title: artifact.title }} title={artifact.title} />
                  </div>
                ) : (
                  <p className="text-sm text-faint">{t('본문은 용량 한도로 빠졌습니다.')}</p>
                )}
              </div>
            )
          })}
        </section>
      )}

      {snapshot.files.length > 0 && (
        <section aria-label={t('대화의 파일')}>
          <h3 className="mb-2 text-base font-semibold">{t('대화의 파일')}</h3>
          <ul className="divide-y divide-line rounded-card border border-line">
            {snapshot.files.map((file, index) => (
              <li key={file.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <span className="shrink-0 text-faint">{fileSize(file.size)}</span>
                {file.copied ? (
                  <Button size="sm" variant="ghost" aria-label={t('{name} 받기').replace('{name}', file.name)} onClick={() => void download(index, file.name)}>
                    <Download size={13} />
                  </Button>
                ) : (
                  <span className="shrink-0 text-xs text-faint">{t('목록만 (용량 초과)')}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </div>
  )
}

function SnapshotMessage({
  message,
  runs,
  sessionId,
  pinned,
}: {
  message: MessageRow
  runs: Map<string, PipelineRun>
  sessionId: string
  pinned: boolean
}) {
  const t = useT()
  const summary = (message.routing as { run?: PipelineRun } | null)?.run
  // The stored run carries the evidence the message's summary leaves out.
  const run = summary ? (runs.get(summary.id) ?? summary) : null
  const routing = message.routing as { action?: string } | null
  const failure = (message as { failure?: string | null }).failure
  return (
    <>
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs text-faint">
        <span className="font-medium text-muted">{message.role === 'user' ? t('질문') : t('답변')}</span>
        {message.model && <span>{message.model}</span>}
        {routing?.action && routing.action !== 'none' && (
          // The privacy route's words from the audit log; an unknown code shows as recorded.
          <Badge title={routing.action}>{t(ACTION_LABEL[`privacy.${routing.action}`] ?? routing.action)}</Badge>
        )}
        {failure && <Badge tone="danger">{t(FAILURE_LABEL[failure] ?? failure)}</Badge>}
        {(message as { rating?: string | null }).rating === 'down' && <Badge tone="warn">👎</Badge>}
        {pinned && <Badge tone="accent"><Pin size={11} />{t('신고한 답변')}</Badge>}
        <span className="ml-auto tabular-nums">{new Date(message.createdAt).toLocaleString()}</span>
      </div>
      {message.content ? (
        <div className="min-w-0 text-base [overflow-wrap:anywhere]">
          <Markdown>{message.content}</Markdown>
        </div>
      ) : (
        <p className="text-sm text-faint">{t('내용 없음')}</p>
      )}
      {run && run.stages.length > 0 && (
        <div className="mt-2 border-t border-line pt-2">
          {/* `live` keeps it from fetching the reporter's run or offering a rerun. */}
          <StageList sessionId={sessionId} run={run} live compact={false} />
        </div>
      )}
    </>
  )
}

/** Loading wrapper used by the admin detail. */
export function SnapshotLoading() {
  const t = useT()
  return (
    <p role="status" className="flex items-center gap-1.5 text-sm text-faint">
      <Loader2 size={13} className="animate-spin" />
      {t('불러오는 중…')}
    </p>
  )
}
