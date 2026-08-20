import { Bot, Boxes, LayoutGrid, PanelRight } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ArtifactPanel } from '@/components/artifacts/ArtifactPanel'
import { Composer } from '@/components/chat/Composer'
import { MessageItem } from '@/components/chat/MessageItem'
import { ShareButton } from '@/components/share/ShareButton'
import { DesignGallery } from '@/components/chat/DesignGallery'
import { TemplateGallery } from '@/components/chat/TemplateGallery'
import { TopBar } from '@/components/layout/TopBar'
import { JobCard } from '@/components/media/JobCard'
import { Badge, Button, EmptyState } from '@/components/ui'
import { templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * What this conversation is already carrying, before a word is typed.
 *
 * An agent, a project or a 서식 is chosen on another screen and then arrives
 * here as a badge in the top bar — which is next to nothing. Pressing 실행 on
 * an agent is the clearest case: the screen that opens looks exactly like a
 * blank session, so the one thing the person just decided is the one thing
 * the screen does not say.
 *
 * Said in the middle of the empty screen instead, where the answer is about
 * to appear, and in the terms the choice was made in — an agent's own
 * description rather than its name repeated.
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
  const english = currentLang() === 'en'
  const session = useStore((s) => s.sessions.find((c) => c.id === sessionId))
  const found = useStore((s) => s.agents.find((a) => a.id === session?.agentId))
  const agent = withoutAgent ? undefined : found
  const project = useStore((s) => s.projects.find((p) => p.id === session?.projectId))
  const pending = useStore((s) => s.pendingTemplate)
  const templates = useStore((s) => s.designTemplates)
  // The composer's own rule for which shape is in force: this turn's pick if
  // there is one, otherwise whatever the session was started with.
  const format =
    (pending?.surface === kind ? pending : null) ??
    templates.find((row) => row.id === session?.renderTemplateId) ??
    null

  if (!agent && !project && !format) return null

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
    format && {
      key: 'format',
      icon: <LayoutGrid size={13} />,
      label: t('결과물이 이 서식으로 나옵니다'),
      name: templateText(format, english).name,
      says: templateText(format, english).description,
      colour: undefined,
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

/** Empty state shown before the first prompt of a fresh session. */
function Intro({
  kind,
  sessionId,
  projectId,
}: {
  kind: SessionKind
  sessionId?: string | null
  projectId?: string | null
}) {
  const t = useT()
  const { user, send, setDraft } = useStore()
  const navigate = useNavigate()
  const meta = kindMeta[kind]
  const Icon = meta.icon
  const agent = useStore((s) =>
    s.agents.find((a) => a.id === s.sessions.find((c) => c.id === sessionId)?.agentId),
  )
  // Into *this* conversation, not a new one. The empty screen also stands in
  // for a session that already exists and already carries a project, a 서식
  // or an agent — passing `null` here started a fresh session and left all
  // three behind, one click after the screen above finished explaining them.
  //
  // A picture or a clip is not sent from here at all. Its length, its
  // resolution and its voice are decided in the composer, and the button that
  // starts it is down there too, so an example card that fired the request
  // would be deciding all of that on the person's behalf. It hands them the
  // sentence instead, which is what the 시작점 gallery two rows below already
  // does.
  const start = (prompt: string) => {
    if (kind === 'image' || kind === 'av') {
      setDraft(prompt)
      return
    }
    void send(sessionId ?? null, kind, prompt, {
      projectId,
      onSession: (id) => navigate(`/s/${id}`, { replace: true }),
    })
  }

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
        {agent && (
          <p className="mt-2 text-sm text-faint">
            {t('{kind}에서 이 에이전트의 지시대로 답합니다.').replace('{kind}', t(meta.label))}
          </p>
        )}
      </div>
      <StartingFrom sessionId={sessionId} kind={kind} withoutAgent={Boolean(agent)} />
      {/* The surface's generic openings, unless an agent is driving. An agent
          is a stance somebody chose on purpose, and "이번 주 회의록 정리해줘"
          under it is the product talking over them. */}
      {!agent && (
        <div
          role="group"
          aria-label={t('이렇게 시작해 보세요')}
          className="grid gap-2 sm:grid-cols-2"
        >
          {meta.examples.map((prompt) => (
            <button
              key={prompt}
              onClick={() => start(prompt)}
              className="animate-fade-up rounded-card border border-line bg-panel px-3.5 py-3 text-left text-base text-muted transition-colors hover:border-line-strong hover:bg-elevated hover:text-fg"
            >
              {t(prompt)}
            </button>
          ))}
        </div>
      )}
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <TemplateGallery kind={kind} />
        {/* The cards are drawn in this project's look, which is the one the
            composer under them will render the answer in. */}
        <DesignGallery kind={kind} projectId={projectId} />
      </div>
    </div>
  )
}

export function SessionPage({ newKind }: { newKind?: SessionKind }) {
  const t = useT()
  const { sessionId } = useParams()
  const {
    sessions,
    jobs,
    projects,
    agents,
    artifacts,
    enabledKinds,
    setActiveSession,
    openSession,
    streaming,
    openArtifactId,
    openArtifact,
  } = useStore()

  const navigate = useNavigate()
  const session = sessions.find((s) => s.id === sessionId) ?? null
  const kind: SessionKind = session?.kind ?? newKind ?? 'chat'
  const meta = kindMeta[kind]
  /**
   * A surface an administrator has switched off.
   *
   * The sidebar and the home screen already hide these, but the URL still
   * works — a bookmark, a shared link, a browser autocomplete — and the screen
   * that came up looked entirely normal. Typing into it did nothing at all:
   * the server refuses to open the session, and the refusal surfaced as an
   * unhandled rejection nobody could see.
   */
  const offHere = newKind !== undefined && !enabledKinds.includes(kind)

  useEffect(() => {
    setActiveSession(session?.id ?? null)
  }, [session?.id, setActiveSession])

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

  const project = projects.find((p) => p.id === session?.projectId)
  const agent = agents.find((a) => a.id === session?.agentId)
  const sessionArtifact = artifacts.find((a) => a.id === session?.artifactId)
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
              <Badge tone="accent">
                <Boxes size={11} />
                {project.name}
              </Badge>
            )}
            {agent && (
              <Badge>
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
          {offHere ? (
            <EmptyState
              icon={<Icon size={18} />}
              title={t('{kind} 기능이 꺼져 있습니다').replace('{kind}', t(meta.label))}
              description={t('관리자가 이 워크스페이스에서 사용하지 않도록 설정했습니다. 필요하면 관리자에게 요청하세요.')}
              action={
                <Button variant="primary" onClick={() => navigate('/')}>
                  {t('홈으로')}
                </Button>
              }
            />
          ) : session && timeline.length > 0 ? (
            <>
              <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
                  {/* Per-message: one malformed turn loses its own bubble, not
                      the conversation around it. */}
                  {timeline.map((item, i) =>
                    item.type === 'message' ? (
                      <ErrorBoundary key={item.m.id}>
                        <MessageItem
                          message={item.m}
                          sessionId={session.id}
                          streaming={streaming && i === timeline.length - 1}
                        />
                      </ErrorBoundary>
                    ) : (
                      <JobCard key={item.j.id} job={item.j} />
                    ),
                  )}
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
              <Intro
                kind={kind}
                sessionId={session?.id ?? sessionId ?? null}
                projectId={session?.projectId ?? null}
              />
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
