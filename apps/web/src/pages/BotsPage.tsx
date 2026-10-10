import { Bot, ExternalLink, Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Card, EmptyState, LoadingState, PageHeader, ReloadNotice } from '@/components/ui'
import { botsApi, type BotListItem } from '@/lib/api'
import { STATUS_LABEL, VISIBILITY_LABEL } from '@/lib/publishing'
import { useT } from '@/lib/useT'

/**
 * 「챗봇」: the bots members of this instance published, to open and ask; and the caller's
 * own, to manage. One place for both, outside 사용자 지정 (where agents are made), so a
 * member who only wants to ask never has to look among the makers' tools.
 */
export function BotsPage() {
  const t = useT()
  const [rows, setRows] = useState<BotListItem[] | null>(null)
  const [failed, setFailed] = useState(false)

  const load = () => {
    botsApi
      .list()
      .then((found) => {
        setRows(found)
        setFailed(false)
      })
      .catch(() => setFailed(true))
  }
  useEffect(load, [])

  const mine = (rows ?? []).filter((b) => b.mine)
  const others = (rows ?? []).filter((b) => !b.mine && b.status === 'approved')

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('챗봇')}</span>} />
      <PageBody>
        <PageHeader title={t('챗봇')} description={t('이 기관 구성원이 공개한 챗봇입니다. 눌러서 바로 물어보세요.')} />
        {failed && <ReloadNotice onRetry={load} />}
        {rows === null && !failed ? (
          <LoadingState />
        ) : (
          <div className="space-y-8">
            {others.length === 0 && mine.length === 0 ? (
              <EmptyState
                icon={<Bot size={18} />}
                title={t('아직 공개된 챗봇이 없습니다')}
                description={t('에이전트 화면의 「챗봇으로 공개」로 내 에이전트를 구성원에게 열 수 있습니다.')}
                action={
                  <Link to="/customize?tab=agents" className="text-base text-accent hover:underline">
                    {t('에이전트 보기')}
                  </Link>
                }
              />
            ) : (
              <section aria-label={t('구성원이 공개한 챗봇')}>
                <ul className="grid gap-3 sm:grid-cols-2" data-testid="bot-list">
                  {others.map((bot) => (
                    <li key={bot.slug}>
                      <Link to={`/b/${bot.slug}`} className="block h-full rounded-card border border-line bg-panel p-4 transition-colors hover:border-accent/40 hover:bg-accent-soft/40">
                        <span className="flex items-center gap-2.5">
                          <span className="grid size-8 shrink-0 place-items-center rounded-control text-white" style={{ backgroundColor: bot.color }} aria-hidden>
                            <Bot size={15} />
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-base font-medium">{bot.title}</span>
                            <span className="block truncate text-sm text-faint">{t('만든 사람: {name}').replace('{name}', bot.ownerName)}</span>
                          </span>
                        </span>
                        {bot.description && <span className="mt-2 line-clamp-2 block text-sm text-muted">{bot.description}</span>}
                      </Link>
                    </li>
                  ))}
                  {others.length === 0 && <li className="text-base text-faint">{t('다른 구성원이 공개한 챗봇은 아직 없습니다.')}</li>}
                </ul>
              </section>
            )}
            {mine.length > 0 && (
              <section aria-label={t('내가 공개한 챗봇')} className="space-y-3">
                <h2 className="text-lg font-semibold">{t('내가 공개한 챗봇')}</h2>
                <Card className="divide-y divide-line" data-testid="my-bots">
                  {mine.map((bot) => (
                    <div key={bot.slug} className="flex flex-wrap items-center gap-3 px-4 py-3">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-base font-medium">{bot.title}</span>
                        <span className="block truncate text-sm text-faint">{t(VISIBILITY_LABEL[bot.visibility])}</span>
                      </span>
                      {bot.needsLook && <Badge tone="accent">{t('확인할 일')}</Badge>}
                      <Badge tone={bot.status === 'approved' ? 'success' : 'warn'}>{t(STATUS_LABEL[bot.status])}</Badge>
                      <Link to={`/b/${bot.slug}`} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
                        <ExternalLink size={13} aria-hidden />
                        {t('열기')}
                      </Link>
                      {bot.id && (
                        <Link to={`/customize/publications/${bot.id}`} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
                          <Settings2 size={13} aria-hidden />
                          {t('관리')}
                        </Link>
                      )}
                    </div>
                  ))}
                </Card>
              </section>
            )}
          </div>
        )}
      </PageBody>
    </>
  )
}
