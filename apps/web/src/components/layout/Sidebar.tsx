import { Bot, Boxes, Brain, ChevronRight, FolderMinus, History, Layers, MoreHorizontal, Palette, Pencil, Pin, PinOff, Plug, Plus, Search, Sparkles, Trash2 } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { Dropdown, Input, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { cn, groupByRecency } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Project, Session } from '@/types'
import { useT } from '@/lib/useT'
import { AccountMenu } from './AccountMenu'
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
        className="flex w-full items-center gap-1 px-2.5 pb-1 text-xs font-semibold tracking-wide text-faint uppercase transition-colors hover:text-muted"
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

interface NavRow {
  to: string
  label: string
  icon: typeof Bot
}

/**
 * The workspace, grouped by what a person is doing rather than listed.
 *
 * Eight rows in one flat column — 프로젝트, 아티팩트, 디자인, 에이전트, 스킬,
 * 메모리, 커넥터, 대화 기록 — said nothing about how any of them relate. An
 * agent and the skills and connectors it can reach are one subject; a finished
 * report and the design it wears are another; and reading the list gave no way
 * to tell which was which, so the connection between an agent and what it can
 * do had to be learned rather than seen.
 *
 * Three groups, in the order work moves through them. **실행** is what does the
 * work and what it is allowed to use. **자산** is what came out and what it is
 * kept in. **기록** is where to go back and find it.
 */
const workspaceNav: { id: string; label: string; items: NavRow[] }[] = [
  {
    id: 'run',
    label: '실행',
    items: [
      { to: '/agents', label: '에이전트', icon: Bot },
      { to: '/skills', label: '스킬', icon: Sparkles },
      { to: '/connectors', label: '커넥터', icon: Plug },
      { to: '/memory', label: '메모리', icon: Brain },
    ],
  },
  {
    id: 'assets',
    label: '자산',
    items: [
      { to: '/projects', label: '프로젝트', icon: Boxes },
      { to: '/artifacts', label: '아티팩트', icon: Layers },
      { to: '/designs', label: '디자인', icon: Palette },
    ],
  },
  {
    id: 'records',
    label: '기록',
    items: [{ to: '/history', label: '대화 기록', icon: History }],
  },
]


function SessionRow({
  session,
  active,
  emoji,
  projects,
  onOpen,
  onRename,
  onTogglePin,
  onMove,
  onDelete,
}: {
  session: Session
  active: boolean
  emoji?: string
  projects: Project[]
  onOpen: () => void
  onRename: (title: string) => void
  onTogglePin: () => void
  onMove: (projectId: string | null) => void
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
        className="w-full rounded-control border border-accent bg-panel px-2.5 py-1.5 text-base outline-none"
      />
    )
  }

  return (
    <div
      className={cn(
        'group relative flex items-center rounded-control text-base transition-colors',
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
        trigger={({ open }) => (
          /* Fading in on hover keeps the list quiet, but hover is a thing only a
             mouse has: on a tablet this was the only way to rename, pin or
             delete a conversation, and it never appeared. So the row shows it
             outright where the pointer cannot hover, on keyboard focus, and for
             as long as its own menu is open. */
          <button
            className={cn(
              'mr-1 grid size-6 shrink-0 place-items-center rounded-control text-faint opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 hover:bg-line hover:text-fg [@media(hover:none)]:opacity-100',
              open && 'opacity-100',
            )}
            aria-label={t('메뉴')}
            title={t('이 대화의 이름 바꾸기·고정·삭제')}
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
        {/* Filing an existing conversation. A project could only be filled by
            starting work inside it, so anything begun the ordinary way was
            stranded outside — which is most of what anybody has. */}
        {projects.length > 0 && (
          <>
            <MenuSeparator />
            <MenuLabel>{t('프로젝트')}</MenuLabel>
            {session.projectId && (
              <MenuItem icon={<FolderMinus size={14} />} onClick={() => onMove(null)}>
                {t('프로젝트에서 빼기')}
              </MenuItem>
            )}
            {projects
              .filter((p) => p.id !== session.projectId)
              .map((p) => (
                <MenuItem key={p.id} icon={<span>{p.emoji}</span>} onClick={() => onMove(p.id)}>
                  {p.name}
                </MenuItem>
              ))}
          </>
        )}
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
  const [query, setQuery] = useState('')
  const {
    sessions,
    projects,
    activeSessionId,
    deleteSession,
    renameSession,
    togglePinSession,
    moveSessionToProject,
    user,
    sidebarOpen,
  } = useStore()

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
      projects={projects}
      onOpen={() => navigate(`/s/${session.id}`)}
      onRename={(title) => void renameSession(session.id, title)}
      onTogglePin={() => togglePinSession(session.id)}
      onMove={(projectId) => void moveSessionToProject(session.id, projectId)}
      onDelete={() => deleteSession(session.id)}
    />
  )

  const used = user?.creditsUsed ?? 0
  const total = user?.monthlyCredits ?? 0
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0

  return (
    <aside className="flex w-[268px] shrink-0 flex-col border-r border-line bg-sidebar">
      {/* The name is where everyone reaches for home. It looked like a header
          and behaved like one, so the only way back was the 홈 item further
          down — which is not where a hand goes. */}
      <Link
        to="/"
        aria-label={t('홈')}
        className="flex items-center gap-2 rounded-control px-3 py-3 transition-colors hover:bg-elevated"
      >
        <Brand name={brand.name} logo={brand.logo} />
      </Link>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('검색')}
            aria-label={t('대화 빠른 검색')}
            className="h-8 bg-transparent pl-8 text-base"
          />
        </div>
      </div>

      {/* 시작하는 곳은 하나. 어느 화면으로 만들지는 홈의 입력창 위에서 고릅니다. */}
      <nav className="px-3 pb-2">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-control px-2.5 py-1.5 text-base transition-colors',
              isActive
                ? 'bg-elevated font-medium text-fg'
                : 'text-muted hover:bg-elevated hover:text-fg',
            )
          }
        >
          <Plus size={15} />
          {t('새로 만들기')}
        </NavLink>
      </nav>

      {/* 관리자 항목은 계정 메뉴 안에 있다. 대기 건수는 계정 버튼에 붙어 있어,
          내비게이션을 한 벌 더 두지 않고도 승인 큐가 스스로 드러난다. */}

      {/* 워크스페이스 + 최근 작업 — 사이드바의 본체입니다. */}
      {/* One scroller for both, because pinning the workspace rows cost what the
          conversations needed: on a 800px-tall laptop the eight fixed rows left
          the list 125px, two conversations, and a row menu with nowhere to open
          — it flipped upward into the clipped edge, where its items drew over
          the navigation and a click on 이름 바꾸기 landed on 커넥터 instead.
          `min-h-0`, not a floor: with a minimum height the region refuses to
          shrink, and on a short window it pushes the credits and the account
          below the fold — the two things that must not go missing. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {workspaceNav.map((group) => (
          <NavSection key={group.id} id={group.id} label={t(group.label)}>
            {group.items.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2.5 rounded-control px-2.5 py-1.5 text-base transition-colors',
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
        ))}

        <div className="border-t border-line px-3 py-2">
          {pinned.length > 0 && (
            <section className="mb-3">
              <p className="px-2.5 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
                {t('고정됨')}
              </p>
              <div className="space-y-0.5">{pinned.map(renderRow)}</div>
            </section>
          )}
          {groups.map((g) => (
            <section key={g.label} className="mb-3">
              <p className="px-2.5 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
                {t(g.label)}
              </p>
              <div className="space-y-0.5">{g.items.map(renderRow)}</div>
            </section>
          ))}
          {hidden > 0 && (
            <button
              onClick={() => setShown((n) => n + PAGE)}
              className="mt-1 w-full rounded-control px-2.5 py-1.5 text-sm text-muted transition-colors hover:bg-elevated hover:text-fg"
            >
              {t('이전 대화')} {hidden.toLocaleString()}{t('개 더 보기')}
            </button>
          )}
          {filtered.length === 0 && (
            <p className="px-2.5 py-6 text-center text-base text-faint">{t('검색 결과가 없습니다')}</p>
          )}
        </div>
      </div>

      {/* 크레딧 + 계정 — always on screen, at any height. */}
      <div className="shrink-0 border-t border-line p-2">
        <button
          aria-label={t('이번 달 사용량')}
          onClick={() => navigate('/usage')}
          className="mb-1 block w-full rounded-control px-2 py-1.5 text-left transition-colors hover:bg-elevated"
        >
          <span className="flex items-center justify-between text-xs">
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

        <AccountMenu />
      </div>
    </aside>
  )
}
