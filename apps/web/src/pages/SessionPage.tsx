import { ArrowDown, Bot, Boxes, Info, Palette, PanelRight, RotateCw } from 'lucide-react'
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ArtifactPanel } from '@/components/artifacts/ArtifactPanel'
import { Composer, hasUnsentDraft } from '@/components/chat/Composer'
import { ProposalCard } from '@/components/chat/ProposalCard'
import { waitingCardMessageId } from '@/lib/stages'
import { MessageItem } from '@/components/chat/MessageItem'
import { ShareButton } from '@/components/share/ShareButton'
import { DesignGallery } from '@/components/chat/DesignGallery'
import { TopBar } from '@/components/layout/TopBar'
import { JobCard } from '@/components/media/JobCard'
import { Badge, Button, LoadingState } from '@/components/ui'
import { agentKinds, kindMeta } from '@/lib/kinds'
import { templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { startFailure } from '@/lib/failures'
import { useStore } from '@/store/useStore'
import type { Agent, SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** Automatic transcript retries before asking the reader. */
const LOAD_RETRIES = 3
/** Follow distance from the bottom, px. */
const FOLLOW_GAP_PX = 120
/** 「맨 아래로」 threshold, px. */
const AWAY_GAP_PX = 320

/** Theme effect per surface; audio and video unaffected. */
const DESIGN_REACHES: Partial<Record<SessionKind, string>> = {
  chat: '이 대화의 말투를 이 테마에 맞춥니다',
  report: '보고서의 말투와 색, 서체를 이 테마에 맞춥니다',
  slides: '슬라이드의 말투와 색, 서체를 이 테마에 맞춥니다',
  image: '그림의 색과 스타일을 이 테마에 맞춥니다',
}

/** Agent, project and theme of an empty session; the template chip lives in the composer. */
function StartingFrom({
  sessionId,
  kind,
  withoutAgent,
}: {
  sessionId?: string | null
  kind: SessionKind
  withoutAgent?: boolean
}) {
  const t = useT()
  const session = useStore((s) => s.sessions.find((c) => c.id === sessionId))
  const found = useStore((s) => s.agents.find((a) => a.id === session?.agentId))
  const agent = withoutAgent ? undefined : found
  const project = useStore((s) => s.projects.find((p) => p.id === session?.projectId))
  // Agent's theme (or the viewer's own of that name), else the project's: the server's order.
  const design = useStore((s) =>
    s.designs.find((d) => d.id === found?.designSystemId) ??
    (found?.designName ? s.designs.find((d) => d.name === found.designName) : undefined) ??
    s.designs.find((d) => d.id === project?.designSystemId),
  )
  const designReach = DESIGN_REACHES[kind]

  if (!agent && !project && !(design && designReach)) return null

  const rows = [
    agent && {
      key: 'agent',
      icon: <Bot size={13} />,
      label: t('이 에이전트가 답합니다'),
      name: agent.name,
      says: agent.description,
      colour: agent.color,
    },
    project && {
      key: 'project',
      icon: <Boxes size={13} />,
      label: t('이 프로젝트의 지침과 자료를 함께 씁니다'),
      name: `${project.emoji} ${project.name}`.trim(),
      says: project.description,
      colour: undefined,
    },
    design &&
      designReach && {
        key: 'design',
        icon: <Palette size={13} />,
        label: t(designReach),
        name: design.name,
        says: design.description,
        colour: design.tokens.accent,
      },
  ].filter(Boolean) as {
    key: string
    icon: React.ReactNode
    label: string
    name: string
    says: string
    colour?: string
  }[]

  return (
    <div className="animate-fade-up mb-5 rounded-card border border-line bg-panel">
      <p className="border-b border-line px-3.5 py-2 text-xs font-medium tracking-wide text-faint uppercase">
        {t('이 대화가 가지고 시작하는 것')}
      </p>
      <div className="divide-y divide-line">
        {rows.map((row) => (
          <div key={row.key} className="flex items-start gap-2.5 px-3.5 py-2.5">
            <span
              className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-control text-white"
              style={{ background: row.colour ?? 'var(--color-faint)' }}
            >
              {row.icon}
            </span>
            <div className="min-w-0">
              <p className="text-base">
                <span className="font-medium">{row.name}</span>
                <span className="ml-1.5 text-sm text-faint">{row.label}</span>
              </p>
              {row.says && <p className="mt-0.5 text-sm text-muted">{row.says}</p>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** Starters sent as-is. */
function AgentGuide({
  agent,
  sessionId,
  kind,
}: {
  agent: Agent
  sessionId: string | null
  kind: SessionKind
}) {
  const t = useT()
  const navigate = useNavigate()
  const send = useStore((s) => s.send)
  const streaming = useStore((s) => !!sessionId && !!s.running[sessionId])
  const surfaces = agent.kinds.length > 1 ? agentKinds(agent) : []
  if (!agent.guide && agent.starters.length === 0 && surfaces.length === 0) return null
  return (
    <div className="animate-fade-up mx-auto mb-6 w-full max-w-2xl">
      {surfaces.length > 0 && <AgentSurfaces agent={agent} sessionId={sessionId} kind={kind} />}
      {agent.guide && (
        <div className="flex items-start gap-3 rounded-card border border-line bg-panel px-4 py-3 text-base leading-relaxed text-muted">
          <Info size={15} className="mt-1 shrink-0 text-faint" />
          <p className="whitespace-pre-wrap">{agent.guide}</p>
        </div>
      )}
      {agent.starters.length > 0 && (
        <div className="mt-3">
          <p className="mb-2 text-center text-xs font-semibold tracking-wide text-faint uppercase">
            {t('이렇게 시작해 보세요')}
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {agent.starters.map((line) => (
              <button
                key={line}
                disabled={streaming}
                onClick={() =>
                  void send(sessionId, kind, line, {
                    onSession: (id) => navigate(`/s/${id}`, { replace: true }),
                  })
                }
                className="max-w-full rounded-full border border-line bg-panel px-3.5 py-1.5 text-left text-sm text-fg transition-colors hover:border-accent/40 hover:bg-accent-soft disabled:opacity-50"
              >
                {line}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** One start button per agent surface: current marked, others open a new conversation with the same agent. */
function AgentSurfaces({
  agent,
  sessionId,
  kind,
}: {
  agent: Agent
  sessionId: string | null
  kind: SessionKind
}) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const setNotice = useStore((s) => s.setNotice)
  const projectId = useStore((s) => s.sessions.find((c) => c.id === sessionId)?.projectId ?? null)
  const template = useStore((s) => s.designTemplates.find((row) => row.id === agent.renderTemplateId))
  return (
    <div className="mb-3 flex flex-wrap justify-center gap-2" role="group" aria-label={t('시작할 화면')}>
      {agentKinds(agent).map((k) => {
        const Icon = kindMeta[k].icon
        const name =
          template && template.surface === k
            ? t('{name} 만들기').replace('{name}', templateText(template, currentLang() === 'en').name)
            : t('{kind}로 시작').replace('{kind}', t(kindMeta[k].label))
        return (
          <Button
            key={k}
            size="sm"
            variant={k === kind ? 'primary' : 'secondary'}
            aria-pressed={k === kind}
            onClick={() => {
              if (k === kind) return
              void newSession(k, { agentId: agent.id, projectId })
                .then((id) => navigate(`/s/${id}`, { replace: true }))
                .catch((err: unknown) => setNotice(startFailure(err, t)))
            }}
          >
            <Icon size={13} />
            {name}
          </Button>
        )
      })}
    </div>
  )
}

/** Agent chat → its document: report or slides session with the same agent, conversation as material; only surfaces the agent names. */
function HandOffButtons({ agent, sessionId }: { agent: Agent; sessionId: string }) {
  const t = useT()
  const navigate = useNavigate()
  const handOff = useStore((s) => s.handOff)
  const setNotice = useStore((s) => s.setNotice)
  const streaming = useStore((s) => !!s.running[sessionId])
  const template = useStore((s) => s.designTemplates.find((row) => row.id === agent.renderTemplateId))
  const [busy, setBusy] = useState(false)
  const targets = (['report', 'slides'] as const).filter((k) => agent.kinds.includes(k))
  return (
    <>
      {targets.map((k) => {
        const Icon = kindMeta[k].icon
        const name =
          template && template.surface === k
            ? templateText(template, currentLang() === 'en').name
            : t(kindMeta[k].label)
        return (
          <Button
            key={k}
            size="sm"
            disabled={busy || streaming}
            title={t('이 대화를 자료로 같은 에이전트가 {name}을 씁니다').replace('{name}', name)}
            onClick={() => {
              setBusy(true)
              void handOff(
                sessionId,
                k,
                t('지금까지 나눈 대화로 {name} 초안을 만들어 주세요.').replace('{name}', name),
                { onSession: (id) => navigate(`/s/${id}`) },
              )
                .catch((err: unknown) => setNotice(startFailure(err, t)))
                .finally(() => setBusy(false))
            }}
          >
            <Icon size={14} />
            {t('{name} 만들기').replace('{name}', name)}
          </Button>
        )
      })}
    </>
  )
}

function Intro({
  kind,
  sessionId,
}: {
  kind: SessionKind
  sessionId?: string | null
}) {
  const t = useT()
  const user = useStore((s) => s.user)
  const meta = kindMeta[kind]
  const Icon = meta.icon
  const agent = useStore((s) =>
    s.agents.find((a) => a.id === s.sessions.find((c) => c.id === sessionId)?.agentId),
  )
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-4 pb-8">
      <div className="animate-fade-up mb-7 text-center">
        <div
          className="mx-auto mb-4 grid size-11 place-items-center rounded-panel text-white"
          style={{ background: agent ? agent.color : meta.color }}
        >
          {agent ? <Bot size={20} /> : <Icon size={20} />}
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {agent
            ? agent.name
            : kind === 'chat'
              ? t('안녕하세요, {name}님').replace('{name}', user?.name ?? '')
              : t(meta.label)}
        </h1>
        <p className="mt-1.5 text-base text-muted">
          {agent
            ? agent.description || t('이 에이전트로 대화를 시작합니다.')
            : kind === 'chat'
              ? t('무엇을 도와드릴까요?')
              : t(meta.tagline)}
        </p>
        {agent && !agent.guide && (
          <p className="mt-2 text-sm text-faint">
            {t('{kind}에서 이 에이전트의 지시대로 답합니다.').replace('{kind}', t(meta.label))}
          </p>
        )}
      </div>
      {agent && <AgentGuide agent={agent} sessionId={sessionId ?? null} kind={kind} />}
      <StartingFrom sessionId={sessionId} kind={kind} withoutAgent={Boolean(agent)} />
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <DesignGallery kind={kind} sessionId={sessionId ?? null} />
      </div>
    </div>
  )
}

export function SessionPage() {
  const t = useT()
  const { sessionId } = useParams()
  // Selectors, not the store: `MessageItem`'s memo keys on these rows.
  const jobs = useStore((s) => s.jobs)
  const projects = useStore((s) => s.projects)
  const agents = useStore((s) => s.agents)
  const artifacts = useStore((s) => s.artifacts)
  const setActiveSession = useStore((s) => s.setActiveSession)
  const openSession = useStore((s) => s.openSession)
  const streaming = useStore((s) => !!sessionId && !!s.running[sessionId])
  const openArtifactId = useStore((s) => s.openArtifactId)
  const openArtifact = useStore((s) => s.openArtifact)

  const [searchParams, setSearchParams] = useSearchParams()
  // `?artifact=` opens that document instead of the latest.
  const requestedArtifactId = searchParams.get('artifact')
  const session = useStore((s) => s.sessions.find((c) => c.id === sessionId) ?? null)
  const kind: SessionKind = session?.kind ?? 'chat'
  const meta = kindMeta[kind]

  useEffect(() => {
    setActiveSession(session?.id ?? null)
  }, [session?.id, setActiveSession])

  // Left empty (never sent, or its first send refused): dropped on leave unless it holds a draft; deferred a tick for StrictMode remounts and same-id returns.
  const pendingCleanup = useRef<{ id: string; timer: number } | null>(null)
  useEffect(() => {
    const id = sessionId
    if (!id) return
    if (pendingCleanup.current?.id === id) {
      window.clearTimeout(pendingCleanup.current.timer)
      pendingCleanup.current = null
    }
    return () => {
      const timer = window.setTimeout(() => {
        pendingCleanup.current = null
        const row = useStore.getState().sessions.find((c) => c.id === id)
        if (!row) return
        const hasWork =
          row.messages.length > 0 ||
          row.messageCount > 0 ||
          row.artifactId !== null ||
          row.made !== null ||
          hasUnsentDraft(id) ||
          useStore.getState().jobs.some((j) => j.sessionId === id)
        if (!hasWork) void useStore.getState().deleteSession(id)
      }, 0)
      pendingCleanup.current = { id, timer }
    }
  }, [sessionId])

  // List carries no transcript; a session opened by URL fetches its own.
  const loaded = session !== null && session.messages.length > 0
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [failedLoad, setFailedLoad] = useState<{ id: string; attempt: number } | null>(null)
  /** Transcript fetched at least once on this page. */
  const [fetchedId, setFetchedId] = useState<string | null>(null)
  /** Automatic retries spent. */
  const autoRetries = useRef<{ id: string | undefined; count: number }>({ id: undefined, count: 0 })
  useEffect(() => {
    let current = true
    if (sessionId && !loaded) {
      const completed = (fetched: boolean) => {
        if (!current) return
        // Gone row or missing transcript: not a new chat.
        if (fetched && useStore.getState().sessions.some((row) => row.id === sessionId)) setFetchedId(sessionId)
        else setFailedLoad({ id: sessionId, attempt: loadAttempt })
      }
      void openSession(sessionId).then(completed, () => completed(false))
    }
    return () => { current = false }
  }, [sessionId, loaded, openSession, loadAttempt])
  if (autoRetries.current.id !== sessionId) autoRetries.current = { id: sessionId, count: 0 }
  const loadFailed = failedLoad?.id === sessionId && failedLoad?.attempt === loadAttempt
  // Dropped request retried a few times with backoff.
  const retrying = loadFailed && autoRetries.current.count < LOAD_RETRIES
  useEffect(() => {
    if (!retrying) return
    const wait = 1500 * 2 ** autoRetries.current.count
    const timer = window.setTimeout(() => {
      autoRetries.current.count += 1
      setLoadAttempt((attempt) => attempt + 1)
    }, wait)
    return () => window.clearTimeout(timer)
  }, [retrying])
  const retryLoad = () => setLoadAttempt((attempt) => attempt + 1)
  // Turns on the server: no empty greeting while loading.
  const awaitingTranscript =
    session !== null && session !== undefined && session.messages.length === 0 &&
    (session.messageCount > 0 || !!session.pending) && fetchedId !== session.id

  // Messages and jobs: one timeline by creation. Messages keep the transcript's order (a
  // reply made on this screen is stamped by the browser clock, its question by the server's).
  const timeline = useMemo(() => {
    if (!session) return []
    const sessionJobs = jobs
      .filter((j) => j.sessionId === session.id)
      .sort((a, b) => +new Date(a.createdAt) - +new Date(b.createdAt))
    const out: (
      | { type: 'message'; m: (typeof session.messages)[number] }
      | { type: 'job'; j: (typeof sessionJobs)[number] }
    )[] = []
    let next = 0
    for (const m of session.messages) {
      while (next < sessionJobs.length && +new Date(sessionJobs[next].createdAt) < +new Date(m.createdAt)) {
        out.push({ type: 'job', j: sessionJobs[next++] })
      }
      out.push({ type: 'message', m })
    }
    for (; next < sessionJobs.length; next++) out.push({ type: 'job', j: sessionJobs[next] })
    return out
  }, [session, jobs])

  // Outline card: transcript end while pending, above the running turn once accepted.
  const proposalAt = useMemo(() => {
    if (!session?.pending) return -1
    if (!streaming) return timeline.length
    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i]
      if (item.type === 'message' && item.m.role === 'user') return i
    }
    return timeline.length
  }, [session?.pending, streaming, timeline])
  // Not twice when the waiting stage shows it.
  const proposal = session?.pending && !waitingCardMessageId(session) ? (
    <ProposalCard sessionId={session.id} pending={session.pending} kind={kind} />
  ) : null

  const scrollRef = useRef<HTMLDivElement>(null)
  const lastLength = session?.messages.at(-1)?.content.length ?? 0
  const runningProgress = jobs.find(
    (j) => j.sessionId === session?.id && j.status === 'running',
  )?.progress
  // Follow only near the bottom; scrolling up stops it, a button or a new question re-engages.
  const following = useRef(true)
  // Own smooth scroll in flight: not the reader leaving the bottom.
  const settling = useRef(false)
  const [awayFromBottom, setAwayFromBottom] = useState(false)
  const measureFollowing = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight
    if (settling.current) {
      if (gap < FOLLOW_GAP_PX) settling.current = false
      return
    }
    following.current = gap < FOLLOW_GAP_PX
    setAwayFromBottom(gap > AWAY_GAP_PX)
  }, [])
  const scrollToBottom = useCallback((behavior: ScrollBehavior) => {
    const el = scrollRef.current
    if (!el) return
    following.current = true
    setAwayFromBottom(false)
    settling.current = behavior === 'smooth'
    el.scrollTo({ top: el.scrollHeight, behavior })
    // A smooth scroll that never reaches the bottom must not pin this.
    if (settling.current) window.setTimeout(() => { settling.current = false }, 800)
  }, [])
  const lastMessageId = session?.messages.at(-1)?.id
  const lastRole = session?.messages.at(-1)?.role
  useLayoutEffect(() => {
    // A just-sent question brings the bottom into view.
    if (lastRole === 'user') following.current = true
  }, [lastMessageId, lastRole])
  useLayoutEffect(() => {
    if (!following.current) return
    scrollToBottom(streaming ? 'auto' : 'smooth')
  }, [timeline.length, lastLength, runningProgress, streaming, scrollToBottom])
  // New conversation opens at the bottom.
  useEffect(() => {
    following.current = true
    setAwayFromBottom(false)
  }, [session?.id])
  // Screen readers hear an answer land; text read on demand.
  const wasStreaming = useRef(false)
  const [announcement, setAnnouncement] = useState('')
  const lastError = session?.messages.at(-1)?.error
  useEffect(() => {
    if (wasStreaming.current && !streaming) {
      setAnnouncement(lastError ? t('답변이 실패했습니다') : t('답변이 도착했습니다'))
    }
    wasStreaming.current = !!streaming
  }, [streaming, lastError, t])

  // Applied once loaded, then cleared, so a closed panel stays closed.
  useEffect(() => {
    if (!requestedArtifactId || !session) return
    openArtifact(requestedArtifactId)
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        next.delete('artifact')
        return next
      },
      { replace: true },
    )
    // Session by id: identity changes per streamed token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedArtifactId, session?.id, openArtifact, setSearchParams])

  const project = projects.find((p) => p.id === session?.projectId)
  const agent = agents.find((a) => a.id === session?.agentId)
  const sessionArtifact = artifacts.find(
    (a) => a.id === (requestedArtifactId ?? session?.artifactId),
  )
  const Icon = meta.icon

  const loadingTranscript = (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6">
      {loadFailed ? (
        <div role="alert" className="flex max-w-sm flex-col items-center gap-3 text-center">
          <p className="text-base text-muted">{t('최신 내용을 불러오지 못했습니다.')}</p>
          {retrying && <p className="text-sm text-faint">{t('잠시 후 다시 불러옵니다.')}</p>}
          <Button onClick={retryLoad}>
            <RotateCw size={14} />
            {t('다시 시도')}
          </Button>
        </div>
      ) : <LoadingState label={t('대화를 불러오는 중…')} />}
    </div>
  )

  if (!session) {
    return (
      <>
        <TopBar left={<span className="text-base font-medium">{t('작업')}</span>} />
        {loadingTranscript}
      </>
    )
  }

  return (
    <>
      <TopBar
        left={
          <div className="flex min-w-0 items-center gap-2">
            <Icon size={14} className="shrink-0" style={{ color: meta.color }} />
            <span className="truncate text-base font-medium">
              {session?.title ?? t('새 {kind}').replace('{kind}', t(meta.label))}
            </span>
            {project && (
              <Badge tone="accent" className="max-sm:hidden">
                <Boxes size={11} />
                {project.name}
              </Badge>
            )}
            {agent && (
              <Badge className="max-sm:hidden">
                <Bot size={11} />
                {agent.name}
              </Badge>
            )}
          </div>
        }
        right={
          <>
            {session.kind === 'chat' && agent && session.messages.length > 0 && (
              <HandOffButtons agent={agent} sessionId={session.id} />
            )}
            {session && !session.unsent && <ShareButton session={session} />}
            {sessionArtifact && !openArtifactId && (
              <Button size="sm" onClick={() => openArtifact(sessionArtifact.id)}>
                <PanelRight size={14} />
                {t(meta.panelLabel)}
              </Button>
            )}
          </>
        }
      />

      <div className="relative flex min-h-0 flex-1">
        <div className="sr-only" aria-live="polite" role="status">
          {announcement}
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          {awaitingTranscript ? (
            loadingTranscript
          ) : session && (timeline.length > 0 || proposal) ? (
            <>
              <div
                ref={scrollRef}
                onScroll={measureFollowing}
                className="relative min-h-0 flex-1 overflow-y-auto"
                data-testid="transcript"
              >
                <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
                  {/* Per-message boundary: one malformed turn loses only its bubble. */}
                  {timeline.map((item, i) =>
                    item.type === 'message' ? (
                      <Fragment key={item.m.id}>
                        {i === proposalAt && proposal}
                        <ErrorBoundary>
                          <MessageItem
                            message={item.m}
                            sessionId={session.id}
                            streaming={streaming && i === timeline.length - 1}
                            last={i === timeline.length - 1}
                          />
                        </ErrorBoundary>
                      </Fragment>
                    ) : (
                      <JobCard key={item.j.id} job={item.j} />
                    ),
                  )}
                  {proposalAt === timeline.length && proposal}
                </div>
              </div>
              {awayFromBottom && (
                <div className="pointer-events-none relative">
                  <button
                    type="button"
                    onClick={() => scrollToBottom('smooth')}
                    aria-label={t('맨 아래로')}
                    title={t('맨 아래로')}
                    data-testid="scroll-to-bottom"
                    className="pointer-events-auto absolute bottom-3 left-1/2 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-elevated text-muted shadow-md transition-colors hover:text-fg"
                  >
                    <ArrowDown size={16} />
                  </button>
                </div>
              )}
              <Composer
                sessionId={session.id}
                kind={kind}
                projectId={session.projectId}
                autoFocus
              />
            </>
          ) : (
            <>
              <Intro kind={kind} sessionId={session?.id ?? sessionId ?? null} />
              {/* URL id first: the row may not have arrived, and `null` would start a new session. */}
              <Composer
                sessionId={session?.id ?? sessionId ?? null}
                kind={kind}
                projectId={session?.projectId ?? null}
                autoFocus
              />
            </>
          )}
        </div>
        {openArtifactId && <ArtifactPanel />}
      </div>
    </>
  )
}
