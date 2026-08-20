import { ArrowRight, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Card } from '@/components/ui'
import { DesignRail } from '@/components/chat/DesignRail'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { madeLine, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

export function HomePage() {
  const t = useT()
  const navigate = useNavigate()
  const { user, sessions, jobs, projects, enabledKinds } = useStore()

  const recent = [...sessions]
    .sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
    .slice(0, 6)
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('홈')}</span>} />
      <PageBody>
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('안녕하세요, {name}님').replace('{name}', user?.name ?? '')}
          </h1>
          <p className="mt-1 text-base text-muted">{t('무엇을 만들까요?')}</p>
        </div>

        <div className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {kindOrder.filter((k) => enabledKinds.includes(k)).map((kind) => {
            const meta = kindMeta[kind]
            const Icon = meta.icon
            return (
              <button
                key={kind}
                onClick={() => navigate(`/new/${kind}`)}
                className="group rounded-card border border-line bg-panel p-4 text-left transition-colors hover:border-line-strong hover:bg-elevated"
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className="grid size-8 shrink-0 place-items-center rounded-control text-white"
                    style={{ background: meta.color }}
                  >
                    <Icon size={16} />
                  </span>
                  <span className="flex-1 text-base font-medium">{t(meta.label)}</span>
                  <ArrowRight
                    size={15}
                    className="text-faint transition-transform group-hover:translate-x-0.5"
                  />
                </div>
                <p className="mt-2.5 text-base leading-relaxed text-muted">{t(meta.tagline)}</p>
              </button>
            )
          })}
        </div>

        {/* Under the five kinds and above the work already done: what to make
            and what it can look like are one decision, taken together. */}
        <DesignRail />

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

        <section>
          <h2 className="mb-2.5 text-base font-semibold">{t('최근 작업')}</h2>
          <div className="space-y-2">
            {recent.map((s) => {
              const meta = kindMeta[s.kind]
              const Icon = meta.icon
              const project = projects.find((p) => p.id === s.projectId)
              return (
                <Card
                  key={s.id}
                  onClick={() => navigate(`/s/${s.id}`)}
                  className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-elevated"
                >
                  <Icon size={15} className="shrink-0" style={{ color: meta.color }} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-base font-medium">{s.title}</p>
                    {/* 그림과 영상 화면은 turn 을 남기지 않아 preview 가 늘 비어
                        있었다. 제목이 이미 사람이 쓴 문장이므로, 그 아래에는
                        되돌아온 것 — 몇 장인지, 몇 초인지 — 을 적는다. */}
                    <p className="truncate text-xs text-muted">
                      {s.messages.at(-1)?.content.slice(0, 80) ??
                        s.preview ??
                        madeLine(s.made, t) ??
                        t('아직 주고받은 메시지가 없습니다')}
                    </p>
                  </div>
                  {project && <Badge>{project.emoji}</Badge>}
                  <span className="shrink-0 text-xs text-faint">
                    {relativeTime(s.updatedAt)}
                  </span>
                </Card>
              )
            })}
          </div>
        </section>
      </PageBody>
    </>
  )
}
