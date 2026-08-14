import { Bot, Boxes, Brain, ChartColumn, Terminal as TerminalIcon, ChevronDown, ChevronRight, History, Layers, LogOut, MoreHorizontal, Pencil, Pin, PinOff, Plug, Plus, Search, Server, Settings, Shield, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { Dropdown, Input, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { cn, groupByRecency } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Session } from '@/types'
import { useT } from '@/lib/useT'
import { Brand } from './Brand'

/**
 * A collapsible group of navigation rows: too many fixed rows leave no height
 * for the conversation list. The collapsed state is remembered.
 */
function NavSection({
  id,
  label,
  children,
  defaultOpen = true,
}: {
  id: string
  label: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const storageKey = `kchat-nav-${id}`
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(storageKey)
    return saved === null ? defaultOpen : saved === '1'
  })
  const toggle = () => {
    setOpen((v) => {
      localStorage.setItem(storageKey, v ? '0' : '1')
      return !v
    })
  }
  return (
    <nav className="shrink-0 border-t border-line px-3 py-2">
      <button
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2.5 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase transition-colors hover:text-muted"
      >
        <ChevronRight
          size={11}
          className={cn('transition-transform', open && 'rotate-90')}
        />
        {label}
      </button>
      {open && <div className="space-y-0.5">{children}</div>}
    </nav>
  )
}

/** Unpinned conversations rendered per page. */
const PAGE = 40

const workspaceNav = [
  { to: '/projects', label: '프로젝트', icon: Boxes },
  { to: '/artifacts', label: '아티팩트', icon: Layers },
  { to: '/agents', label: '에이전트', icon: Bot },
  { to: '/skills', label: '스킬', icon: Sparkles },
  { to: '/memory', label: '메모리', icon: Brain },
  { to: '/connectors', label: '커넥터', icon: Plug },
  { to: '/history', label: '대화 기록', icon: History },
]


function SessionRow({
  session,
  active,
  emoji,
  onOpen,
  onRename,
  onTogglePin,
  onDelete,
}: {
  session: Session
  active: boolean
  emoji?: string
  onOpen: () => void
  onRename: (title: string) => void
  onTogglePin: () => void
  onDelete: () => void
}) {
  const t = useT()
  const meta = kindMeta[session.kind]
  const Icon = meta.icon
    /** Rename in place; a generated title is not always the right one. */
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(session.title)

  const commit = () => {
    const next = draft.trim()
    if (next && next !== session.title) onRename(next)
    else setDraft(session.title)
    setEditing(false)
  }

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        aria-label={t('대화 이름')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') {
            setDraft(session.title)
            setEditing(false)
          }
        }}
        className="w-full rounded-lg border border-accent bg-panel px-2.5 py-1.5 text-[13px] outline-none"
      />
    )
  }

  return (
    <div
      className={cn(
        'group relative flex items-center rounded-lg text-[13px] transition-colors',
        active ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
      )}
    >
      <button
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left"
        title={`${t(meta.label)} · ${session.title}`}
      >
        <Icon size={13} className="shrink-0" style={{ color: meta.color }} />
        <span className="min-w-0 flex-1 truncate">
          {emoji && <span className="mr-1 opacity-70">{emoji}</span>}
          {session.title}
        </span>
      </button>
      <Dropdown
        align="right"
        trigger={() => (
          <button
            className="mr-1 grid size-6 shrink-0 place-items-center rounded-md text-faint opacity-0 transition-opacity group-hover:opacity-100 hover:bg-line hover:text-fg"
            aria-label={t('메뉴')}
          >
            <MoreHorizontal size={14} />
          </button>
        )}
      >
        <MenuItem
          icon={<Pencil size={14} />}
          onClick={() => {
            setDraft(session.title)
            setEditing(true)
          }}
        >
          {t('이름 바꾸기')}
        </MenuItem>
        <MenuItem
          icon={session.pinned ? <PinOff size={14} /> : <Pin size={14} />}
          onClick={onTogglePin}
        >
          {session.pinned ? t('고정 해제') : t('고정')}
        </MenuItem>
        <MenuSeparator />
        <MenuItem danger icon={<Trash2 size={14} />} onClick={onDelete}>
          {t('삭제')}
        </MenuItem>
      </Dropdown>
    </div>
  )
}

export function Sidebar() {
  const navigate = useNavigate()
  const t = useT()
  const brand = useStore((s) => s.brand)
  const enabledKinds = useStore((s) => s.enabledKinds)
  const [query, setQuery] = useState('')
  const {
    sessions,
    projects,
    users,
    activeSessionId,
    deleteSession,
    renameSession,
    togglePinSession,
    user,
    logout,
    sidebarOpen,
  } = useStore()
  const pendingUsers = users.filter((u) => u.status === 'pending').length

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q ? sessions.filter((s) => s.title.toLowerCase().includes(q)) : sessions
    return [...list].sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
  }, [sessions, query])

  const pinned = filtered.filter((s) => s.pinned)
  const unpinned = filtered.filter((s) => !s.pinned)
    /**
     * Cap on rendered unpinned conversations. The sidebar is on every screen,
     * so the full list would be hundreds of buttons reconciled on every store
     * change. Search still looks at all of them.
     */
  const [shown, setShown] = useState(PAGE)
  const visible = unpinned.slice(0, shown)
  const groups = groupByRecency(visible)
  const hidden = unpinned.length - visible.length

  if (!sidebarOpen) return null

  const renderRow = (session: Session) => (
    <SessionRow
      key={session.id}
      session={session}
      active={activeSessionId === session.id}
      emoji={projects.find((p) => p.id === session.projectId)?.emoji}
      onOpen={() => navigate(`/s/${session.id}`)}
      onRename={(title) => void renameSession(session.id, title)}
      onTogglePin={() => togglePinSession(session.id)}
      onDelete={() => deleteSession(session.id)}
    />
  )

  const used = user?.creditsUsed ?? 0
  const total = user?.monthlyCredits ?? 0
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0

  return (
    <aside className="flex w-[268px] shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-3 py-3">
        <Brand name={brand.name} logo={brand.logo} />
      </div>

      {/* 만들기 — 다섯 개 축 */}
      <nav className="space-y-0.5 px-3 pb-2">
        {kindOrder.filter((k) => enabledKinds.includes(k)).map((kind) => {
          const meta = kindMeta[kind]
          const Icon = meta.icon
          return (
            <NavLink
              key={kind}
              to={`/new/${kind}`}
              className={({ isActive }) =>
                cn(
                  'group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] transition-colors',
                  isActive
                    ? 'bg-elevated font-medium text-fg'
                    : 'text-muted hover:bg-elevated hover:text-fg',
                )
              }
            >
              <Icon size={15} style={{ color: meta.color }} />
              {t(meta.label)}
              <Plus
                size={13}
                className="ml-auto text-faint opacity-0 transition-opacity group-hover:opacity-100"
              />
            </NavLink>
          )
        })}
      </nav>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('검색')}
            className="h-8 bg-transparent pl-8 text-[13px]"
          />
        </div>
      </div>

      {/* 워크스페이스 */}
      <NavSection id="workspace" label={t('워크스페이스')}>
        {workspaceNav.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] transition-colors',
                isActive
                  ? 'bg-elevated font-medium text-fg'
                  : 'text-muted hover:bg-elevated hover:text-fg',
              )
            }
          >
            <Icon size={15} />
            {t(label)}
          </NavLink>
        ))}
      </NavSection>

      {/* 관리자 항목은 계정 메뉴 안에 있다. 대기 건수는 계정 버튼에 붙어 있어,
          내비게이션을 한 벌 더 두지 않고도 승인 큐가 스스로 드러난다. */}

      {/* 최근 작업 — 사이드바의 본체입니다. */}
      {/* `min-h-0`, not a floor: with a minimum height the list refuses to shrink,
          and on a short window it pushes the credits and the account below the
          fold — the two things that must not go missing. */}
      <div className="mt-1 min-h-0 flex-1 overflow-y-auto border-t border-line px-3 py-2">
        {pinned.length > 0 && (
          <section className="mb-3">
            <p className="px-2.5 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">
              {t('고정됨')}
            </p>
            <div className="space-y-0.5">{pinned.map(renderRow)}</div>
          </section>
        )}
        {groups.map((g) => (
          <section key={g.label} className="mb-3">
            <p className="px-2.5 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">
              {t(g.label)}
            </p>
            <div className="space-y-0.5">{g.items.map(renderRow)}</div>
          </section>
        ))}
        {hidden > 0 && (
          <button
            onClick={() => setShown((n) => n + PAGE)}
            className="mt-1 w-full rounded-lg px-2.5 py-1.5 text-[12px] text-muted transition-colors hover:bg-elevated hover:text-fg"
          >
            {t('이전 대화')} {hidden.toLocaleString()}{t('개 더 보기')}
          </button>
        )}
        {filtered.length === 0 && (
          <p className="px-2.5 py-6 text-center text-[13px] text-faint">{t('검색 결과가 없습니다')}</p>
        )}
      </div>

      {/* 크레딧 + 계정 — always on screen, at any height. */}
      <div className="shrink-0 border-t border-line p-2">
        <button
          aria-label={t('이번 달 사용량')}
          onClick={() => navigate('/usage')}
          className="mb-1 block w-full rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-elevated"
        >
          <span className="flex items-center justify-between text-[11px]">
            <span className="text-faint">{t('이번 달 크레딧')}</span>
            <span className="tabular-nums text-muted">
              {(total - used).toLocaleString()} {t('남음')}
            </span>
          </span>
          <span className="mt-1 block h-1 overflow-hidden rounded-full bg-elevated">
            <span
              className={cn(
                'block h-full rounded-full transition-[width]',
                pct > 90 ? 'bg-danger' : 'bg-accent',
              )}
              style={{ width: `${pct}%` }}
            />
          </span>
        </button>

        <Dropdown
          align="left"
          className="min-w-56"
          trigger={() => (
            <button className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-elevated">
              <span
                className="grid size-7 shrink-0 place-items-center rounded-full text-[12px] font-semibold text-white"
                style={{ background: user?.avatarColor }}
              >
                {user?.name?.[0] ?? 'U'}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{user?.name}</span>
                <span className="block truncate text-[11px] text-faint">{user?.email}</span>
              </span>
              {/* The queue, on the button that opens the menu holding it. Without
                  this the only signal was inside the menu nobody had a reason to
                  open. */}
              {user?.role === 'admin' && pendingUsers > 0 && (
                <span
                  aria-label={t('승인 대기 {n}건').replace('{n}', String(pendingUsers))}
                  className="shrink-0 rounded-md bg-warn/15 px-1.5 text-[11px] font-medium text-warn"
                >
                  {pendingUsers}
                </span>
              )}
              <ChevronDown size={14} className="shrink-0 text-faint" />
            </button>
          )}
        >
          <MenuLabel>{t('계정')}</MenuLabel>
          <MenuItem icon={<Settings size={14} />} onClick={() => navigate('/settings')}>
            {t('설정')}
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
              <MenuItem
                icon={<ShieldCheck size={14} />}
                onClick={() => navigate('/admin/governance')}
              >
                {t('보안 · 감사')}
              </MenuItem>
              {/* Instance configuration — the proxy and the mail relay. It used
                  to be a tab inside 설정, behind an admin-only flag, next to the
                  theme switch. */}
              <MenuItem icon={<Server size={14} />} onClick={() => navigate('/admin/system')}>
                {t('시스템')}
              </MenuItem>
            </>
          )}
          <MenuSeparator />
          <MenuItem danger icon={<LogOut size={14} />} onClick={logout}>
            {t('로그아웃')}
          </MenuItem>
        </Dropdown>
      </div>
    </aside>
  )
}
