import { AlertTriangle, ArrowLeft, Bot, CheckCircle2, ExternalLink, Eye, PauseCircle, PlayCircle, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Markdown } from '@/components/chat/Markdown'
import { Badge, Button, Card, EmptyState, Field, LoadingState, Modal, PageHeader, ReloadNotice, Tabs, Textarea } from '@/components/ui'
import {
  adminPublicationsApi,
  errorMessage,
  type AdminPublication,
  type BotChannels,
  type BotConversationRow,
  type BotTranscriptMessage,
  type PublicationReview,
} from '@/lib/api'
import {
  CHANNEL_LABEL,
  CHANNELS,
  FINDING_LABEL,
  PAUSE_LABEL,
  STATUS_LABEL,
  useBotAttention,
  VISIBILITY_HINT,
  VISIBILITY_LABEL,
} from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { formatDateTime } from '@/lib/utils'

type View = 'pending' | 'all'

const ACTION_LABEL: Record<string, string> = {
  'publication.published': '공개',
  'publication.requested': '승인 요청',
  'publication.reviewed': '검토',
  'publication.withdrawn': '공개 범위 닫음',
  'publication.narrowed': '범위 좁힘',
  'publication.version': '새 판',
  'publication.request_withdrawn': '요청 취소',
  'publication.admin_status': '관리자 상태 변경',
  'publication.channels_requested': '채널 요청',
  'publication.channels_closed': '채널 닫음',
}

/** Approval effect by request. */
const REQUEST_CONSEQUENCE: Record<'visibility' | 'channels' | 'version', string> = {
  visibility: '승인하면 아래 판이 로그인하지 않은 사람에게도 답합니다. 비용은 만든 사람의 크레딧에서 나가며, 하루 최대 {n}크레딧입니다.',
  channels: '승인하면 요청한 채널이 열려, 봇 키를 가진 프로그램이나 승인한 사이트에서도 이 챗봇이 답합니다. 비용은 만든 사람의 크레딧에서 나가며, 하루 최대 {n}크레딧입니다.',
  version: '승인하면 아래 판이 지금 공개 범위와 채널에서 답합니다. 승인 전까지는 지금 판이 답합니다.',
}

/** Recorded review by request. */
const ASKED_LABEL: Record<string, string> = {
  visibility: '범위 요청',
  channels: '채널 요청',
  version: '새 판 확인',
}

const CHANGE_LABEL: Record<string, string> = {
  instructions: '지시',
  contact: '담당 부서·연락처',
  model: '답하는 모델',
  tools: '도구',
  checks: '검사',
  knowledge: '자료',
}

/** By screen name. */
const TOOL_LABEL: Record<string, string> = {
  search_knowledge: '자료 찾기',
  calculate: '계산기',
}

function changeWords(value: unknown, t: (s: string) => string): string {
  if (Array.isArray(value)) return value.map((v) => t(TOOL_LABEL[String(v)] ?? String(v))).join('\n')
  return value == null ? '' : String(value)
}

/** 「다른 사이트에 넣기(https://…) · API」. */
function channelWords(set: BotChannels | null | undefined, t: (s: string) => string): string {
  return CHANNELS.filter((c) => set?.[c])
    .map((c) => (c === 'embed' ? `${t(CHANNEL_LABEL[c])} (${(set?.embed?.origins ?? []).join(', ')})` : t(CHANNEL_LABEL[c])))
    .join(' · ')
}

/** Each reason is read by the owner and the audit log. */
type Decision = 'approve' | 'reject' | 'withdraw' | 'paused' | 'approved' | 'revoked'

const DECISION: Record<Decision, { title: string; hint: string; required: boolean; danger?: boolean }> = {
  approve: { title: '승인', hint: '만든 사람에게 보일 메모(선택)', required: false },
  reject: { title: '반려', hint: '만든 사람에게 보일 반려 사유', required: true, danger: true },
  withdraw: { title: '공개 범위 닫기', hint: '구성원에게만 열린 상태로 돌리고, 다른 사이트·API·MCP 채널도 함께 닫습니다. 만든 사람에게 보일 사유', required: true, danger: true },
  paused: { title: '일시 중지', hint: '만든 사람에게 보일 사유', required: true },
  approved: { title: '다시 열기', hint: '감사 기록에 남길 사유', required: true },
  revoked: { title: '챗봇 닫기', hint: '누구도 쓸 수 없게 닫습니다. 만든 사람에게 보일 사유', required: true, danger: true },
}

/** Review queue first, then every bot. */
export function AdminPublicationsPage() {
  const { publicationId } = useParams()
  return publicationId ? <ReviewView id={publicationId} /> : <PublicationList />
}

function scopeBadge(row: { visibility: AdminPublication['visibility'] }, t: (s: string) => string) {
  return <Badge tone={row.visibility === 'internal' ? 'neutral' : 'accent'}>{t(VISIBILITY_LABEL[row.visibility])}</Badge>
}

function PublicationList() {
  const t = useT()
  const navigate = useNavigate()
  const [view, setView] = useState<View>('pending')
  const [rows, setRows] = useState<AdminPublication[] | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    adminPublicationsApi.list(view === 'pending').then(
      (found) => {
        setRows(found)
        setFailed(false)
      },
      () => setFailed(true),
    )
  }, [view])
  useEffect(load, [load])

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('공개 챗봇')}</span>} />
      <PageBody>
        <PageHeader
          title={t('공개 챗봇')}
          description={t('구성원이 공개한 챗봇입니다. 로그인하지 않은 사람에게 여는 링크 공개·외부 공개는 여기서 승인해야 열립니다.')}
        />
        <div className="mb-4">
          <Tabs
            tabs={[
              { id: 'pending', label: t('승인 대기') },
              { id: 'all', label: t('모든 챗봇') },
            ]}
            value={view}
            onChange={(next) => setView(next as View)}
          />
        </div>
        {failed && <ReloadNotice onRetry={load} />}
        {rows === null && !failed ? (
          <LoadingState />
        ) : rows && rows.length === 0 ? (
          <EmptyState icon={<Bot size={18} />} title={view === 'pending' ? t('승인을 기다리는 요청이 없습니다') : t('공개된 챗봇이 없습니다')} />
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line bg-panel" data-testid="admin-publications">
            {rows?.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => navigate(`/admin/publications/${row.id}`)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3.5 py-3 text-left hover:bg-elevated/50"
                >
                  <span className="min-w-0 flex-1 basis-60">
                    <span className="block truncate text-base font-medium">{row.title}</span>
                    <span className="block truncate text-xs text-faint">
                      {row.ownerName} · {row.ownerEmail} · {t('대화 {n}개').replace('{n}', String(row.conversations))}
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    {scopeBadge(row, t)}
                    <Badge tone={row.status === 'approved' ? 'success' : 'warn'}>{t(STATUS_LABEL[row.status])}</Badge>
                    {row.reviewState === 'pending' && (
                      <Badge tone="accent">
                        {row.requestedVisibility
                          ? t('{scope} 요청').replace('{scope}', t(VISIBILITY_LABEL[row.requestedVisibility]))
                          : row.requestedChannels && !row.pendingVersionId
                            ? t('채널 요청')
                            : t('새 판 확인')}
                      </Badge>
                    )}
                    {row.reviewState === 'rejected' && <Badge tone="danger">{t('반려')}</Badge>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </PageBody>
    </>
  )
}

function ReviewView({ id }: { id: string }) {
  const t = useT()
  const refreshBadge = useBotAttention((s) => s.refresh)
  const [data, setData] = useState<PublicationReview | null>(null)
  const [failed, setFailed] = useState(false)
  const [deciding, setDeciding] = useState<Decision | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    adminPublicationsApi.review(id).then(
      (found) => {
        setData(found)
        setFailed(false)
      },
      () => setFailed(true),
    )
  }, [id])
  useEffect(load, [load])

  const decide = async () => {
    if (!deciding) return
    setBusy(true)
    setError(null)
    try {
      if (deciding === 'approve' || deciding === 'reject') await adminPublicationsApi.decide(id, deciding, reason.trim())
      else if (deciding === 'withdraw') await adminPublicationsApi.withdraw(id, reason.trim())
      else await adminPublicationsApi.setStatus(id, deciding, reason.trim())
      setDeciding(null)
      setReason('')
      load()
      void refreshBadge()
    } catch (err) {
      setError(errorMessage(err, t('처리하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const back = (
    <Link to="/admin/publications" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
      <ArrowLeft size={14} aria-hidden />
      {t('공개 챗봇')}
    </Link>
  )
  if (failed) {
    return (
      <PageBody>
        {back}
        <ReloadNotice onRetry={load} />
      </PageBody>
    )
  }
  if (!data) {
    return (
      <PageBody>
        <LoadingState />
      </PageBody>
    )
  }
  const pub = data.publication
  const pending = pub.reviewState === 'pending'
  const outside = pub.visibility !== 'internal'
  const spec = deciding ? DECISION[deciding] : null
  const short = !!spec?.required && reason.trim().length < 4

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('공개 챗봇')}</span>} />
      <PageBody>
        <div className="mb-4">{back}</div>
        <header className="flex flex-wrap items-start gap-3 border-b border-line pb-5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight break-words">{pub.title}</h1>
              {scopeBadge(pub, t)}
              <Badge tone={pub.status === 'approved' ? 'success' : 'warn'}>{t(STATUS_LABEL[pub.status])}</Badge>
            </div>
            <p className="mt-1 text-sm text-faint">
              {data.owner.name} · {data.owner.email}
            </p>
          </div>
          <a href={`/b/${pub.slug}`} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line px-3 text-base hover:bg-elevated">
            <ExternalLink size={14} aria-hidden />
            {t('열기')}
          </a>
        </header>

        {pending && (
          <Card className="mt-4 space-y-3 border-accent/40 p-4" data-testid="admin-review-request">
            <p className="text-base font-medium">
              {pub.requestedVisibility
                ? t('{scope} 요청').replace('{scope}', t(VISIBILITY_LABEL[pub.requestedVisibility]))
                : pub.requestedChannels && !pub.pendingVersionId
                  ? t('채널 요청')
                  : t('새 판 확인 요청')}
              {data.version && ` · ${t('{n}판').replace('{n}', String(data.version.number))}`}
            </p>
            {data.version?.note && (
              <p className="text-sm" data-testid="admin-review-note">
                <span className="text-faint">{t('바꾼 점 메모')}: </span>
                {data.version.note}
              </p>
            )}
            {pub.requestedVisibility && <p className="text-sm text-muted">{t(VISIBILITY_HINT[pub.requestedVisibility])}</p>}
            {pub.requestedChannels && (
              <p className="text-sm" data-testid="admin-review-channels">
                <span className="text-faint">{t('요청한 채널')}: </span>
                {channelWords(pub.requestedChannels, t) || t('없음')}
              </p>
            )}
            <p className="text-sm text-muted" data-testid="admin-review-consequence">
              {t(REQUEST_CONSEQUENCE[data.request ?? (pub.requestedVisibility ? 'visibility' : pub.requestedChannels && !pub.pendingVersionId ? 'channels' : 'version')]).replace(
                '{n}',
                (pub.limits.creditsPerDay ?? 0).toLocaleString(),
              )}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => setDeciding('approve')}>
                <CheckCircle2 size={14} />
                {t('승인')}
              </Button>
              <Button variant="danger" onClick={() => setDeciding('reject')}>
                <XCircle size={14} />
                {t('반려')}
              </Button>
            </div>
          </Card>
        )}
        {pub.reviewState === 'rejected' && pub.reviewNote && (
          <p className="mt-4 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
            {t('반려 사유: {reason}').replace('{reason}', pub.reviewNote)}
          </p>
        )}
        {pub.status === 'paused' && (
          <p className="mt-4 rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-base text-warn">
            {t(PAUSE_LABEL[pub.pauseReason] ?? '일시 중지되었습니다.')}
          </p>
        )}

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,20rem)]">
          <div className="min-w-0 space-y-4">
            {(data.changes?.length ?? 0) > 0 && (
              <Card className="space-y-3 p-4" data-testid="admin-review-changes">
                <p className="text-sm text-faint">{t('지금 답하는 판과 달라진 것')}</p>
                <ul className="space-y-2">
                  {data.changes!.map((change) => (
                    <li key={change.field} className="space-y-1 text-sm">
                      <p className="font-medium">{t(CHANGE_LABEL[change.field] ?? change.field)}</p>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <pre className="max-h-48 overflow-auto rounded-control bg-elevated px-3 py-2 text-sm whitespace-pre-wrap">
                          <span className="block text-xs text-faint">{t('전')}</span>
                          {changeWords(change.before, t) || '—'}
                        </pre>
                        <pre className="max-h-48 overflow-auto rounded-control bg-accent-soft px-3 py-2 text-sm whitespace-pre-wrap">
                          <span className="block text-xs text-faint">{t('후')}</span>
                          {changeWords(change.after, t) || '—'}
                        </pre>
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {data.agent && (
              <Card className="space-y-3 p-4" data-testid="admin-review-agent">
                <p className="text-sm text-faint">{t('지시')}</p>
                <pre className="max-h-80 overflow-auto rounded-control bg-elevated px-3 py-2 text-sm whitespace-pre-wrap">{data.agent.instructions || '—'}</pre>
                {data.agent.contact && (
                  <p className="text-sm">
                    <span className="text-faint">{t('담당 부서·연락처')}: </span>
                    {data.agent.contact}
                  </p>
                )}
                {data.agent.guide && (
                  <p className="text-sm">
                    <span className="text-faint">{t('첫 인사')}: </span>
                    {pub.branding.greeting || data.agent.guide}
                  </p>
                )}
                <p className="text-sm text-muted">
                  {t('답하는 모델: {model}').replace('{model}', data.agent.model)} · {t('도구')}:{' '}
                  {data.agent.tools.map((tool) => t(TOOL_LABEL[tool] ?? tool)).join(', ') || '—'} · {t('검사')}:{' '}
                  {(data.agent.checkNames ?? data.agent.checks).join(', ') || '—'}
                </p>
              </Card>
            )}
            <Card className="space-y-2 p-4" data-testid="admin-review-knowledge">
              <p className="text-sm text-faint">{t('자료')}</p>
              {data.knowledge.length === 0 ? (
                <p className="text-sm text-faint">{t('자료가 없습니다.')}</p>
              ) : (
                <ul className="space-y-1">
                  {data.knowledge.map((file) => (
                    <li key={file.name} className="text-sm">
                      {file.name} <span className="text-faint">· {t('{n}자').replace('{n}', file.chars.toLocaleString())}</span>
                      {file.findings.length > 0 && (
                        <span className="ml-2 inline-flex items-center gap-1 text-warn">
                          <AlertTriangle size={13} aria-hidden />
                          {file.findings.map((f) => `${t(FINDING_LABEL[f.category] ?? f.category)} ${f.count}`).join(', ')}
                        </span>
                      )}
                      {file.preview && (
                        <details className="mt-1">
                          <summary className="cursor-pointer text-faint hover:text-fg">{t('내용 보기 (개인정보는 가림)')}</summary>
                          <pre className="mt-1 max-h-64 overflow-auto rounded-control bg-elevated px-3 py-2 text-sm whitespace-pre-wrap" data-testid="admin-review-preview">
                            {file.preview}
                            {file.chars > file.preview.length && '\n…'}
                          </pre>
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <ConversationsCard id={pub.id} />
          </div>
          <div className="space-y-4">
            <Card className="space-y-1 p-4" data-testid="admin-open-channels">
              <p className="text-sm text-faint">{t('열린 채널')}</p>
              <p className="text-sm">{channelWords(pub.channels, t) || t('없음')}</p>
            </Card>
            <Card className="space-y-2 p-4">
              <p className="text-sm text-faint">{t('관리')}</p>
              <div className="flex flex-wrap gap-2">
                {outside && (
                  <Button onClick={() => setDeciding('withdraw')}>
                    {t(pub.visibility === 'link' ? '링크 공개 닫기' : '외부 공개 닫기')}
                  </Button>
                )}
                {pub.status === 'approved' && (
                  <Button onClick={() => setDeciding('paused')}>
                    <PauseCircle size={14} />
                    {t('일시 중지')}
                  </Button>
                )}
                {pub.status === 'paused' && (
                  <Button onClick={() => setDeciding('approved')}>
                    <PlayCircle size={14} />
                    {t('다시 열기')}
                  </Button>
                )}
                {pub.status !== 'revoked' && (
                  <Button variant="danger" onClick={() => setDeciding('revoked')}>
                    {t('챗봇 닫기')}
                  </Button>
                )}
              </div>
            </Card>
            <Card className="space-y-2 p-4">
              <p className="text-sm text-faint">{t('기록')}</p>
              <ul className="space-y-1.5 text-sm" data-testid="admin-review-history">
                {data.history.map((e, i) => (
                  <li key={i}>
                    <span>{t(ACTION_LABEL[e.action] ?? e.action)}</span>
                    {typeof e.metadata.asked === 'string' && ASKED_LABEL[e.metadata.asked] && ` · ${t(ASKED_LABEL[e.metadata.asked])}`}
                    {typeof e.metadata.decision === 'string' && ` · ${e.metadata.decision === 'approve' ? t('승인') : t('반려')}`}
                    {typeof e.metadata.visibility === 'string' && e.metadata.visibility && e.metadata.asked !== 'channels' && e.metadata.asked !== 'version' &&
                      ` · ${t(VISIBILITY_LABEL[e.metadata.visibility as AdminPublication['visibility']] ?? '')}`}
                    <span className="block text-xs text-faint">{formatDateTime(e.at)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
      </PageBody>
      <Modal
        open={!!deciding}
        onClose={() => setDeciding(null)}
        title={spec ? t(spec.title) : ''}
        footer={
          <>
            <Button onClick={() => setDeciding(null)}>{t('취소')}</Button>
            <Button variant={spec?.danger ? 'danger' : 'primary'} disabled={busy || short} onClick={() => void decide()}>
              {spec ? t(spec.title) : ''}
            </Button>
          </>
        }
      >
        {error && (
          <p role="alert" className="text-base text-danger">
            {error}
          </p>
        )}
        <Field label={spec ? t(spec.hint) : ''}>
          <Textarea rows={3} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} data-testid="admin-review-reason" />
        </Field>
      </Modal>
    </>
  )
}


/** Abuse review: reason first (audit log, shown to the owner); personal details masked. */
function ConversationsCard({ id }: { id: string }) {
  const t = useT()
  const [asking, setAsking] = useState(false)
  const [reason, setReason] = useState('')
  const [given, setGiven] = useState('')
  const [rows, setRows] = useState<BotConversationRow[] | null>(null)
  const [open, setOpen] = useState<(BotConversationRow & { messages: BotTranscriptMessage[] }) | null>(null)
  const [error, setError] = useState<string | null>(null)

  const start = async () => {
    setError(null)
    try {
      setRows(await adminPublicationsApi.conversations(id, reason.trim()))
      setGiven(reason.trim())
      setAsking(false)
    } catch (err) {
      setError(errorMessage(err, t('대화를 열지 못했습니다.')))
    }
  }

  return (
    <Card className="space-y-3 p-4" data-testid="admin-conversations">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-sm text-faint">{t('대화 열람')}</p>
        {rows === null && (
          <Button size="sm" onClick={() => setAsking(true)} data-testid="admin-conversations-open">
            <Eye size={13} />
            {t('대화 열람')}
          </Button>
        )}
      </div>
      <p className="text-sm text-muted">
        {t('남용 신고를 확인할 때만 엽니다. 사유와 열람한 날은 감사 기록과 만든 사람의 화면에 남고, 개인정보는 가려서 보입니다.')}
      </p>
      {rows !== null &&
        (rows.length === 0 ? (
          <p className="text-sm text-faint">{t('대화가 없습니다.')}</p>
        ) : (
          <div className="grid gap-3 xl:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
            <ul className="space-y-1" data-testid="admin-conversation-list">
              {rows.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() =>
                      void adminPublicationsApi
                        .conversation(id, row.id, given)
                        .then(setOpen, (err) => setError(errorMessage(err, t('대화를 열지 못했습니다.'))))
                    }
                    className={
                      'w-full rounded-control border px-3 py-2 text-left text-sm transition-colors ' +
                      (open?.id === row.id ? 'border-accent bg-accent-soft' : 'border-line hover:bg-elevated')
                    }
                  >
                    <span className="block truncate">{row.title || t('제목 없음')}</span>
                    <span className="block text-xs text-faint">
                      {formatDateTime(row.updatedAt)} · {t('질문 {n}개').replace('{n}', String(row.turns ?? 0))}
                      {row.visitor?.byKey && ` · ${t('API·MCP 키')}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="min-w-0 space-y-3" data-testid="admin-conversation-transcript">
              {open ? (
                open.messages.map((m) => (
                  <div key={m.id} className={m.role === 'user' ? 'rounded-card bg-elevated px-3 py-2' : ''}>
                    <p className="mb-1 text-xs font-medium text-faint">{m.role === 'user' ? t('질문') : t('답변')}</p>
                    <Markdown>{m.content}</Markdown>
                  </div>
                ))
              ) : (
                <p className="text-sm text-faint">{t('대화를 고르세요.')}</p>
              )}
            </div>
          </div>
        ))}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <Modal
        open={asking}
        onClose={() => setAsking(false)}
        title={t('대화 열람')}
        footer={
          <>
            <Button onClick={() => setAsking(false)}>{t('취소')}</Button>
            <Button variant="primary" disabled={reason.trim().length < 4} onClick={() => void start()} data-testid="admin-conversations-confirm">
              {t('열람')}
            </Button>
          </>
        }
      >
        <Field label={t('열람 사유 (감사 기록과 만든 사람의 화면에 남습니다)')}>
          <Textarea rows={3} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} data-testid="admin-conversations-reason" />
        </Field>
      </Modal>
    </Card>
  )
}
