import { ChevronRight, Flag, Image as ImageIcon, Layers, MessageCircleQuestionMark, Plus } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { IssueThread, StatusBadge } from '@/components/issues/IssueThread'
import { Badge, Button, EmptyState, LoadingState, ReloadNotice } from '@/components/ui'
import { issuesApi, type IssueDetail, type IssueRow } from '@/lib/api'
import { KIND_LABEL, useIssues } from '@/lib/issues'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/**
 * 「문의·신고」: what I sent, where it stands, and what the administrators said; new reports
 * and inquiries start here. The account menu passes the conversation the user came from as
 * router state, so a report started here can still attach it.
 */
export function IssuesPage() {
  const t = useT()
  const openReport = useIssues((s) => s.openReport)
  const sessionId = (useLocation().state as { sessionId?: string } | null)?.sessionId
  const dialogOpen = useIssues((s) => s.open)
  const refreshUnread = useIssues((s) => s.refreshUnread)
  const [rows, setRows] = useState<IssueRow[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)

  const load = useCallback(() => {
    issuesApi.list().then(
      (list) => {
        setRows(list)
        setFailed(false)
      },
      () => setFailed(true),
    )
  }, [])

  // Again after the dialog closes, so a new report shows.
  useEffect(() => {
    if (!dialogOpen) load()
  }, [dialogOpen, load])

  const markSeen = (id: string) => {
    setRows((list) => list?.map((r) => (r.id === id ? { ...r, unread: false } : r)) ?? list)
    void refreshUnread()
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
      <PageBody className="max-w-3xl">
        <div className="flex flex-wrap items-end justify-between gap-3 pb-5">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{t('문의·신고')}</h1>
            <p className="mt-1 text-base text-muted">{t('보낸 오류 신고와 문의, 처리 상태와 관리자 답변입니다.')}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => openReport({ kind: 'other', sessionId })}>
              <MessageCircleQuestionMark size={14} />
              {t('새 문의')}
            </Button>
            <Button variant="primary" onClick={() => openReport({ kind: 'bug', sessionId })}>
              <Plus size={14} />
              {t('새 신고')}
            </Button>
          </div>
        </div>
        {failed && <ReloadNotice onRetry={load} />}
        {rows === null && !failed ? (
          <LoadingState />
        ) : rows && rows.length === 0 ? (
          <EmptyState
            icon={<Flag size={18} />}
            title={t('보낸 문의·신고가 없습니다')}
            description={t('오류가 나거나 필요한 기능이 있으면 알려 주세요.')}
          />
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line bg-panel">
            {rows?.map((row) => (
              <IssueItem
                key={row.id}
                row={row}
                open={openId === row.id}
                onToggle={() => setOpenId(openId === row.id ? null : row.id)}
                onSeen={() => markSeen(row.id)}
              />
            ))}
          </ul>
        )}
      </PageBody>
    </>
  )
}

function IssueItem({
  row,
  open,
  onToggle,
  onSeen,
}: {
  row: IssueRow
  open: boolean
  onToggle: () => void
  onSeen: () => void
}) {
  const t = useT()
  const [detail, setDetail] = useState<IssueDetail | null>(null)
  const [shot, setShot] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let live = true
    issuesApi.get(row.id).then((d) => live && setDetail(d), () => undefined)
    if (row.unread) {
      void issuesApi.markRead(row.id).then(onSeen, () => undefined)
    }
    let url: string | null = null
    if (row.hasScreenshot) {
      issuesApi.screenshot(row.id).then((blob) => {
        if (!live) return
        url = URL.createObjectURL(blob)
        setShot(url)
      }, () => undefined)
    }
    return () => {
      live = false
      if (url) URL.revokeObjectURL(url)
    }
    // Opening is the trigger; `onSeen` and the unread flag are read at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, row.id])

  return (
    <li data-testid="my-issue" data-status={row.status}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start gap-2.5 px-3.5 py-3 text-left hover:bg-elevated/50"
      >
        <ChevronRight size={14} className={cn('mt-1 shrink-0 text-faint transition-transform', open && 'rotate-90')} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={row.status} />
            <Badge>{t(KIND_LABEL[row.kind])}</Badge>
            {row.sessionShared && <Layers size={13} className="text-faint" aria-label={t('세션 공유됨')} />}
            {row.hasScreenshot && <ImageIcon size={13} className="text-faint" aria-label={t('스크린샷 있음')} />}
            {row.unread && (
              <span className="rounded-control bg-accent px-1.5 text-xs font-medium text-white">{t('새 답변')}</span>
            )}
          </span>
          <span className="mt-1 block truncate text-base">{row.body}</span>
          <span className="mt-0.5 block text-xs text-faint">
            {new Date(row.createdAt).toLocaleString()}
            {row.replies ? ` · ${t('답변 {n}개').replace('{n}', String(row.replies))}` : ''}
          </span>
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-3.5 py-3 sm:pl-9">
          <p className="whitespace-pre-wrap text-base [overflow-wrap:anywhere]">{row.body}</p>
          {row.status === 'on_hold' && row.holdReason && (
            <p className="rounded-control border border-warn/30 bg-warn/10 px-2.5 py-1.5 text-sm text-warn">
              {t('보류 사유')}: {row.holdReason}
            </p>
          )}
          {shot && <img src={shot} alt={t('보낸 스크린샷')} className="max-h-56 rounded-control border border-line" />}
          <p className="text-xs text-faint">
            {row.sessionShared ? t('세션 사본을 관리자에게 공유했습니다.') : t('세션 내용은 공유하지 않았습니다.')}
          </p>
          {detail ? (
            <IssueThread
              thread={detail.thread}
              viewer="user"
              onReply={async (text) => {
                const reply = await issuesApi.reply(row.id, text)
                setDetail((d) => (d ? { ...d, thread: [...d.thread, reply] } : d))
              }}
            />
          ) : (
            <LoadingState />
          )}
        </div>
      )}
    </li>
  )
}
