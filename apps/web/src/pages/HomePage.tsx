import { ArrowRight, Bot, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, EmptyState } from '@/components/ui'
import { Composer } from '@/components/chat/Composer'
import { DesignGallery } from '@/components/chat/DesignGallery'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { startFailure } from '@/lib/failures'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * The first screen, rebuilt around the thing people came here to do.
 *
 * It used to be five equal cards over a list of recent work, and the box you
 * type into was not on it at all — you picked a card, landed on another screen,
 * and found the input at the bottom of that one. So the main flow began with a
 * decision nobody had made yet ("which of five surfaces is this?") and the
 * primary control of the whole product was two screens and one scroll away.
 *
 * Three changes, and they are one change.
 *
 * **The composer is the centre.** You arrive and you can type. The five
 * surfaces did not disappear — they became a row attached to the input, which
 * is where that choice belongs: it shapes what the sentence you are already
 * writing will produce, and it is a smaller decision than the sentence.
 *
 * **The agents are beside it.** What this product is for is having a named
 * thing with its own instructions, tools and documents do the work, and that
 * was reachable only through a workspace list further down the sidebar. A
 * layout that opens on a chat box and nothing else describes a chat app.
 *
 * **The rest is below the fold it belongs under.** Work in progress and work
 * already done are things you come back for, not things you start with.
 */
export function HomePage({ initialKind }: { initialKind?: SessionKind }) {
  const t = useT()
  const navigate = useNavigate()
  const { user, sessions, jobs, agents, enabledKinds, newSession, setNotice } = useStore()
  //: The 서식 already picked for this surface, if any. Read here only to
  //: decide whether the rail below would be repeating it.
  //: Which surface the composer is currently writing for. Local, and only for
  //: as long as this screen is open — a session gets its kind at creation.
  const [kind, setKind] = useState<SessionKind>(initialKind ?? 'chat')

  /**
   * The route can change while this screen stays mounted. `/` and `/new/:kind`
   * both render this component, so React keeps it in place and the initial
   * value above is read once — 서식에서 시작 navigates from one to the other,
   * and without this the surface stays whatever it already was. The pick then
   * lands on a composer of the wrong kind, where `shownTemplate` discards it
   * for not matching and the chip never appears.
   */
  useEffect(() => {
    if (initialKind) setKind(initialKind)
  }, [initialKind])

  /**
   * 시작 화면에는 앞 대화의 문서가 따라오지 않는다.
   *
   * `openArtifactId` is global and nothing on the way here cleared it, so
   * leaving a deck and pressing 새로 만들기 opened an empty composer with the
   * previous conversation's document still beside it — and it stayed there
   * through the next turn, so somebody watching their 보고서 being written
   * was looking at a slide deck from another session while they waited.
   *
   * Cleared on arrival rather than on leaving: there is one start screen and
   * many ways to reach it, and the screen that must not show a document is
   * this one.
   */
  const openArtifact = useStore((s) => s.openArtifact)
  useEffect(() => {
    openArtifact(null)
  }, [openArtifact])

  const surfaces = kindOrder.filter((k) => enabledKinds.includes(k))
  /**
   * A surface an administrator has switched off, asked for by name. The chip
   * row simply would not contain it, so falling back silently would answer a
   * request for 이미지 with a chat box and no reason given.
   */
  const offHere = initialKind !== undefined && !enabledKinds.includes(initialKind)
  //: The kind can be switched off by an administrator while this screen is
  //: open, or arrive disabled on a reload. Falling back keeps the composer on
  //: something that exists rather than on a surface that will refuse the turn.
  const active = surfaces.includes(kind) ? kind : (surfaces[0] ?? 'chat')

  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  //: Only the ones that can take this surface, and only the ones switched on.
  //: An agent listed here and refused on click is worse than one not listed.
  const usableAgents = agents
    .filter((a) => a.enabled && (a.kinds.length === 0 || a.kinds.includes(active)))
    .slice(0, 6)

  if (offHere) {
    const meta = kindMeta[initialKind]
    const Icon = meta.icon
    // An administrator is not somebody to ask an administrator. They are the
    // person who can turn it on, and the switch is two clicks away — so the
    // screen says which switch instead of who to petition.
    const canSwitch = user?.role === 'admin'
    return (
      <>
        <TopBar left={<span className="text-base font-medium">{t('홈')}</span>} />
        <PageBody>
          <EmptyState
            icon={<Icon size={18} />}
            title={t('{kind} 기능이 꺼져 있습니다').replace('{kind}', t(meta.label))}
            headingLevel="h1"
            description={
              canSwitch
                ? t('이 워크스페이스에서 사용하지 않도록 설정되어 있습니다. 시스템 · 기능에서 켤 수 있습니다.')
                : t('관리자가 이 워크스페이스에서 사용하지 않도록 설정했습니다. 필요하면 관리자에게 요청하세요.')
            }
            action={
              <div className="flex gap-2">
                {canSwitch && (
                  <Button variant="primary" onClick={() => navigate('/admin/system/features')}>
                    {t('기능 설정 열기')}
                  </Button>
                )}
                <Button variant={canSwitch ? 'secondary' : 'primary'} onClick={() => navigate('/')}>
                  {t('홈으로')}
                </Button>
              </div>
            }
          />
        </PageBody>
      </>
    )
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('홈')}</span>} />
      <PageBody>
        <div className="mb-5 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('안녕하세요, {name}님').replace('{name}', user?.name ?? '')}
          </h1>
          <p className="mt-1 text-base text-muted">{t('무엇을 만들까요?')}</p>
        </div>

        {/* The surface row sits directly above the box it governs, so the two
            read as one control: pick the shape, write the sentence. */}
        <div className="mx-auto mb-2 flex w-full max-w-3xl flex-wrap justify-center gap-1.5 px-4">
          {surfaces.map((k) => {
            const meta = kindMeta[k]
            const Icon = meta.icon
            const on = k === active
            return (
              <button
                key={k}
                onClick={() => setKind(k)}
                aria-pressed={on}
                title={t(meta.tagline)}
                className={cn(
                  'flex items-center gap-1.5 rounded-control border px-2.5 py-1.5 text-base transition-colors',
                  on
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:bg-elevated hover:text-fg',
                )}
              >
                <Icon size={14} style={{ color: on ? undefined : meta.color }} />
                {t(meta.label)}
              </button>
            )
          })}
        </div>

        {/* `key` remounts on a surface change. The composer holds a draft, an
            attachment list and a one-turn skill choice, and every one of those
            is about the surface it was made for — carrying an image prompt's
            aspect chips into a report is not a saved draft, it is a bug. */}
        <Composer key={active} sessionId={null} kind={active} autoFocus />

        {/* The composer carries these only once a turn has been taken; before
            that the empty screen is what offers them. `/new/:kind` used to be
            that screen and now lands here, so this is the only place left
            that can — without them a starting point cannot be picked at all. */}
        <div className="mx-auto mb-8 mt-3 w-full max-w-3xl px-4">
          <section className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              {/* 무엇을 해 주는지를 말한다. 「빈 화면에서 시작하지 않아도
                  됩니다」 was a negation — it said what you need not do and
                  nothing about what pressing the button does. */}
              <h2 className="text-base font-semibold">{t('자주 하는 일로 시작하기')}</h2>
              <p className="mt-0.5 text-sm text-muted">
                {active === 'chat'
                  ? t('개념 배우기, 논문 읽기, 문제 원인 분석 등 일을 고르면 그 일에 필요한 것만 묻고 요청을 대신 씁니다.')
                  : active === 'image'
                    ? t('개념도, 처리 흐름도, 표지 그림 등 그림의 쓰임을 고르면 빈칸 몇 개로 요청이 완성됩니다.')
                    : t('과제 보고서, 논문 서론, 임원 보고 등 일을 고르면 필요한 것만 묻고 결과물 모양까지 정해 줍니다.')}
              </p>
            </div>
            <DesignGallery key={active} kind={active} />
          </section>
        </div>

        {usableAgents.length > 0 && (
          <section className="mx-auto mb-8 w-full max-w-3xl px-4">
            <div className="mb-2 flex items-baseline justify-between">
              <h2 className="text-base font-semibold">{t('에이전트에게 맡기기')}</h2>
              <button
                onClick={() => navigate('/agents')}
                className="min-h-8 px-1 text-sm text-muted hover:text-fg"
              >
                {t('전체 보기')}
              </button>
            </div>
            <p className="mb-2.5 text-sm text-muted">
              {t('지침·도구·자료를 갖춘 에이전트가 대신 진행합니다. 같은 프로젝트 안에서는 서로의 결론을 이어받습니다.')}
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {usableAgents.map((a) => (
                <Card
                  key={a.id}
                  // Runs it, rather than navigating to a list where it would
                  // have to be found again. This card *is* the run button.
                  onClick={() =>
                    void newSession(a.kinds[0] ?? active, { agentId: a.id })
                      .then((id) => navigate(`/s/${id}`))
                      .catch((err: unknown) => setNotice(startFailure(err, t)))
                  }
                  className="flex cursor-pointer items-center gap-2.5 px-3 py-2.5 transition-colors hover:bg-elevated"
                >
                  <span
                    className="grid size-7 shrink-0 place-items-center rounded-control text-white"
                    style={{ background: a.color }}
                  >
                    <Bot size={14} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-base font-medium">{a.name}</span>
                    <span className="block truncate text-xs text-muted">{a.description}</span>
                  </span>
                  <ArrowRight size={14} className="shrink-0 text-faint" />
                </Card>
              ))}
            </div>
          </section>
        )}

        {running.length > 0 && (
          <section className="mb-8">
            <h2 className="mb-2.5 text-base font-semibold">{t('진행 중')}</h2>
            <div className="space-y-2">
              {running.map((j) => {
                const session = sessions.find((s) => s.id === j.sessionId)
                return (
                  <Card
                    key={j.id}
                    onClick={() => session && navigate(`/s/${session.id}`)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-elevated"
                  >
                    <Loader2 size={15} className="shrink-0 animate-spin text-accent" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-base font-medium">
                        {session?.title ?? t('작업')}
                      </p>
                      <p className="text-xs text-faint">{j.stage}</p>
                    </div>
                    <div className="w-24">
                      <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
                        <div
                          className="h-full rounded-full bg-accent transition-[width]"
                          style={{ width: `${j.progress}%` }}
                        />
                      </div>
                    </div>
                    <span className="w-9 text-right text-xs tabular-nums text-faint">
                      {j.progress}%
                    </span>
                  </Card>
                )
              })}
            </div>
          </section>
        )}

      </PageBody>
    </>
  )
}
