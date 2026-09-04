import { Code2,
  Bot,
  Brain,
  ChartColumn,
  ChevronDown,
  History,
  LogOut,
  Palette,
  Plug,
  Server,
  Keyboard,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Terminal as TerminalIcon,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Dropdown, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { useT } from '@/lib/useT'
import { isMac } from '@/lib/shortcuts'
import { useStore } from '@/store/useStore'
import { openShortcuts } from './KeyboardShortcuts'

/** Shared account menu for sidebar and top bar. */
function AccountItems() {
  const navigate = useNavigate()
  const t = useT()
  const user = useStore((s) => s.user)
  const users = useStore((s) => s.users)
  const logout = useStore((s) => s.logout)
  const pendingUsers = users.filter((u) => u.status === 'pending').length

  return (
    <>
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
      <MenuItem icon={<History size={14} />} onClick={() => navigate('/history')}>
        {t('대화 기록')}
      </MenuItem>
      <MenuSeparator />
      <MenuLabel>{t('계정')}</MenuLabel>
      <MenuItem icon={<TerminalIcon size={14} />} onClick={() => navigate('/agent-setup')}>
        {t('AI 에이전트 연동')}
      </MenuItem>
      <MenuItem icon={<Code2 size={14} />} onClick={() => navigate('/api-setup')}>
        {t('API 연동')}
      </MenuItem>
      <MenuItem icon={<ChartColumn size={14} />} onClick={() => navigate('/usage')}>
        {t('사용량')}
      </MenuItem>
      <MenuItem icon={<Settings size={14} />} onClick={() => navigate('/settings')}>
        {t('설정')}
      </MenuItem>
      <MenuItem icon={<Keyboard size={14} />} onClick={openShortcuts} hint={`${isMac() ? '⌘' : 'Ctrl'} /`}>
        {t('키보드 단축키')}
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

/** Sidebar footer: avatar, name, email, pending-approval count. */
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
          aria-label={`${t('계정 메뉴')} · ${user?.email ?? ''}`}
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

/** Top-bar form: avatar only, same menu. */
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
          aria-label={`${t('계정 메뉴')} · ${user?.email ?? ''}`}
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
