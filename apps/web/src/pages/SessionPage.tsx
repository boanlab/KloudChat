import { Bot, Boxes, Info, Palette, PanelRight } from 'lucide-react'
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ArtifactPanel } from '@/components/artifacts/ArtifactPanel'
import { Composer, hasUnsentDraft } from '@/components/chat/Composer'
import { ProposalCard } from '@/components/chat/ProposalCard'
import { MessageItem } from '@/components/chat/MessageItem'
import { ShareButton } from '@/components/share/ShareButton'
import { DesignGallery } from '@/components/chat/DesignGallery'
import { TopBar } from '@/components/layout/TopBar'
import { JobCard } from '@/components/media/JobCard'
import { Badge, Button } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { Agent, SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * What a project's design system does to each surface, in that surface's own
 * terms.
 *
 * Four entries and not six: audio and video are rendered by something the
 * design never reaches. Chat gets the voice alone — an accent colour is not
 * something a sentence can be written in.
 */
const DESIGN_REACHES: Partial<Record<SessionKind, string>> = {
  chat: '이 대화의 말투를 이 디자인에 맞춥니다',
  report: '보고서의 말투와 색, 서체를 이 디자인에 맞춥니다',
  slides: '슬라이드의 말투와 색, 서체를 이 디자인에 맞춥니다',
  image: '그림의 색과 스타일을 이 디자인에 맞춥니다',
}

/**
 * What this conversation is already carrying, before a word is typed.
 *
 * An agent, a project or a 디자인 is chosen on another screen and arrives here
 * as a badge in the top bar. Pressing 실행 on an agent is the clearest case:
 * the screen that opens looks exactly like a blank session. So it is said in
 * the middle of the empty screen, in the terms the choice was made in — an
 * agent's own description rather than its name repeated.
 *
 * A 서식 is deliberately absent: it lands as a named chip inside the composer
 * carrying the × that takes it off again, so naming it here too would put the
 * same two words at both ends of an empty screen with only the lower one
 * actionable. The shared page keeps its 서식 row — no composer there.
 */
function StartingFrom({
  sessionId,
  kind,
  withoutAgent,
}: {
  sessionId?: string | null
  kind: SessionKind
  /** The agent is the headline above, so listing it here would say it twice. */
  withoutAgent?: boolean
}) {
  const t = useT()
  const session = useStore((s) => s.sessions.find((c) => c.id === sessionId))
  const found = useStore((s) => s.agents.find((a) => a.id === session?.agentId))
  const agent = withoutAgent ? undefined : found
  const project = useStore((s) => s.projects.find((p) => p.id === session?.projectId))
  // The design comes with the project or not at all, so it is looked up from
  // the project rather than the session.
  const design = useStore((s) => s.designs.find((d) => d.id === project?.designSystemId))
  const designReach = DESIGN_REACHES[kind]

  // The 디자인 row rides on the project, so a card with neither an agent nor a
  // project has nothing of its own left to say.
  if (!agent && !project) return null

  // Each row says what the thing will *do* to this conversation rather than
  // naming its category. "프로젝트" over a project's name says nothing the
  // name did not; "이 프로젝트의 지침과 자료를 함께 씁니다" is the reason it
  // is worth telling somebody about before they type.
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

/**
 * How to use the agent, and a few sentences to start with.
 *
 * 「에이전트로 시작하면 에이전트 사용법 같은 것을 첫 화면에서 보여줄 수
 * 없나」 — the screen had a name and one line. The guide says what to bring
 * and what a turn does; a starter is a first message, sent as it stands.
 */
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
  if (!agent.guide && agent.starters.length === 0) return null
  return (
    <div className="animate-fade-up mx-auto mb-6 w-full max-w-2xl">
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

/** Empty state shown before the first prompt of a fresh session. */
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
      {/* Pressing 실행 on an agent is a decision about what this conversation
          is for, so the agent says who it is and the product does not greet
          the person over the top of it. */}
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
        {/* 챗은 인사하고 나머지는 설명한다. 인사를 `tagline` 에 두면 홈 카드와
            로그인 목록이 기능 나열 한가운데서 독자에게 인사하게 된다. */}
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
        {/* The cards are drawn in this project's look, which is the one the
            composer under them will render the answer in. */}
        <DesignGallery kind={kind} sessionId={sessionId ?? null} />
      </div>
    </div>
  )
}

export function SessionPage() {
  const t = useT()
  const { sessionId } = useParams()
  // Selectors, not the whole store: this screen re-renders on every streamed
  // chunk regardless (the session row changes), but nothing here should make
  // it re-render for a change in some other conversation or an unrelated
  // slice, and the rows below are what `MessageItem`'s memo keys on.
  const jobs = useStore((s) => s.jobs)
  const projects = useStore((s) => s.projects)
  const agents = useStore((s) => s.agents)
  const artifacts = useStore((s) => s.artifacts)
  const setActiveSession = useStore((s) => s.setActiveSession)
  const openSession = useStore((s) => s.openSession)
  // Whether *this* conversation has a turn in flight.
  const streaming = useStore((s) => !!sessionId && !!s.running[sessionId])
  const openArtifactId = useStore((s) => s.openArtifactId)
  const openArtifact = useStore((s) => s.openArtifact)

  const [searchParams, setSearchParams] = useSearchParams()
    /**
     * A document named by whoever sent us here — the 작업 목록's 원본 작업 열기.
     *
     * The panel otherwise follows `session.artifactId`, this conversation's
     * *latest* result: right while you are working, wrong when somebody arrives
     * asking for a particular file.
     */
  const requestedArtifactId = searchParams.get('artifact')
  const session = useStore((s) => s.sessions.find((c) => c.id === sessionId) ?? null)
  const kind: SessionKind = session?.kind ?? 'chat'
  const meta = kindMeta[kind]

  useEffect(() => {
    setActiveSession(session?.id ?? null)
  }, [session?.id, setActiveSession])

  /**
   * A conversation nobody put anything into. 새 채팅 시작 and 이 프로젝트에서
   * 새로 만들기 create the row on the server before there is a sentence, so
   * leaving before the first send left an empty chat sitting in every list
   * that reads sessions — the project's own tab and the sidebar are one
   * array, so removing it here clears both without touching either.
   *
   * A typed-but-unsent draft is the one thing that keeps an empty session:
   * it already survives navigation on its own (Composer's in-memory map),
   * and deleting the row out from under it would make that draft
   * unreachable rather than merely unsent.
   *
   * The actual check runs a tick later, in a timer cancelled by whatever
   * mounts next for the same id. Deleting straight from the cleanup would
   * also catch StrictMode's dev-only mount → cleanup → mount, which tests
   * that an effect survives being repeated by repeating it once, in the same
   * tick, on the same session — a delete fired there removes what the second
   * mount was about to show. Returning to the same still-empty session before
   * the timer fires cancels it exactly the same way, which is also correct:
   * being looked at is reason enough not to go.
   */
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

  // The sidebar list carries titles only, so a session opened by URL (a reload,
  // a shared link, a back button) has to fetch its own transcript.
  const loaded = session !== null && session.messages.length > 0
  useEffect(() => {
    if (sessionId && !loaded) void openSession(sessionId)
  }, [sessionId, loaded, openSession])

  /**
   * Messages and jobs share one timeline. For image and video the job card is
   * the visible unit of work, so it has to sit inline with the prompt that
   * started it rather than in a separate pane.
   */
  const timeline = useMemo(() => {
    if (!session) return []
    const sessionJobs = jobs.filter((j) => j.sessionId === session.id)
    const items = [
      ...session.messages.map((m) => ({ t: m.createdAt, node: { type: 'message' as const, m } })),
      ...sessionJobs.map((j) => ({ t: j.createdAt, node: { type: 'job' as const, j } })),
    ]
    return items.sort((a, b) => +new Date(a.t) - +new Date(b.t)).map((i) => i.node)
  }, [session, jobs])

  /**
   * The outline sits under the message that proposed it. While the answer is
   * still awaited that is the end of the transcript, above the composer where
   * the decision is: read what it means to write, then press the button or
   * type what to change. Once the button is pressed the transcript keeps
   * reading in order — proposal, outline, 「이대로 생성해 주세요」, the work —
   * so the card moves above the question that started the running turn
   * instead of trailing the steps.
   */
  const proposalAt = useMemo(() => {
    if (!session?.pending) return -1
    if (!streaming) return timeline.length
    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i]
      if (item.type === 'message' && item.m.role === 'user') return i
    }
    return timeline.length
  }, [session?.pending, streaming, timeline])
  const proposal = session?.pending ? (
    <ProposalCard sessionId={session.id} pending={session.pending} kind={kind} />
  ) : null

  const scrollRef = useRef<HTMLDivElement>(null)
  const lastLength = session?.messages.at(-1)?.content.length ?? 0
  const runningProgress = jobs.find(
    (j) => j.sessionId === session?.id && j.status === 'running',
  )?.progress
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: streaming ? 'auto' : 'smooth' })
  }, [timeline.length, lastLength, runningProgress, streaming])

  /**
   * Spent on arrival, and only once the session it names is in hand:
   * `setActiveSession` above opens the panel on the session's own document,
   * and that runs again when a reload finally loads the list. Clearing the
   * query afterwards is what lets a closed panel stay closed.
   */
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
    // The session is a dependency by id: its object identity changes on every
    // streamed token, and reopening the panel mid-answer is not what this is.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedArtifactId, session?.id, openArtifact, setSearchParams])

  const project = projects.find((p) => p.id === session?.projectId)
  const agent = agents.find((a) => a.id === session?.agentId)
  const sessionArtifact = artifacts.find(
    (a) => a.id === (requestedArtifactId ?? session?.artifactId),
  )
  const Icon = meta.icon

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
            {session && <ShareButton session={session} />}
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
        <div className="flex min-w-0 flex-1 flex-col">
          {session && timeline.length > 0 ? (
            <>
              <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
                  {/* Per-message: one malformed turn loses its own bubble, not
                      the conversation around it. */}
                  {timeline.map((item, i) =>
                    item.type === 'message' ? (
                      <Fragment key={item.m.id}>
                        {i === proposalAt && proposal}
                        <ErrorBoundary>
                          <MessageItem
                            message={item.m}
                            sessionId={session.id}
                            streaming={streaming && i === timeline.length - 1}
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
              {/* The URL is authoritative, not the store lookup. On `/s/:id`
                  the row may not have arrived yet — falling back to `null` there
                  makes the composer start a *new* conversation, silently
                  discarding the agent or project the old one carried. */}
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
