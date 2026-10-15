import { ArrowLeft, Ellipsis, Loader2, MessageCircle, Plus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Dropdown, MenuItem } from '@/components/ui'
import { useT } from '@/lib/useT'
import { itemMeta, type HubItem } from './hubItems'
import { ItemIconTile } from './ItemCard'

/** Hub screen top bar: 「사용자 지정 / {종류}」 back to its tab. */
export function HubTopBar({
  tab,
  tabLabel,
  title,
  to,
}: {
  tab: string
  tabLabel: string
  title: string
  /** Middle crumb target when not a hub tab (내 템플릿). */
  to?: string
}) {
  const t = useT()
  return (
    <TopBar
      left={
        <span className="flex min-w-0 items-center gap-1.5 text-base">
          <Link to={`/customize?tab=${tab}`} className="shrink-0 text-muted hover:text-fg">
            {t('사용자 지정')}
          </Link>
          <span className="text-faint" aria-hidden>
            /
          </span>
          <Link to={to ?? `/customize?tab=${tab}`} className="shrink-0 text-muted hover:text-fg max-sm:hidden">
            {t(tabLabel)}
          </Link>
          <span className="text-faint max-sm:hidden" aria-hidden>
            /
          </span>
          <span className="min-w-0 truncate font-medium">{title}</span>
        </span>
      }
    />
  )
}

export function BackLink({ tab, label, to }: { tab: string; label: string; to?: string }) {
  const t = useT()
  return (
    <Link
      to={to ?? `/customize?tab=${tab}`}
      className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
    >
      <ArrowLeft size={14} aria-hidden />
      {t(label)}
    </Link>
  )
}

export interface MenuAction {
  label: string
  icon: ReactNode
  run: () => void
  danger?: boolean
}

/** Item screen: header, 바로 쓰기 and 추가/편집, rest under ⋯; main actions pinned bottom on phones. */
export function DetailLayout({
  item,
  tab,
  tabLabel,
  onTry,
  primary,
  menu,
  aside,
  children,
}: {
  item: HubItem
  tab: string
  tabLabel: string
  onTry: () => Promise<void>
  /** 「추가」 or 「편집」. */
  primary?: { label: string; run: () => void | Promise<void>; added?: boolean }
  menu: MenuAction[]
  /** Beside the buttons: on/off switch. */
  aside?: ReactNode
  children: ReactNode
}) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const name = t(item.name)
  const meta = itemMeta(item, item.mine ? 'mine' : 'explore', t)

  const tryIt = async () => {
    setBusy(true)
    try {
      await onTry()
    } finally {
      setBusy(false)
    }
  }

  const actions = (phone: boolean) => (
    <>
      <Button variant={phone ? 'secondary' : 'secondary'} className={phone ? 'flex-1' : ''} disabled={busy} onClick={() => void tryIt()}>
        {busy ? <Loader2 size={15} className="animate-spin" /> : <MessageCircle size={15} />}
        {t('바로 쓰기')}
      </Button>
      {primary && (
        <Button
          variant="primary"
          className={phone ? 'flex-1' : ''}
          disabled={primary.added}
          onClick={() => void primary.run()}
        >
          {primary.label === '추가' && <Plus size={15} />}
          {t(primary.label)}
        </Button>
      )}
    </>
  )

  return (
    <>
      <HubTopBar tab={tab} tabLabel={tabLabel} title={name} />
      <PageBody className="max-sm:pb-24">
        <BackLink tab={tab} label={tabLabel} />
        <header className="flex flex-wrap items-start gap-4 border-b border-line pb-6" data-testid="item-detail-header">
          <ItemIconTile item={item} size="lg" />
          <div className="min-w-0 flex-1 max-sm:basis-32">
            <h1 className="text-2xl font-semibold tracking-tight break-words">{name}</h1>
            {item.summary && <p className="mt-1 text-md text-muted">{t(item.summary)}</p>}
            {meta && <p className="mt-1.5 text-sm text-faint">{meta}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2 max-sm:w-full max-sm:justify-end">
            {aside}
            <span className="flex gap-2 max-sm:hidden">{actions(false)}</span>
            {menu.length > 0 && (
              <Dropdown
                align="right"
                trigger={() => (
                  <Button variant="ghost" size="icon" aria-label={t('더 보기')} title={t('공유 · 복제 · 삭제')}>
                    <Ellipsis size={16} />
                  </Button>
                )}
              >
                {menu.map((m) => (
                  <MenuItem key={m.label} icon={m.icon} danger={m.danger} onClick={m.run}>
                    {t(m.label)}
                  </MenuItem>
                ))}
              </Dropdown>
            )}
          </div>
        </header>
        <div className="space-y-8 pt-6">{children}</div>
      </PageBody>
      <div
        className="fixed inset-x-0 bottom-0 z-30 hidden gap-2 border-t border-line bg-bg/95 px-4 pt-3 max-sm:flex"
        style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}
        data-testid="detail-bottom-bar"
      >
        {actions(true)}
      </div>
    </>
  )
}

export function DetailSection({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-label={title} className="scroll-mt-4 space-y-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  )
}
