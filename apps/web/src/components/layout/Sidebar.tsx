import { Bot, Boxes, FolderMinus, Layers, MoreHorizontal, Pencil, Pin, PinOff, Plus, Search, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { ConfirmDialog, Dropdown, Input, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Project, Session } from '@/types'
import { useT } from '@/lib/useT'
import { AccountMenu, AccountMenuCompact } from './AccountMenu'
import { Brand } from './Brand'

const rowBase =
  'flex items-center gap-2.5 rounded-control px-2.5 py-1.5 text-base transition-colors'

const rowState = (active: boolean) =>
  active
    ? 'bg-selected font-medium text-fg'
    : 'text-muted hover:bg-elevated hover:text-fg'

/** Unpinned conversations rendered per page. */
const PAGE = 40

interface NavRow {
  to: string
  label: string
  icon: typeof Bot
}

// The rest of the workspace (agents, skills, connectors, memory, designs) lives in the account menu.
const workspaceNav: NavRow[] = [
  { to: '/projects', label: '프로젝트', icon: Boxes },
  { to: '/artifacts', label: '아티팩트', icon: Layers },
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
        active
          ? 'bg-selected font-medium text-fg'
          : 'text-muted hover:bg-elevated hover:text-fg',
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
          <button
            className={cn(
              'mr-1 grid size-8 shrink-0 place-items-center rounded-control text-faint transition-colors hover:bg-line hover:text-fg',
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
    deleteSession,
    renameSession,
    togglePinSession,
    moveSessionToProject,
    user,
  } = useStore()

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q ? sessions.filter((s) => s.title.toLowerCase().includes(q)) : sessions
    return [...list].sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt))
  }, [sessions, query])

  const pinned = filtered.filter((s) => s.pinned)
  const unpinned = filtered.filter((s) => !s.pinned)
  // Rendered unpinned rows are capped; search still covers all of them.
  const [shown, setShown] = useState(PAGE)
  // Row awaiting delete confirmation.
  const [confirming, setConfirming] = useState<Session | null>(null)
  // The next page loads when the "more" button scrolls into view.
  const moreRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const el = moreRef.current
    if (!el) return
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setShown((n) => n + PAGE)
      },
      { rootMargin: '200px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [shown, query])

  const visible = unpinned.slice(0, shown)
  const hidden = unpinned.length - visible.length

  const renderRow = (session: Session) => (
    <SessionRow
      key={session.id}
      session={session}
      active={openSessionId === session.id}
      emoji={projects.find((p) => p.id === session.projectId)?.emoji}
      projects={projects}
      onOpen={() => navigate(`/s/${session.id}`)}
      onRename={(title) => void renameSession(session.id, title)}
      onTogglePin={() => togglePinSession(session.id)}
      onMove={(projectId) => void moveSessionToProject(session.id, projectId)}
      onDelete={() => setConfirming(session)}
    />
  )

  // From the route, not `activeSessionId`, which outlives the conversation screen.
  const openSessionId = useLocation().pathname.match(/^\/s\/([^/]+)/)?.[1] ?? null

  const rail = useStore((s) => s.sidebar) === 'rail'

  const used = user?.creditsUsed ?? 0
  const total = user?.monthlyCredits ?? 0
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0

  if (rail) {
    return (
      <aside className="flex h-full w-16 shrink-0 flex-col items-center border-r border-line bg-sidebar py-3 transition-[width] duration-300">
        <Brand name={brand.name} logo={brand.logo} markOnly />
        <nav className="mt-3 flex flex-col items-center gap-1">
          {[{ to: '/', label: '새로 만들기', icon: Plus, end: true }, ...workspaceNav].map(
            ({ to, label, icon: Icon, ...rest }) => (
              <NavLink
                key={to}
                to={to}
                end={'end' in rest ? (rest as { end?: boolean }).end : undefined}
                title={t(label)}
                aria-label={t(label)}
                className={({ isActive }) =>
                  cn(
                    'grid size-9 place-items-center rounded-control transition-colors',
                    isActive
                      ? 'bg-selected text-fg'
                      : 'text-muted hover:bg-elevated hover:text-fg',
                  )
                }
              >
                <Icon size={17} />
              </NavLink>
            ),
          )}
        </nav>
        <div className="mt-auto">
          <AccountMenuCompact />
        </div>
      </aside>
    )
  }

  return (
    <aside className="flex h-full w-[268px] shrink-0 flex-col border-r border-line bg-sidebar transition-[width] duration-300">
      <div className="flex items-center gap-2 px-3 py-3">
        <Brand name={brand.name} logo={brand.logo} />
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('검색')}
            aria-label={t('대화 빠른 검색')}
            data-session-search=""
            className="h-8 bg-transparent pl-8 text-base"
          />
        </div>
      </div>

      <nav className="space-y-0.5 px-3 pb-2">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            cn(rowBase, rowState(isActive))
          }
        >
          <Plus size={15} />
          {t('새로 만들기')}
        </NavLink>
        {workspaceNav.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(rowBase, rowState(isActive))
            }
          >
            <Icon size={15} />
            {t(label)}
          </NavLink>
        ))}
      </nav>

      {/* `min-h-0` so a short window shrinks the list, not the footer. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="border-t border-line px-3 py-2">
          {pinned.length > 0 && (
            <section className="mb-3">
              <p className="px-2.5 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
                {t('고정됨')}
              </p>
              <div className="space-y-0.5">{pinned.map(renderRow)}</div>
            </section>
          )}
          {visible.length > 0 && (
            <section className="mb-3">
              <p className="px-2.5 pb-1 text-xs font-semibold tracking-wide text-faint uppercase">
                {t('작업 목록')}
              </p>
              <div className="space-y-0.5">{visible.map(renderRow)}</div>
            </section>
          )}
          {hidden > 0 && (
            <button
              ref={moreRef}
              onClick={() => setShown((n) => n + PAGE)}
              className="mt-1 min-h-8 w-full rounded-control px-2.5 py-1.5 text-sm text-muted transition-colors hover:bg-elevated hover:text-fg"
            >
              {t('이전 대화')} {hidden.toLocaleString()}{t('개 더 보기')}
            </button>
          )}
          {filtered.length === 0 && (
            <p className="px-2.5 py-6 text-center text-base text-faint">
              {query.trim() ? t('검색 결과가 없습니다') : t('아직 대화가 없습니다')}
            </p>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-line p-2">
        <button
          aria-label={t('이번 달 사용량')}
          onClick={() => navigate('/usage')}
          title={t(
            '크레딧은 모델을 쓸 때마다 줄어듭니다. 글은 조금, 그림과 영상은 많이 듭니다. 다 쓰면 다음 달까지 새 요청을 보낼 수 없고, 만든 것은 그대로 남습니다. 눌러서 무엇에 얼마나 썼는지 봅니다.',
          )}
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

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void deleteSession(confirming.id)}
        title={t('{name} 삭제').replace('{name}', confirming?.title ?? '')}
        description={t('되돌릴 수 없습니다. 아티팩트와 프로젝트, 메모리는 지워지지 않습니다.')}
      />
    </aside>
  )
}
