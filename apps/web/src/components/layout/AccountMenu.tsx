import {
  Bot,
  Brain,
  ChartColumn,
  ChevronDown,
  History,
  LogOut,
  Palette,
  Plug,
  Server,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Terminal as TerminalIcon,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Dropdown, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/**
 * The account menu, and the only way out of the account.
 *
 * It used to live in the sidebar footer and nowhere else. The sidebar returns
 * `null` when it is collapsed, and it starts collapsed under 1024px — so on a
 * phone, and on any desktop where somebody had hidden the panel, a signed-in
 * account had no reachable 로그아웃 at all. In a shared lab or library that is
 * the next person reading the previous person's conversations, so the menu is
 * now rendered in two places from one definition: the sidebar footer, where a
 * hand already goes, and the top bar, which is on every screen and never
 * disappears.
 */
function AccountItems() {
  const navigate = useNavigate()
  const t = useT()
  const user = useStore((s) => s.user)
  const users = useStore((s) => s.users)
  const logout = useStore((s) => s.logout)
  const pendingUsers = users.filter((u) => u.status === 'pending').length

  return (
    <>
      {/* 한 번 설정하고 나면 컴포저에서 쓰이는 것들. 사이드바의 세로는
          대화 목록이 써야 하므로 여기에 둡니다. */}
      <MenuLabel>{t('워크스페이스')}</MenuLabel>
      <MenuItem icon={<Bot size={14} />} onClick={() => navigate('/agents')}>
        {t('에이전트')}
      </MenuItem>
      <MenuItem icon={<Sparkles size={14} />} onClick={() => navigate('/skills')}>
        {t('스킬')}
      </MenuItem>
      <MenuItem icon={<Plug size={14} />} onClick={() => navigate('/connectors')}>
        {t('커넥터')}
      </MenuItem>
      <MenuItem icon={<Brain size={14} />} onClick={() => navigate('/memory')}>
        {t('메모리')}
      </MenuItem>
      <MenuItem icon={<Palette size={14} />} onClick={() => navigate('/designs')}>
        {t('디자인')}
      </MenuItem>
      {/* 목록은 사이드바가 전부 보여줍니다. 이 화면이 남은 이유는 좁은 세로
          칼럼에서 할 수 없는 것 — 여러 건을 골라 한 번에 지우는 일입니다. */}
      <MenuItem icon={<History size={14} />} onClick={() => navigate('/history')}>
        {t('대화 관리')}
      </MenuItem>
      <MenuSeparator />
      <MenuLabel>{t('계정')}</MenuLabel>
      <MenuItem icon={<Settings size={14} />} onClick={() => navigate('/settings')}>
        {t('설정')}
      </MenuItem>
      <MenuItem icon={<ChartColumn size={14} />} onClick={() => navigate('/usage')}>
        {t('사용량')}
      </MenuItem>
      <MenuItem icon={<TerminalIcon size={14} />} onClick={() => navigate('/agent-setup')}>
        {t('AI 에이전트 연동')}
      </MenuItem>
      {user?.role === 'admin' && (
        <>
          <MenuSeparator />
          <MenuLabel>{t('관리')}</MenuLabel>
          <MenuItem
            icon={<Shield size={14} />}
            onClick={() => navigate('/admin/users')}
            hint={pendingUsers > 0 ? `${t('승인')} ${pendingUsers}` : undefined}
          >
            {t('사용자 · 크레딧')}
          </MenuItem>
          <MenuItem icon={<ChartColumn size={14} />} onClick={() => navigate('/admin/usage')}>
            {t('사용량')}
          </MenuItem>
          <MenuItem icon={<ShieldCheck size={14} />} onClick={() => navigate('/admin/governance')}>
            {t('보안 · 감사')}
          </MenuItem>
          {/* Instance configuration — the proxy and the mail relay. It used to
              be a tab inside 설정, behind an admin-only flag, next to the theme
              switch. */}
          <MenuItem icon={<Server size={14} />} onClick={() => navigate('/admin/system')}>
            {t('시스템')}
          </MenuItem>
        </>
      )}
      <MenuSeparator />
      <MenuItem danger icon={<LogOut size={14} />} onClick={() => void logout()}>
        {t('로그아웃')}
      </MenuItem>
    </>
  )
}

/** The sidebar footer row: avatar, name, address, and the approval queue. */
export function AccountMenu() {
  const t = useT()
  const user = useStore((s) => s.user)
  const users = useStore((s) => s.users)
  const pendingUsers = users.filter((u) => u.status === 'pending').length

  return (
    <Dropdown
      align="left"
      className="min-w-56"
      trigger={() => (
        <button
          aria-label={t('계정 메뉴')}
          className="flex w-full items-center gap-2.5 rounded-control px-2 py-1.5 text-left transition-colors hover:bg-elevated"
        >
          <span
            className="grid size-7 shrink-0 place-items-center rounded-full text-sm font-semibold text-white"
            style={{ background: user?.avatarColor }}
          >
            {user?.name?.[0] ?? 'U'}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-base font-medium">{user?.name}</span>
            <span className="block truncate text-xs text-faint">{user?.email}</span>
          </span>
          {/* The queue, on the button that opens the menu holding it. Without
              this the only signal was inside the menu nobody had a reason to
              open. */}
          {user?.role === 'admin' && pendingUsers > 0 && (
            <span
              aria-label={t('승인 대기 {n}건').replace('{n}', String(pendingUsers))}
              className="shrink-0 rounded-control bg-warn/15 px-1.5 text-xs font-medium text-warn"
            >
              {pendingUsers}
            </span>
          )}
          <ChevronDown size={14} className="shrink-0 text-faint" />
        </button>
      )}
    >
      <AccountItems />
    </Dropdown>
  )
}

/**
 * The top bar's compact form: the avatar alone. Same menu, always on screen —
 * this is the copy that guarantees 로그아웃 exists no matter what the sidebar
 * is doing.
 */
export function AccountMenuCompact() {
  const t = useT()
  const user = useStore((s) => s.user)
  const users = useStore((s) => s.users)
  const pendingUsers = users.filter((u) => u.status === 'pending').length

  return (
    <Dropdown
      align="left"
      className="min-w-56"
      trigger={() => (
        <button
          aria-label={t('계정 메뉴')}
          title={user?.email ?? t('계정 메뉴')}
          className="relative grid size-7 shrink-0 place-items-center rounded-full text-sm font-semibold text-white transition-opacity hover:opacity-85"
          style={{ background: user?.avatarColor }}
        >
          {user?.name?.[0] ?? 'U'}
          {user?.role === 'admin' && pendingUsers > 0 && (
            <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-warn ring-2 ring-bg" />
          )}
        </button>
      )}
    >
      <div className="border-b border-line px-2.5 pt-1.5 pb-2">
        <p className="truncate text-base font-medium">{user?.name}</p>
        <p className="truncate text-xs text-faint">{user?.email}</p>
      </div>
      <AccountItems />
    </Dropdown>
  )
}
