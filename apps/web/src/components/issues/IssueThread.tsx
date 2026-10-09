import { Loader2, Send } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Textarea } from '@/components/ui'
import { errorCode, type IssueReply, type IssueStatus } from '@/lib/api'
import { CREATE_ERROR, STATUS_LABEL, STATUS_TONE } from '@/lib/issues'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

export function StatusBadge({ status }: { status: IssueStatus }) {
  const t = useT()
  return <Badge tone={STATUS_TONE[status]}>{t(STATUS_LABEL[status])}</Badge>
}

/** A report's thread: replies both ways, and the status changes with their reasons. */
export function IssueThread({
  thread,
  viewer,
  onReply,
}: {
  thread: IssueReply[]
  /** Whose screen this is: their own entries sit on the right. */
  viewer: 'admin' | 'user'
  onReply?: (text: string) => Promise<void>
}) {
  const t = useT()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    if (!onReply || !text.trim()) return
    setBusy(true)
    setError(null)
    try {
      await onReply(text.trim())
      setText('')
    } catch (err) {
      setError(t(CREATE_ERROR[errorCode(err)] ?? '보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      {thread.length === 0 && <p className="text-sm text-faint">{t('아직 답변이 없습니다.')}</p>}
      <ol className="space-y-2" aria-label={t('답변과 처리 기록')}>
        {thread.map((entry) => {
          const mine = entry.authorRole === viewer
          if (entry.status) {
            return (
              <li key={entry.id} className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
                <span>{entry.authorRole === 'admin' ? t('관리자') : entry.authorName}</span>
                <span>{t('상태를 바꿈')}</span>
                <StatusBadge status={entry.status} />
                {entry.body && <span className="min-w-0 [overflow-wrap:anywhere]">· {entry.body}</span>}
                <span className="ml-auto text-xs tabular-nums text-faint">{new Date(entry.createdAt).toLocaleString()}</span>
              </li>
            )
          }
          return (
            <li key={entry.id} className={cn('flex', mine ? 'justify-end' : 'justify-start')}>
              <div
                className={cn(
                  'max-w-[85%] rounded-card border px-3 py-2 text-base',
                  mine ? 'border-accent/30 bg-accent-soft' : 'border-line bg-elevated/60',
                )}
              >
                <p className="mb-0.5 text-xs text-faint">
                  {entry.authorRole === 'admin' ? `${t('관리자')} · ${entry.authorName}` : entry.authorName}
                  {' · '}
                  {new Date(entry.createdAt).toLocaleString()}
                </p>
                <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{entry.body}</p>
              </div>
            </li>
          )
        })}
      </ol>
      {onReply && (
        <div className="space-y-1.5 pt-1">
          <Textarea
            rows={3}
            maxLength={2000}
            value={text}
            aria-label={viewer === 'admin' ? t('답변 쓰기') : t('덧붙일 내용')}
            placeholder={viewer === 'admin' ? t('신고한 사람에게 보일 답변') : t('관리자에게 덧붙일 내용')}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex items-center justify-end gap-2">
            {error && <p role="alert" className="mr-auto text-sm text-danger">{error}</p>}
            <Button size="sm" variant="primary" disabled={busy || !text.trim()} onClick={() => void send()}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
              {viewer === 'admin' ? t('답변 보내기') : t('보내기')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
