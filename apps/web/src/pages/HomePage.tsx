import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, EmptyState } from '@/components/ui'
import { Composer } from '@/components/chat/Composer'
import { StartExamples, StartHeading } from '@/components/chat/EmptyStart'
import { useBlankDocument } from '@/lib/useBlankDocument'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { ReportOutput, SessionKind, SlidesOutput } from '@/types'
import { useT } from '@/lib/useT'

export function HomePage({ initialKind }: { initialKind?: SessionKind }) {
  const t = useT()
  const navigate = useNavigate()
  const { user, sessions, jobs, enabledKinds } = useStore()
  const [kind, setKind] = useState<SessionKind>(initialKind ?? 'chat')

  // `/` and `/new/:kind` share the mount: resync the kind on route change.
  useEffect(() => {
    if (initialKind) setKind(initialKind)
  }, [initialKind])

  const blank = useBlankDocument()
  /** The file picked per surface (null: the request decides). */
  const picked = useStore((s) => s.outputPick)
  const setOutputPick = useStore((s) => s.setOutputPick)
  // A deep link picks the file: /new/report?format=hwp, /new/slides?format=pptx.
  const [params] = useSearchParams()
  const linkedFormat = params.get('format')
  useEffect(() => {
    if (!linkedFormat) return
    if (initialKind === 'report' && ['web', 'hwp', 'docx'].includes(linkedFormat)) {
      setOutputPick('report', linkedFormat as ReportOutput)
    } else if (initialKind === 'slides' && ['deck', 'pptx'].includes(linkedFormat)) {
      setOutputPick('slides', linkedFormat as SlidesOutput)
    }
  }, [initialKind, linkedFormat, setOutputPick])

  const openArtifact = useStore((s) => s.openArtifact)
  useEffect(() => {
    openArtifact(null)
  }, [openArtifact])

  const surfaces = kindOrder.filter((k) => enabledKinds.includes(k))
  // Disabled kind by URL: explained, no silent fallback.
  const offHere = initialKind !== undefined && !enabledKinds.includes(initialKind)
  const active = surfaces.includes(kind) ? kind : (surfaces[0] ?? 'chat')

  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')

  if (offHere) {
    const meta = kindMeta[initialKind]
    const Icon = meta.icon
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
      {/* As a session: content scrolls, composer docked with the surface chips. */}
      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="home-scroll">
      <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col px-4 py-6 sm:px-6 sm:py-8 short:py-2">
        {/* Top-aligned; short content leaves space below, over the dock. A short screen keeps the examples instead. */}
        <div className="mt-[min(8vh,4rem)] mb-5 text-center phone:mt-[min(14vh,7rem)] short:sr-only">
          <StartHeading title={t('안녕하세요, {name}님').replace('{name}', user?.name ?? '')} subtitle={t('무엇을 만들까요?')} />
        </div>

        {/* Click fills the box, sends nothing. */}
        <StartExamples kind={active} className="mb-8 sm:px-4 short:mb-2" />

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
      </div>
      </div>

      <div className="shrink-0 pt-2" data-testid="home-dock">
        <div className="mx-auto mb-2 flex w-full max-w-3xl flex-wrap justify-center gap-1.5 px-4 max-sm:flex-nowrap max-sm:justify-start max-sm:overflow-x-auto max-sm:[scrollbar-width:none] [@media(max-height:30rem)]:flex-nowrap [@media(max-height:30rem)]:justify-start [@media(max-height:30rem)]:overflow-x-auto [@media(max-height:30rem)]:[scrollbar-width:none]">
          {/* The surfaces; an HWP, Word or PowerPoint document beside them is a chat with a
              file, not a surface. One scrolling row on a short screen too, so the examples keep their
              place. */}
          <div role="group" aria-label={t('만들 것')} className="contents">
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
                    'flex shrink-0 items-center gap-1.5 rounded-control border px-2.5 py-1.5 text-base transition-colors',
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
        </div>
        {/* `key` remount per surface: draft, attachments, skill pick are per surface. */}
        <Composer
          key={active}
          sessionId={null}
          kind={active}
          autoFocus
          output={active === 'report' || active === 'slides' ? picked[active] : null}
          onPickOutput={active === 'report' || active === 'slides'
            ? (value) => setOutputPick(active, value)
            : undefined}
          onBlankDocument={surfaces.includes('chat') ? (kind) => void blank.start(kind) : undefined}
          openingDocument={blank.opening}
        />
      </div>
    </>
  )
}

