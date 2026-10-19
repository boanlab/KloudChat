import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, EmptyState } from '@/components/ui'
import { Composer, fillComposer } from '@/components/chat/Composer'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** Per-surface examples under the greeting; click fills the composer. */
const EXAMPLES: Record<SessionKind, string[]> = {
  chat: [
    '첨부한 문서를 세 줄로 요약해 줘',
    '다음 주 회의 안건 초안을 같이 잡아 줘',
    '이 문장을 더 자연스럽게 다듬어 줘',
    '엑셀 수식이 왜 오류가 나는지 찾아 줘',
  ],
  report: [
    '신규 서비스 도입을 검토하는 보고서를 써 줘',
    '지난 분기 사업 실적 보고서 — 요약, 성과, 남은 과제',
    '두 가지 안을 기준별로 비교하는 의사결정 보고서',
    '첨부한 자료로 연구 과제 중간 보고서 초안을 써 줘',
  ],
  // Only what the shown deck template (사내 브리핑) makes.
  slides: [
    '다음 주 회의 전에 돌릴 사내 브리핑 5장',
    '프로젝트 결과 보고 15분 발표',
    '새 제도 시행을 팀에 알리는 사내 브리핑',
    '이번 분기 진행 현황과 결정할 것을 정리한 발표',
  ],
  image: [
    '봄 행사 포스터에 쓸 밝은 파스텔 톤 일러스트',
    '홈페이지 메인 배너용 사무실 풍경 사진',
    '사용자 → 게이트웨이 → 모델로 이어지는 흐름도',
    '미니멀한 제품 아이콘 네 개',
  ],
  av: [
    '제품을 소개하는 30초 내레이션',
    '발표 시작에 틀 잔잔한 배경 음악 30초',
    '바다 위로 해가 뜨는 6초 영상',
  ],
}

/** Also the `/new/:kind` entry. */
export function HomePage({ initialKind }: { initialKind?: SessionKind }) {
  const t = useT()
  const navigate = useNavigate()
  const { user, sessions, jobs, enabledKinds } = useStore()
  const [kind, setKind] = useState<SessionKind>(initialKind ?? 'chat')

  // `/` and `/new/:kind` share the mount: resync the kind on route change.
  useEffect(() => {
    if (initialKind) setKind(initialKind)
  }, [initialKind])

  const openArtifact = useStore((s) => s.openArtifact)
  useEffect(() => {
    openArtifact(null)
  }, [openArtifact])

  const surfaces = kindOrder.filter((k) => enabledKinds.includes(k))
  // Disabled kind by URL: explained, no silent fallback.
  const offHere = initialKind !== undefined && !enabledKinds.includes(initialKind)
  const active = surfaces.includes(kind) ? kind : (surfaces[0] ?? 'chat')

  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  const ActiveIcon = kindMeta[active].icon

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
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('안녕하세요, {name}님').replace('{name}', user?.name ?? '')}
          </h1>
          <p className="mt-1 text-base text-muted">{t('무엇을 만들까요?')}</p>
        </div>

        {/* Click fills the box, sends nothing. */}
        <section aria-label={t('예시')} className="mx-auto mb-8 w-full max-w-3xl sm:px-4 short:mb-2">
          <div className="grid gap-2 sm:grid-cols-2">
            {EXAMPLES[active].map((example, index) => (
              <button
                key={example}
                type="button"
                onClick={() => fillComposer(t(example))}
                // Phone shows three; a fourth pushes the composer's neighbours off screen.
                className={cn(
                  'flex min-h-11 items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-left text-base text-muted transition-colors hover:bg-elevated hover:text-fg',
                  index >= 3 && 'phone:hidden',
                )}
              >
                <ActiveIcon size={14} className="shrink-0 text-faint" />
                <span className="min-w-0 flex-1">{t(example)}</span>
              </button>
            ))}
          </div>
        </section>

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
        <div role="group"
          aria-label={t('만들 것')}
          className="mx-auto mb-2 flex w-full max-w-3xl flex-wrap justify-center gap-1.5 px-4 max-sm:flex-nowrap max-sm:justify-start max-sm:overflow-x-auto max-sm:[scrollbar-width:none]">
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
        {/* `key` remount per surface: draft, attachments, skill pick are per surface. */}
        <Composer key={active} sessionId={null} kind={active} autoFocus />
      </div>
    </>
  )
}
