import { ChevronRight, Flag, Image as ImageIcon, Layers, MessageCircleQuestionMark, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { IssueThread, StatusBadge } from '@/components/issues/IssueThread'
import { Badge, Button, EmptyState, ReloadNotice, Tabs } from '@/components/ui'
import { ListPager } from '@/components/ui/ListPager'
import { useServerPages } from '@/lib/useServerPages'
import { issuesApi, type IssueDetail, type IssueRow } from '@/lib/api'
import { KIND_LABEL, useIssues } from '@/lib/issues'
import { useT } from '@/lib/useT'
import { cn, formatDateTime } from '@/lib/utils'
import { ListSkeleton } from '@/components/ui/Skeleton'

/** Reports per page. */

type IssueFilter = 'all' | 'unread'

/** 「문의·신고」: sent items, status, replies; the account menu passes the originating conversation as router state. */
export function IssuesPage() {
  const t = useT()
  const openReport = useIssues((s) => s.openReport)
  const sessionId = (useLocation().state as { sessionId?: string } | null)?.sessionId
  const [params, setParams] = useSearchParams()
  const filter: IssueFilter = params.get('unread') === '1' ? 'unread' : 'all'
  const dialogOpen = useIssues((s) => s.open)
  const refreshUnread = useIssues((s) => s.refreshUnread)
  const [openId, setOpenId] = useState<string | null>(null)
  const list = useServerPages<IssueRow, number>({
    keys: [filter],
    fetch: async (offset, limit) => {
      const page = await issuesApi.page({ limit, offset, unread: filter === 'unread' })
      return { items: page.items, total: page.total, counts: page.unread }
    },
  })
  const { load, setRows, setCounts } = list
  const total = list.total
  const unread = list.counts ?? 0
  const failed = list.failed
  const rows = list.loading || failed ? null : list.rows

  // Again after the dialog closes, so a new report shows.
  useEffect(() => {
    if (!dialogOpen) void load(true)
  }, [dialogOpen, load])

  const markSeen = (id: string) => {
    // Still listed under 새 답변 until the filter is asked again: the open one does not vanish.
    setRows((held) => held.map((r) => (r.id === id ? { ...r, unread: false } : r)))
    setCounts((n) => Math.max(0, (n ?? 0) - 1))
    void refreshUnread()
  }

  const shown = rows ?? []
  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
      <PageBody className="max-w-3xl">
        <div className="flex flex-wrap items-end justify-between gap-3 pb-5">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{t('문의·신고')}</h1>
            <p className="mt-1 text-base text-muted">{t('보낸 신고·문의와 관리자 답변입니다.')}</p>
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
        <div className="mb-3">
          <Tabs<IssueFilter>
            value={filter}
            onChange={(next) => {
              setOpenId(null)
              setParams(next === 'unread' ? { unread: '1' } : {}, { replace: true, state: sessionId ? { sessionId } : null })
            }}
            tabs={[
              { id: 'all', label: t('전체'), count: filter === 'all' && rows ? total : undefined },
              { id: 'unread', label: t('새 답변'), count: unread },
            ]}
          />
        </div>
        {failed && <ReloadNotice onRetry={() => void load()} />}
        {rows === null && !failed ? (
          <ListSkeleton />
        ) : rows && rows.length === 0 ? (
          filter === 'unread' ? (
            <EmptyState icon={<Flag size={18} />} title={t('새 답변이 없습니다')} description={t('관리자가 답하면 여기에 모입니다.')} />
          ) : (
            <EmptyState
              icon={<Flag size={18} />}
              title={t('보낸 문의·신고가 없습니다')}
              description={t('오류가 나거나 필요한 기능이 있으면 알려 주세요.')}
            />
          )
        ) : rows ? (
          <>
            <ul className="divide-y divide-line rounded-card border border-line bg-panel">
              {shown.map((row) => (
                <IssueItem
                  key={row.id}
                  row={row}
                  open={openId === row.id}
                  onToggle={() => setOpenId(openId === row.id ? null : row.id)}
                  onSeen={() => markSeen(row.id)}
                />
              ))}
            </ul>
            <ListPager paging={list.paging} total={total} label={t('문의·신고 페이지')} />
          </>
        ) : null}
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
  const [detailFailed, setDetailFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [shot, setShot] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let live = true
    setDetailFailed(false)
    issuesApi.get(row.id).then(
      (d) => {
        if (!live) return
        setDetail(d)
        // Read only once the answer is on screen: a failed fetch leaves it 새 답변.
        if (row.unread) void issuesApi.markRead(row.id).then(onSeen, () => undefined)
      },
      () => live && setDetailFailed(true),
    )
    return () => {
      live = false
    }
    // Opening (or 다시 시도) triggers it; `onSeen` and unread read at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, row.id, attempt])

  useEffect(() => {
    if (!open || !row.hasScreenshot) return
    let live = true
    let url: string | null = null
    issuesApi.screenshot(row.id).then((blob) => {
      if (!live) return
      url = URL.createObjectURL(blob)
      setShot(url)
    }, () => undefined)
    return () => {
      live = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [open, row.id, row.hasScreenshot])

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
            {formatDateTime(row.createdAt)}
            {row.replies ? ` · ${t.n('답변 {n}개', row.replies)}` : ''}
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
          ) : detailFailed ? (
            <div role="alert" className="flex flex-wrap items-center gap-2 text-base text-danger">
              {t('답변을 불러오지 못했습니다.')}
              <Button size="sm" onClick={() => setAttempt((n) => n + 1)}>
                {t('다시 시도')}
              </Button>
            </div>
          ) : (
            <ListSkeleton />
          )}
        </div>
      )}
    </li>
  )
}
