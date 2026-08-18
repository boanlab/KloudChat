import { Bot, Boxes, PanelRight } from 'lucide-react'
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
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** Empty state shown before the first prompt of a fresh session. */
function Intro({ kind }: { kind: SessionKind }) {
  const t = useT()
  const { user, send, setDraft } = useStore()
  const navigate = useNavigate()
  const meta = kindMeta[kind]
  const Icon = meta.icon
  const start = (prompt: string) =>
    void send(null, kind, prompt, {
      onSession: (id) => navigate(`/s/${id}`, { replace: true }),
    })

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-4 pb-8">
      <div className="animate-fade-up mb-7 text-center">
        <div
          className="mx-auto mb-4 grid size-11 place-items-center rounded-panel text-white"
          style={{ background: meta.color }}
        >
          <Icon size={20} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {kind === 'chat' ? t('안녕하세요, {name}님').replace('{name}', user?.name ?? '') : t(meta.label)}
        </h1>
        {/* 챗은 인사하고 나머지는 설명한다. 인사를 `tagline` 에 두면 홈 카드와
            로그인 목록이 기능 나열 한가운데서 독자에게 인사하게 된다. */}
        <p className="mt-1.5 text-base text-muted">
          {kind === 'chat' ? t('무엇을 도와드릴까요?') : t(meta.tagline)}
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
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
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <TemplateGallery kind={kind} onPick={setDraft} />
        <DesignGallery kind={kind} />
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
              <Intro kind={kind} />
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
