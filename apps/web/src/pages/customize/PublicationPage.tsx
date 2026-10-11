import { Ban, Clock, Copy, ExternalLink, Loader2, PauseCircle, PlayCircle, RotateCcw, Trash2, Upload, XCircle } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { HubTopBar } from '@/components/customize/DetailLayout'
import { PageBody } from '@/components/layout/AppShell'
import { AdminReadsCard, ApiTab, ChannelsCard } from '@/components/publishing/BotChannels'
import { PublicationSettingsForm } from '@/components/publishing/PublicationSettingsForm'
import { ScopePicker } from '@/components/publishing/ScopePicker'
import { Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, LoadingState, Select, Tabs } from '@/components/ui'
import {
  ApiError,
  errorMessage,
  publicationsApi,
  type BotConversationRow,
  type BotTranscriptMessage,
  type BotVisibility,
  type BotVisitor,
  type Publication,
  type PublicationStats,
} from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import {
  botUrl,
  creditsCap,
  PAUSE_LABEL,
  reviewLine,
  SCOPE_ERROR,
  SHOW_PROCESS_LABEL,
  STATUS_LABEL,
  toSettings,
  useBotAttention,
  VISIBILITIES,
  VISIBILITY_LABEL,
  type SettingsDraft,
} from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { cn, formatDate } from '@/lib/utils'

type Tab = 'overview' | 'stats' | 'conversations' | 'api' | 'versions' | 'settings'
const TABS: Tab[] = ['overview', 'stats', 'conversations', 'api', 'versions', 'settings']
const TAB_LABEL: Record<Tab, string> = {
  overview: '개요',
  stats: '통계',
  conversations: '보낸 대화',
  api: 'API',
  versions: '버전',
  settings: '설정',
}

function draftOf(pub: Publication): SettingsDraft {
  return {
    title: pub.title,
    description: pub.description,
    greeting: pub.branding.greeting,
    prompts: pub.branding.suggestedPrompts.join('\n'),
    placeholder: pub.branding.placeholder,
    showProcess: pub.showProcess,
    showSources: pub.showSources,
    retentionDays: pub.retentionDays,
    conversationView: pub.conversationView,
    limits: pub.limits,
  }
}

const day = (iso: string | null | undefined) => (iso ? formatDate(iso) : '')

/** /customize/publications/:id — a published bot's owner screen. */
export function PublicationPage() {
  const t = useT()
  const navigate = useNavigate()
  const { publicationId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') as Tab | null
  const tab: Tab = raw && TABS.includes(raw) ? raw : 'overview'
  const [pub, setPub] = useState<Publication | null>(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const refreshBadge = useBotAttention((s) => s.refresh)

  const load = useCallback(async () => {
    try {
      setPub(await publicationsApi.get(publicationId))
      // Opening the page is seeing what changed: the badge follows.
      void refreshBadge()
    } catch {
      setMissing(true)
    }
  }, [publicationId, refreshBadge])
  useEffect(() => {
    void load()
  }, [load])

  const run = async (work: () => Promise<Publication | void>) => {
    setBusy(true)
    setError(null)
    try {
      const next = await work()
      if (next) setPub(next)
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setError(SCOPE_ERROR[code] ? t(SCOPE_ERROR[code]) : errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  if (missing) {
    return (
      <>
        <HubTopBar tab="agents" tabLabel="에이전트" title="" />
        <PageBody>
          <EmptyState icon={<PauseCircle size={18} />} title={t('이 챗봇을 찾을 수 없습니다')} description={t('지워졌거나 볼 수 없는 챗봇입니다.')} />
        </PageBody>
      </>
    )
  }
  if (!pub) {
    return (
      <>
        <HubTopBar tab="agents" tabLabel="에이전트" title="" />
        <PageBody>
          <LoadingState />
        </PageBody>
      </>
    )
  }

  const paused = pub.status === 'paused'
  const active = pub.versions?.find((v) => v.active)
  const review = reviewLine(pub, t)

  return (
    <>
      <HubTopBar tab="agents" tabLabel="에이전트" title={pub.title} />
      <PageBody>
        <header className="flex flex-wrap items-start gap-3 border-b border-line pb-5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight break-words">{pub.title}</h1>
              <Badge tone={pub.status === 'approved' ? 'success' : 'warn'} >{t(STATUS_LABEL[pub.status])}</Badge>
              {pub.reviewState === 'pending' && <Badge tone="accent">{t('승인 대기')}</Badge>}
            </div>
            <p className="mt-1 text-sm text-faint">
              {t('챗봇')} · {t(VISIBILITY_LABEL[pub.visibility])}
              {pub.agentName && (
                <>
                  {' · '}
                  <Link to={`/customize/agents/${pub.agentId}`} className="hover:underline">
                    {t('에이전트: {name}').replace('{name}', pub.agentName)}
                  </Link>
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <a href={`/b/${pub.slug}`} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line px-3 text-base hover:bg-elevated">
              <ExternalLink size={14} aria-hidden />
              {t('열기')}
            </a>
            {pub.status === 'approved' && (
              <Button disabled={busy} onClick={() => void run(() => publicationsApi.update(pub.id, { status: 'paused' }))}>
                <PauseCircle size={14} />
                {t('일시 중지')}
              </Button>
            )}
            {paused && pub.pauseReason !== 'admin' && (
              <Button variant="primary" disabled={busy} onClick={() => void run(() => publicationsApi.update(pub.id, { status: 'approved' }))}>
                <PlayCircle size={14} />
                {t('다시 열기')}
              </Button>
            )}
          </div>
        </header>

        {error && (
          <p role="alert" className="mt-4 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
            {error}
          </p>
        )}
        {paused && (
          <p role="status" className="mt-4 rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-base text-warn" data-testid="publication-paused">
            {t('일시 중지되어 방문자가 질문할 수 없습니다.')} {t(PAUSE_LABEL[pub.pauseReason] ?? '')}
            {pub.pauseReason === 'admin' && pub.reviewNote && ` ${t('사유: {reason}').replace('{reason}', pub.reviewNote)}`}
          </p>
        )}
        {review && (
          <div
            role="status"
            data-testid="publication-review"
            className={cn(
              'mt-4 flex flex-wrap items-center gap-2 rounded-control border px-3 py-2 text-base',
              pub.reviewState === 'rejected' ? 'border-danger/30 bg-danger/5 text-danger' : 'border-accent/30 bg-accent-soft text-fg',
            )}
          >
            {pub.reviewState === 'rejected' ? <XCircle size={15} aria-hidden /> : <Clock size={15} aria-hidden />}
            <span className="min-w-0 flex-1">{review}</span>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => publicationsApi.withdrawRequest(pub.id))}>
              {pub.reviewState === 'rejected' ? t('닫기') : t('요청 취소')}
            </Button>
          </div>
        )}
        {!review && pub.reviewNote && !(paused && pub.pauseReason === 'admin') && (
          <p role="status" className="mt-4 rounded-control border border-line bg-elevated px-3 py-2 text-base text-muted" data-testid="publication-note">
            {t('관리자 메모: {reason}').replace('{reason}', pub.reviewNote)}
          </p>
        )}

        <div className="mt-5">
          <Tabs
            tabs={TABS.map((id) => ({ id, label: t(TAB_LABEL[id]) }))}
            value={tab}
            onChange={(next) => setParams({ tab: next }, { replace: true })}
          />
        </div>

        <div className="pt-5">
          {tab === 'overview' && (
            <div className="space-y-4">
              <ScopeCard key={pub.updatedAt} pub={pub} busy={busy} onSave={(visibility, credits) => run(() => publicationsApi.setScope(pub.id, visibility, credits))} />
              <ChannelsCard key={`channels-${pub.updatedAt}`} pub={pub} onChange={setPub} />
              <Card className="space-y-2 p-4">
                <p className="text-sm text-faint">{t('주소')}</p>
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <code className="min-w-0 flex-1 truncate text-sm" data-testid="publication-url">{botUrl(pub.slug)}</code>
                  <Button size="sm" variant="ghost" onClick={async () => setCopied(await copyText(botUrl(pub.slug)))}>
                    <Copy size={13} />
                    {copied ? t('복사됨') : t('복사')}
                  </Button>
                </div>
              </Card>
              <div className="grid gap-4 sm:grid-cols-2">
                <Card className="space-y-1 p-4">
                  <p className="text-sm text-faint">{t('지금 판')}</p>
                  <p className="text-base">
                    {active ? t('{n}판').replace('{n}', String(active.number)) : '—'}
                    {active && <span className="text-faint"> · {day(active.createdAt)}</span>}
                  </p>
                  {active && <p className="text-sm text-muted">{t('답하는 모델: {model}').replace('{model}', active.model)}</p>}
                </Card>
                <Card className="space-y-1 p-4">
                  <p className="text-sm text-faint">{t('만든 사람이 보는 것')}</p>
                  <p className="text-base">
                    {pub.conversationView === 'on'
                      ? t('대화 열람: 방문자가 동의한 뒤의 대화')
                      : t('통계와 방문자가 보낸 대화만')}
                  </p>
                  <p className="text-sm text-muted">{t('대화는 마지막 질문 뒤 {n}일이 지나면 지워집니다.').replace('{n}', String(pub.retentionDays))}</p>
                </Card>
              </div>
              <Card className="space-y-1 p-4">
                <p className="text-sm text-faint">{t('방문자에게 보이는 것')}</p>
                <p className="text-base">
                  {t('답을 만드는 과정')}: {t(SHOW_PROCESS_LABEL[pub.showProcess])} · {t('출처 목록')}: {pub.showSources ? t('보임') : t('숨김')}
                </p>
                <p className="text-sm text-muted">
                  {t('답은 공개한 판의 지시와 자료로만 하고, 내 메모리·개인 맞춤 설정·다른 파일은 쓰지 않습니다. 사용량은 내 크레딧으로 계산됩니다.')}
                </p>
              </Card>
              <AdminReadsCard pub={pub} />
            </div>
          )}
          {tab === 'api' && <ApiTab pub={pub} />}
          {tab === 'stats' && <StatsTab id={pub.id} />}
          {tab === 'conversations' && <ConversationsTab pub={pub} />}
          {tab === 'versions' && (
            <VersionsTab
              pub={pub}
              busy={busy}
              onNew={(note) => run(() => publicationsApi.newVersion(pub.id, note))}
              onActivate={(versionId) => run(() => publicationsApi.activate(pub.id, versionId))}
            />
          )}
          {tab === 'settings' && (
            <SettingsTab
              key={pub.updatedAt}
              pub={pub}
              busy={busy}
              onSave={(draft) => run(() => publicationsApi.update(pub.id, toSettings(draft)))}
              onDelete={() => setDeleting(true)}
            />
          )}
        </div>
      </PageBody>
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={() =>
          void publicationsApi.remove(pub.id).then(() => navigate('/bots'), (err) => setError(errorMessage(err, t('지우지 못했습니다.'))))
        }
        title={t('챗봇 지우기')}
        description={t('챗봇과 그 대화·통계·판이 모두 지워집니다. 에이전트는 그대로 남습니다.')}
      />
    </>
  )
}

/** Who the bot is open to: narrowing applies at once, widening is asked of an administrator. */
function ScopeCard({
  pub,
  busy,
  onSave,
}: {
  pub: Publication
  busy: boolean
  onSave: (visibility: BotVisibility, credits?: number) => Promise<void>
}) {
  const t = useT()
  const [value, setValue] = useState<BotVisibility>(pub.requestedVisibility || pub.visibility)
  const [credits, setCredits] = useState(pub.limits.creditsPerDay ? String(pub.limits.creditsPerDay) : '')
  const rank = (v: BotVisibility) => VISIBILITIES.indexOf(v)
  const wider = rank(value) > rank(pub.visibility)
  const cap = creditsCap(credits)
  const changed = value !== pub.visibility || (value !== 'internal' && cap !== pub.limits.creditsPerDay)
  const asked = pub.reviewState === 'pending' && pub.requestedVisibility === value
  return (
    <Card className="space-y-3 p-4" data-testid="publication-scope">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-base font-medium">{t('공개 범위')}</p>
        <p className="text-sm text-faint">{t('지금: {scope}').replace('{scope}', t(VISIBILITY_LABEL[pub.visibility]))}</p>
      </div>
      <ScopePicker value={value} onChange={setValue} credits={credits} onCredits={setCredits} current={pub.visibility} />
      <div className="flex flex-wrap items-center justify-end gap-2">
        {wider && <p className="min-w-0 flex-1 text-sm text-muted">{t('관리자가 승인할 때까지 지금 범위로 답합니다.')}</p>}
        <Button
          variant="primary"
          disabled={busy || !changed || asked || (value !== 'internal' && cap === undefined)}
          onClick={() => void onSave(value, value !== 'internal' ? cap : undefined)}
        >
          {wider ? t('승인 요청') : t('범위 바꾸기')}
        </Button>
      </div>
    </Card>
  )
}

function StatsTab({ id }: { id: string }) {
  const t = useT()
  const [days, setDays] = useState(30)
  const [stats, setStats] = useState<PublicationStats | null>(null)
  useEffect(() => {
    let live = true
    publicationsApi.stats(id, days).then((s) => live && setStats(s)).catch(() => undefined)
    return () => {
      live = false
    }
  }, [id, days])
  const max = useMemo(() => Math.max(1, ...(stats?.daily ?? []).map((d) => d.turns)), [stats])
  if (!stats) return <LoadingState />
  const cards: [string, number][] = [
    ['질문', stats.totals.turns],
    ['대화', stats.totals.conversations],
    ['방문자', stats.totals.visitorsAll],
    ['답하지 못한 질문', stats.totals.unanswered],
    ['👍', stats.totals.up],
    ['👎', stats.totals.down],
  ]
  return (
    <div className="space-y-5" data-testid="publication-stats">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">{t('개별 대화는 보이지 않고, 아래 통계만 보입니다. 질문 문장은 개인정보를 가려서 보여 줍니다.')}</p>
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} aria-label={t('기간')} className="w-32">
          {[7, 30, 90].map((d) => (
            <option key={d} value={d}>
              {t('최근 {n}일').replace('{n}', String(d))}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {cards.map(([label, value]) => (
          <Card key={label} className="p-3">
            <p className="text-sm text-faint">{t(label)}</p>
            <p className="text-xl font-semibold tabular-nums">{value.toLocaleString()}</p>
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <p className="mb-3 text-base font-medium">{t('날짜별 질문')}</p>
        <div className="flex h-28 items-end gap-0.5" role="img" aria-label={t('날짜별 질문')}>
          {stats.daily.map((d) => (
            <div key={d.date} className="flex-1 rounded-t-sm bg-accent/70" style={{ height: `${(d.turns / max) * 100}%`, minHeight: d.turns ? 2 : 0 }} title={`${d.date} · ${d.turns}`} />
          ))}
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <p className="mb-2 text-base font-medium">{t('자주 나온 주제')}</p>
          {stats.topics.length === 0 ? (
            <p className="text-sm text-faint">{t('아직 없습니다.')}</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5" data-testid="publication-topics">
              {stats.topics.map((topic) => (
                <li key={topic.word} className="rounded-full border border-line px-2.5 py-0.5 text-sm">
                  {topic.word} <span className="text-faint">{topic.count}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="p-4">
          <p className="mb-2 text-base font-medium">{t('답하지 못한 질문')}</p>
          {stats.unanswered.length === 0 ? (
            <p className="text-sm text-faint">{t('아직 없습니다.')}</p>
          ) : (
            <ul className="space-y-1.5" data-testid="publication-unanswered">
              {stats.unanswered.map((q, i) => (
                <li key={i} className="text-sm">
                  {q.text} <span className="text-faint">· {day(q.at)}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-faint">{t('자료에 없는 질문이 자주 나오면 자료를 보태고 새 판으로 공개하세요.')}</p>
        </Card>
      </div>
    </div>
  )
}

function ConversationsTab({ pub }: { pub: Publication }) {
  const t = useT()
  const [rows, setRows] = useState<BotConversationRow[] | null>(null)
  const [open, setOpen] = useState<(BotConversationRow & { messages: BotTranscriptMessage[] }) | null>(null)
  useEffect(() => {
    publicationsApi.conversations(pub.id).then(setRows).catch(() => setRows([]))
  }, [pub.id])
  if (rows === null) return <LoadingState />
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        {pub.conversationView === 'on'
          ? t('방문자가 보낸 대화와, 대화 열람에 동의한 뒤의 대화입니다. 개인정보는 가려서 보입니다.')
          : t('방문자가 「만든 사람에게 보내기」로 보낸 대화만 보입니다. 개인정보는 가려서 보입니다.')}
      </p>
      {rows.length === 0 ? (
        <EmptyState icon={<Upload size={18} />} title={t('보낸 대화가 없습니다')} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
          <ul className="space-y-1" data-testid="publication-conversations">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => void publicationsApi.conversation(pub.id, row.id).then(setOpen)}
                  className={cn(
                    'w-full rounded-control border px-3 py-2 text-left text-base transition-colors',
                    open?.id === row.id ? 'border-accent bg-accent-soft' : 'border-line hover:bg-elevated',
                  )}
                >
                  <span className="block truncate">{row.title || t('제목 없음')}</span>
                  <span className="block text-xs text-faint">
                    {row.sharedAt ? t('보낸 날 {date}').replace('{date}', day(row.sharedAt)) : t('대화 열람')} · {t('질문 {n}개').replace('{n}', String(row.turns ?? 0))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <Card className="min-w-0 p-4">
            {open ? (
              <div className="space-y-4" data-testid="publication-transcript">
                {open.visitor && (
                  <VisitorBar
                    visitor={open.visitor}
                    onBlock={async () => {
                      const visitor = await publicationsApi.block(pub.id, open.id)
                      setOpen({ ...open, visitor })
                    }}
                  />
                )}
                {open.messages.map((m) => (
                  <div key={m.id} className={cn(m.role === 'user' && 'rounded-card bg-elevated px-3 py-2')}>
                    <p className="mb-1 text-xs font-medium text-faint">
                      {m.role === 'user' ? t('질문') : t('답변')}
                      {m.rating === 'up' && ' · 👍'}
                      {m.rating === 'down' && ' · 👎'}
                    </p>
                    <Markdown>{m.content}</Markdown>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-base text-faint">{t('왼쪽에서 대화를 고르세요.')}</p>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

function visitorName(visitor: BotVisitor, t: (s: string) => string): string {
  const who = visitor.member ? t('구성원') : visitor.byKey ? t('API·MCP 키') : t('로그인하지 않음')
  return `${t('방문자 {label}').replace('{label}', visitor.label)} · ${who}`
}

/** Who sent the conversation, as far as the owner may know, and blocking them. */
function VisitorBar({ visitor, onBlock }: { visitor: BotVisitor; onBlock: () => Promise<void> }) {
  const t = useT()
  const [asking, setAsking] = useState(false)
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line pb-3 text-sm" data-testid="publication-visitor">
      <span className="min-w-0 flex-1 text-muted">{visitorName(visitor, t)}</span>
      {visitor.blocked ? (
        <Badge tone="danger">{t('차단됨')}</Badge>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setAsking(true)}>
          <Ban size={13} />
          {t('이 방문자 차단')}
        </Button>
      )}
      <ConfirmDialog
        open={asking}
        onClose={() => setAsking(false)}
        onConfirm={() => void onBlock().finally(() => setAsking(false))}
        title={t('이 방문자 차단')}
        confirmLabel={t('이 방문자 차단')}
        description={t('이 방문자는 더 이상 이 챗봇에 질문할 수 없습니다. 설정에서 언제든 풀 수 있습니다.')}
      />
    </div>
  )
}

/** The visitors the owner blocked, and unblocking them. */
function BlockedVisitors({ pub }: { pub: Publication }) {
  const t = useT()
  const [rows, setRows] = useState<BotVisitor[] | null>(null)
  useEffect(() => {
    publicationsApi.blocked(pub.id).then(setRows).catch(() => setRows([]))
  }, [pub.id])
  return (
    <Card className="space-y-2 p-4" data-testid="publication-blocked">
      <p className="text-base font-medium">{t('차단한 방문자')}</p>
      {rows === null ? null : rows.length === 0 ? (
        <p className="text-sm text-faint">{t('차단한 방문자가 없습니다. 보낸 대화에서 방문자를 차단할 수 있습니다.')}</p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="min-w-0 flex-1">{visitorName(v, t)}</span>
              <span className="text-faint">{day(v.blockedAt)}</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void publicationsApi.unblock(pub.id, v.id).then(() => setRows(rows.filter((x) => x.id !== v.id)))}
              >
                {t('차단 풀기')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function VersionsTab({
  pub,
  busy,
  onNew,
  onActivate,
}: {
  pub: Publication
  busy: boolean
  onNew: (note: string) => Promise<void>
  onActivate: (versionId: string) => Promise<void>
}) {
  const t = useT()
  const [note, setNote] = useState('')
  const outside = pub.visibility !== 'internal'
  return (
    <div className="space-y-4">
      <Card className="space-y-3 p-4">
        <p className="text-base font-medium">{t('지금 에이전트로 새 판 공개')}</p>
        <p className="text-sm text-muted">{t('에이전트의 지시·자료·검사를 지금 상태로 고정해 새 판으로 엽니다. 문제가 있으면 아래에서 이전 판으로 되돌릴 수 있습니다.')}</p>
        {outside && (
          <p className="text-sm text-warn">{t('로그인하지 않은 사람에게도 열린 챗봇이라 새 판은 관리자가 확인한 뒤 답합니다. 그동안 승인된 판이 답합니다.')}</p>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <Field label={t('바꾼 점 메모')}>
              <Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder={t('예: 2026학년도 학칙으로 교체')} />
            </Field>
          </div>
          <Button variant="primary" disabled={busy} onClick={() => void onNew(note).then(() => setNote(''))}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            {t('새 판 공개')}
          </Button>
        </div>
      </Card>
      <Card className="divide-y divide-line" data-testid="publication-versions">
        {(pub.versions ?? []).map((v) => (
          <div key={v.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 text-base font-medium">
                {t('{n}판').replace('{n}', String(v.number))}
                {v.active && <Badge tone="success">{t('지금 판')}</Badge>}
                {v.pending && <Badge tone="accent">{t('승인 대기')}</Badge>}
                {outside && v.approvedAt && !v.pending && <Badge>{t('승인됨')}</Badge>}
              </p>
              <p className="text-sm text-faint">
                {day(v.createdAt)} · {t('자료 {n}개').replace('{n}', String(v.summary.knowledge?.length ?? 0))} · {v.model}
                {v.note && ` · ${v.note}`}
              </p>
            </div>
            {!v.active && !v.pending && (
              <Button size="sm" disabled={busy} onClick={() => void onActivate(v.id)}>
                <RotateCcw size={13} />
                {t('이 판으로 되돌리기')}
              </Button>
            )}
          </div>
        ))}
      </Card>
    </div>
  )
}

function SettingsTab({
  pub,
  busy,
  onSave,
  onDelete,
}: {
  pub: Publication
  busy: boolean
  onSave: (draft: SettingsDraft) => Promise<void>
  onDelete: () => void
}) {
  const t = useT()
  const [draft, setDraft] = useState<SettingsDraft>(() => draftOf(pub))
  const viewChanged = draft.conversationView !== pub.conversationView || draft.retentionDays !== pub.retentionDays
  return (
    <div className="max-w-2xl space-y-6">
      <PublicationSettingsForm draft={draft} defaults={pub.defaults} onChange={setDraft} viewChanged={viewChanged} />
      <div className="flex justify-end">
        <Button variant="primary" disabled={busy} onClick={() => void onSave(draft)}>
          {t('저장')}
        </Button>
      </div>
      <BlockedVisitors pub={pub} />
      <Card className="flex flex-wrap items-center gap-3 border-danger/30 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('챗봇 지우기')}</p>
          <p className="text-sm text-muted">{t('챗봇과 그 대화·통계·판이 모두 지워집니다. 에이전트는 그대로 남습니다.')}</p>
        </div>
        <Button variant="danger" onClick={onDelete}>
          <Trash2 size={14} />
          {t('지우기')}
        </Button>
      </Card>
    </div>
  )
}
