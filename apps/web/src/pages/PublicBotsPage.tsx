import { Bot, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Brand } from '@/components/layout/Brand'
import { botsApi, type PublicBotItem } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/**
 * /b — the bots an administrator opened to everyone, for anyone: no account, no app shell,
 * no owner names. A member also finds these on 「챗봇」.
 */
export function PublicBotsPage() {
  const t = useT()
  const authenticated = useStore((s) => s.authenticated)
  const brand = useStore((s) => s.brand)
  const [rows, setRows] = useState<PublicBotItem[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    botsApi.publicList().then(
      (found) => live && setRows(found),
      () => live && setFailed(true),
    )
    return () => {
      live = false
    }
  }, [])

  return (
    <div className="min-h-dvh bg-bg text-fg">
      <header
        className="flex items-center gap-3 border-b border-line px-4 py-3"
        style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
      >
        <Brand name={brand.name} logo={brand.logo} />
        <span className="min-w-0 flex-1" />
        <Link to={authenticated ? '/bots' : '/'} className="text-sm text-muted hover:text-fg">
          {authenticated ? t('챗봇 목록으로') : t('로그인')}
        </Link>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-8">
        <h1 className="text-2xl font-semibold tracking-tight">{t('공개 챗봇')}</h1>
        <p className="mt-1 text-base text-muted">{t('누구나 로그인 없이 물어볼 수 있는 챗봇입니다.')}</p>
        <div className="mt-6">
          {failed ? (
            <p role="alert" className="text-base text-danger">
              {t('목록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.')}
            </p>
          ) : rows === null ? (
            <p role="status" className="flex items-center gap-2 text-base text-faint">
              <Loader2 size={14} className="animate-spin" />
              {t('불러오는 중…')}
            </p>
          ) : rows.length === 0 ? (
            <p className="text-base text-faint">{t('지금은 공개된 챗봇이 없습니다.')}</p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2" data-testid="public-bot-list">
              {rows.map((bot) => (
                <li key={bot.slug}>
                  <Link
                    to={`/b/${bot.slug}`}
                    className="block h-full rounded-card border border-line bg-panel p-4 transition-colors hover:border-accent/40 hover:bg-accent-soft/40"
                  >
                    <span className="flex items-center gap-2.5">
                      <span className="grid size-8 shrink-0 place-items-center rounded-control text-white" style={{ backgroundColor: bot.color }} aria-hidden>
                        <Bot size={15} />
                      </span>
                      <span className="min-w-0 truncate text-base font-medium">{bot.title}</span>
                    </span>
                    {bot.description && <span className="mt-2 line-clamp-2 block text-sm text-muted">{bot.description}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
    </div>
  )
}
