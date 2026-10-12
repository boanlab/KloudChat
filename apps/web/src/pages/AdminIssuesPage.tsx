import { ArrowLeft, Eye, Flag, Image as ImageIcon, Layers, Search } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { IssueThread, StatusBadge } from '@/components/issues/IssueThread'
import { SnapshotLoading, SnapshotView } from '@/components/issues/SnapshotView'
import { StageList } from '@/components/chat/StageList'
import { Badge, Button, EmptyState, Input, LoadingState, PageHeader, ReloadNotice, Select, Textarea } from '@/components/ui'
import {
  errorCode,
  issuesApi,
  type AdminIssueFilters,
  type IssueDetail,
  type IssueRow,
  type IssueSnapshot,
  type IssueStatus,
} from '@/lib/api'
import { KIND_LABEL, STATUS_LABEL } from '@/lib/issues'
import { kindMeta } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import type { SessionKind } from '@/types'

const STATUSES: IssueStatus[] = ['received', 'checking', 'resolved', 'on_hold']
const PAGE_SIZE = 30

const UPDATE_ERROR: Record<string, string> = {
  hold_reason_required: '보류하려면 사유를 적어 주세요.',
  issue_transition_refused: '지금 상태에서는 그 상태로 바꿀 수 없습니다.',
  assignee_not_admin: '담당자는 관리자만 맡을 수 있습니다.',
}

/** Next local midnight after `date`: inclusive end. */
function dayAfter(date: string): string {
  const d = new Date(`${date}T00:00:00`)
  d.setDate(d.getDate() + 1)
  return d.toISOString()
}

export function AdminIssuesPage() {
  const { issueId } = useParams()
  return issueId ? <IssueDetailView id={issueId} /> : <IssueList />
}

function IssueList() {
  const t = useT()
  const navigate = useNavigate()
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [user, setUser] = useState('')
  const [q, setQ] = useState('')
  const [assignee, setAssignee] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Awaited<ReturnType<typeof issuesApi.adminList>> | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    const filters: AdminIssueFilters = {
      kind, status, user: user.trim(), q: q.trim(), assignee, page, size: PAGE_SIZE,
      since: since ? new Date(`${since}T00:00:00`).toISOString() : undefined,
      until: until ? dayAfter(until) : undefined,
    }
    issuesApi.adminList(filters).then(
      (d) => {
        setData(d)
        setFailed(false)
      },
      () => setFailed(true),
    )
  }, [kind, status, since, until, user, q, assignee, page])

  // Debounced text filters.
  useEffect(() => {
    const timer = setTimeout(load, 250)
    return () => clearTimeout(timer)
  }, [load])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
      <PageBody>
        <PageHeader title={t('문의·신고')} description={t('사용자가 보낸 오류 신고·기능 요청·문의를 확인하고 답합니다.')} />
        {data && (
          <div className="mb-4 flex flex-wrap gap-1.5" aria-label={t('상태별 건수')}>
            {STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => reset(setStatus)(status === s ? '' : s)}
                aria-pressed={status === s}
                className="rounded-control border border-line px-2.5 py-1 text-sm hover:bg-elevated aria-pressed:border-accent aria-pressed:bg-accent-soft"
              >
                {t(STATUS_LABEL[s])} <span className="tabular-nums text-faint">{data.counts[s] ?? 0}</span>
              </button>
            ))}
          </div>
        )}
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          <Select aria-label={t('종류')} value={kind} onChange={(e) => reset(setKind)(e.target.value)}>
            <option value="">{t('모든 종류')}</option>
            {(['bug', 'feature', 'other'] as const).map((k) => <option key={k} value={k}>{t(KIND_LABEL[k])}</option>)}
          </Select>
          <Select aria-label={t('상태')} value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
            <option value="">{t('모든 상태')}</option>
            {STATUSES.map((s) => <option key={s} value={s}>{t(STATUS_LABEL[s])}</option>)}
          </Select>
          <Input type="date" aria-label={t('시작일')} value={since} onChange={(e) => reset(setSince)(e.target.value)} />
          <Input type="date" aria-label={t('종료일')} value={until} onChange={(e) => reset(setUntil)(e.target.value)} />
          <Input aria-label={t('사용자')} placeholder={t('사용자 이름·이메일')} value={user} maxLength={100} onChange={(e) => reset(setUser)(e.target.value)} />
          <div className="relative">
            <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <Input aria-label={t('검색')} placeholder={t('내용 검색')} className="pl-7" value={q} maxLength={200} onChange={(e) => reset(setQ)(e.target.value)} />
          </div>
          <Select aria-label={t('담당자')} value={assignee} onChange={(e) => reset(setAssignee)(e.target.value)}>
            <option value="">{t('모든 담당자')}</option>
            <option value="me">{t('내가 맡은 것')}</option>
            <option value="none">{t('담당자 없음')}</option>
            {data?.admins.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </div>
        {failed && <ReloadNotice onRetry={load} />}
        {!data && !failed ? (
          <LoadingState />
        ) : data && data.items.length === 0 ? (
          <EmptyState icon={<Flag size={18} />} title={t('조건에 맞는 신고가 없습니다')} />
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line bg-panel">
            {data?.items.map((row) => <IssueListRow key={row.id} row={row} onOpen={() => navigate(`/admin/issues/${row.id}`)} />)}
          </ul>
        )}
        {data && pages > 1 && (
          <div className="mt-3 flex items-center justify-end gap-2 text-sm">
            <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t('이전 페이지')}</Button>
            <span className="tabular-nums text-muted">{page} / {pages}</span>
            <Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>{t('다음 페이지')}</Button>
          </div>
        )}
      </PageBody>
    </>
  )
}

function IssueListRow({ row, onOpen }: { row: IssueRow; onOpen: () => void }) {
  const t = useT()
  return (
    <li data-testid="admin-issue" data-status={row.status}>
      <button type="button" onClick={onOpen} className="flex w-full flex-wrap items-start gap-x-3 gap-y-1 px-3.5 py-3 text-left hover:bg-elevated/50">
        <span className="flex shrink-0 items-center gap-1.5">
          <StatusBadge status={row.status} />
          <Badge>{t(KIND_LABEL[row.kind])}</Badge>
        </span>
        <span className="min-w-0 flex-1 basis-60">
          <span className="block truncate text-base">{row.body}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-faint">
            <span>{row.reporter ? `${row.reporter.name} · ${row.reporter.email}` : ''}</span>
            <span>{new Date(row.createdAt).toLocaleString()}</span>
            {row.assignee && <span>{t('담당자')}: {row.assignee.name}</span>}
            {row.replies ? <span>{t('답변 {n}개').replace('{n}', String(row.replies))}</span> : null}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-faint">
          {row.sessionShared && <Layers size={14} aria-label={t('세션 공유됨')} />}
          {row.hasScreenshot && <ImageIcon size={14} aria-label={t('스크린샷 있음')} />}
        </span>
      </button>
    </li>
  )
}

function IssueDetailView({ id }: { id: string }) {
  const t = useT()
  const [detail, setDetail] = useState<IssueDetail | null>(null)
  const [failed, setFailed] = useState(false)
  const [shot, setShot] = useState<string | null>(null)
  const [nextStatus, setNextStatus] = useState<IssueStatus | ''>('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<IssueSnapshot | null>(null)
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    let live = true
    let url: string | null = null
    issuesApi.adminGet(id).then(
      (d) => {
        if (!live) return
        setDetail(d)
        if (d.hasScreenshot) {
          issuesApi.adminScreenshot(id).then((blob) => {
            if (!live) return
            url = URL.createObjectURL(blob)
            setShot(url)
          }, () => undefined)
        }
      },
      () => live && setFailed(true),
    )
    return () => {
      live = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [id])

  const update = async (patch: { status?: IssueStatus; holdReason?: string; assigneeId?: string }) => {
    setSaving(true)
    setError(null)
    try {
      const next = await issuesApi.adminUpdate(id, patch)
      setDetail(next)
      setNextStatus('')
      setReason('')
    } catch (err) {
      setError(t(UPDATE_ERROR[errorCode(err)] ?? '저장하지 못했습니다.'))
    } finally {
      setSaving(false)
    }
  }

  const openSnapshot = async () => {
    setOpening(true)
    setError(null)
    try {
      setSnapshot(await issuesApi.adminSnapshot(id))
    } catch {
      setError(t('세션 사본을 열지 못했습니다.'))
    } finally {
      setOpening(false)
    }
  }

  if (failed) {
    return (
      <>
        <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
        <PageBody><EmptyState icon={<Flag size={18} />} title={t('신고를 찾을 수 없습니다')} action={<Link className="text-accent" to="/admin/issues">{t('목록으로')}</Link>} /></PageBody>
      </>
    )
  }
  if (!detail) {
    return (
      <>
        <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
        <PageBody><LoadingState /></PageBody>
      </>
    )
  }

  const context = detail.context ?? {}
  const session = context.session
  const sessionKind = session?.kind as SessionKind | undefined

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('문의·신고')}</span>} />
      <PageBody>
        <Link to="/admin/issues" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
          <ArrowLeft size={14} />
          {t('목록으로')}
        </Link>
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="min-w-0 space-y-5">
            <section className="space-y-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <StatusBadge status={detail.status} />
                <Badge>{t(KIND_LABEL[detail.kind])}</Badge>
                <span className="text-sm text-faint">{new Date(detail.createdAt).toLocaleString()}</span>
              </div>
              <h1 className="text-lg font-semibold">
                {detail.reporter ? `${detail.reporter.name} · ${detail.reporter.email}` : t('알 수 없는 사용자')}
              </h1>
              <p data-testid="issue-body" className="whitespace-pre-wrap rounded-card border border-line bg-panel p-3 text-base [overflow-wrap:anywhere]">{detail.body}</p>
              {detail.status === 'on_hold' && detail.holdReason && (
                <p className="text-sm text-warn">{t('보류 사유')}: {detail.holdReason}</p>
              )}
              {shot && (
                <a href={shot} target="_blank" rel="noreferrer" className="block">
                  <img src={shot} alt={t('보낸 스크린샷')} className="max-h-80 rounded-card border border-line" />
                </a>
              )}
            </section>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">{t('답변과 처리 기록')}</h2>
              <IssueThread
                thread={detail.thread}
                viewer="admin"
                onReply={async (text) => {
                  const reply = await issuesApi.adminReply(id, text)
                  setDetail((d) => (d ? { ...d, thread: [...d.thread, reply] } : d))
                }}
              />
            </section>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">{t('세션 사본')}</h2>
              {!detail.sessionShared ? (
                <p className="text-sm text-faint">{t('신고한 사람이 세션을 공유하지 않았습니다. 아래 자동 기록만 있습니다.')}</p>
              ) : snapshot ? (
                <SnapshotView reportId={id} snapshot={snapshot} />
              ) : (
                <div className="space-y-2 rounded-card border border-line p-3">
                  <p className="text-sm text-muted">
                    {detail.snapshot?.title || t('제목 없는 대화')}
                    {' · '}
                    {t('메시지 {n}개').replace('{n}', String(detail.snapshot?.messages ?? 0))}
                    {detail.snapshot && detail.snapshot.artifacts > 0 && ` · ${t('아티팩트 {n}개').replace('{n}', String(detail.snapshot.artifacts))}`}
                  </p>
                  <p className="text-xs text-faint">{t('세션 사본을 열면 감사 기록에 남습니다.')}</p>
                  {opening ? <SnapshotLoading /> : (
                    <Button size="sm" onClick={() => void openSnapshot()}>
                      <Eye size={13} />
                      {t('세션 사본 열기')}
                    </Button>
                  )}
                </div>
              )}
            </section>
          </div>

          <aside className="min-w-0 space-y-4">
            <section className="space-y-2 rounded-card border border-line bg-panel p-3">
              <h2 className="text-base font-semibold">{t('처리')}</h2>
              <label className="block space-y-1 text-sm">
                <span className="text-muted">{t('담당자')}</span>
                <Select
                  value={detail.assignee?.id ?? ''}
                  disabled={saving}
                  onChange={(e) => void update({ assigneeId: e.target.value })}
                >
                  <option value="">{t('담당자 없음')}</option>
                  {detail.admins?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </label>
              <label className="block space-y-1 text-sm">
                <span className="text-muted">{t('상태 바꾸기')}</span>
                <Select value={nextStatus} disabled={saving} onChange={(e) => setNextStatus(e.target.value as IssueStatus | '')}>
                  <option value="">{t('{status} (지금)').replace('{status}', t(STATUS_LABEL[detail.status]))}</option>
                  {detail.transitions?.map((s) => <option key={s} value={s}>{t(STATUS_LABEL[s])}</option>)}
                </Select>
              </label>
              {nextStatus === 'on_hold' && (
                <label className="block space-y-1 text-sm">
                  <span className="text-muted">{t('보류 사유')}</span>
                  <Textarea rows={2} maxLength={400} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('신고한 사람에게 보입니다')} />
                </label>
              )}
              {nextStatus && (
                <Button
                  size="sm"
                  variant="primary"
                  disabled={saving || (nextStatus === 'on_hold' && !reason.trim())}
                  onClick={() => void update({ status: nextStatus, holdReason: reason.trim() || undefined })}
                >
                  {t('상태 저장')}
                </Button>
              )}
              {error && <p role="alert" className="text-sm text-danger">{error}</p>}
            </section>

            <section className="space-y-2 rounded-card border border-line bg-panel p-3 text-sm">
              <h2 className="text-base font-semibold">{t('자동 기록')}</h2>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
                <dt className="text-faint">{t('앱 빌드')}</dt>
                <dd className="[overflow-wrap:anywhere]">{context.client?.appBuild ?? '—'} · API {context.apiVersion ?? '—'}</dd>
                <dt className="text-faint">{t('브라우저')}</dt>
                <dd className="[overflow-wrap:anywhere]">{context.client?.userAgent ?? '—'}</dd>
                <dt className="text-faint">{t('화면')}</dt>
                <dd>{[context.client?.viewport, context.client?.lang, context.client?.timezone].filter(Boolean).join(' · ') || '—'}</dd>
                <dt className="text-faint">{t('보던 곳')}</dt>
                <dd className="[overflow-wrap:anywhere]">{context.client?.path ?? '—'}</dd>
                {session && (
                  <>
                    <dt className="text-faint">{t('대화 종류')}</dt>
                    <dd>
                      {sessionKind && kindMeta[sessionKind] ? t(kindMeta[sessionKind].label) : session.kind}
                      {session.strictLocal && <Badge tone="warn" className="ml-1.5">strict-local</Badge>}
                    </dd>
                    <dt className="text-faint">{t('모델')}</dt>
                    <dd className="[overflow-wrap:anywhere]">{context.models?.join(', ') || session.model || '—'}</dd>
                    <dt className="text-faint">{t('에이전트·진행 과정')}</dt>
                    <dd>{[session.agent, session.pipeline].filter(Boolean).join(' · ') || '—'}</dd>
                    <dt className="text-faint">{t('메시지')}</dt>
                    <dd>{session.messageCount}</dd>
                  </>
                )}
                {(context.failures?.length ?? 0) > 0 && (
                  <>
                    <dt className="text-faint">{t('실패한 턴')}</dt>
                    <dd>{context.failures?.map((f) => f.failure).join(', ')}</dd>
                  </>
                )}
                {(context.artifacts?.length ?? 0) > 0 && (
                  <>
                    <dt className="text-faint">{t('아티팩트 판')}</dt>
                    <dd className="font-mono text-xs [overflow-wrap:anywhere]">
                      {context.artifacts?.map((a) => `${a.id.slice(0, 8)} v${a.version}`).join(', ')}
                    </dd>
                  </>
                )}
              </dl>
              {(context.runs?.length ?? 0) > 0 && (
                <div className="space-y-2 border-t border-line pt-2">
                  <p className="font-medium">{t('단계 기록')}</p>
                  {context.runs?.map((run) => (
                    <div key={run.id} className="space-y-1">
                      <p className="text-xs text-faint">{run.pipeline} · {run.flow} · {run.status}</p>
                      <StageList sessionId={run.sessionId} run={run} live compact />
                    </div>
                  ))}
                </div>
              )}
            </section>
          </aside>
        </div>
      </PageBody>
    </>
  )
}
