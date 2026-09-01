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

/**
 * How a navigation row says it is the one you are on: a ground that separates
 * from the sidebar, and the full text colour at medium weight.
 */
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

/**
 * What the sidebar keeps: the two places conversations are filed. Everything
 * else the workspace holds — agents, skills, connectors, memory, designs — is
 * set up once and then used from the composer, so it lives in the account menu
 * and leaves this column to the list it exists for.
 */
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
          /* Fading in on hover keeps the list quiet, but hover is a thing only a
             mouse has: on a tablet this was the only way to rename, pin or
             delete a conversation, and it never appeared. So the row shows it
             outright where the pointer cannot hover, on keyboard focus, and for
             as long as its own menu is open. */
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
    /**
     * Cap on rendered unpinned conversations. The sidebar is on every screen,
     * so the full list would be hundreds of buttons reconciled on every store
     * change. Search still looks at all of them.
     */
  const [shown, setShown] = useState(PAGE)
  /**
   * The row waiting for a yes. 삭제 sits one row under 고정 in the same menu
   * and the server delete is final — there is no soft delete to restore from
   * — so the question is asked before the request goes, with the title in it.
   */
  const [confirming, setConfirming] = useState<Session | null>(null)
  /**
   * The list goes to the end of the history now that 대화 기록 is not a
   * separate screen, so the last page has to arrive on its own rather than on
   * a click every forty rows.
   */
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

  /**
   * Which row is the screen you are on — a question about the route, not about
   * store state. `activeSessionId` outlives the conversation screen: nothing
   * clears it on the way out, so leaving a chat for 홈 left its row lit beside
   * the newly lit 새로 만들기.
   */
  const openSessionId = useLocation().pathname.match(/^\/s\/([^/]+)/)?.[1] ?? null

  const rail = useStore((s) => s.sidebar) === 'rail'

  const used = user?.creditsUsed ?? 0
  const total = user?.monthlyCredits ?? 0
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0

  /**
   * The collapsed state used to be no panel at all. What a rail keeps is the
   * things you navigate *to*; what it gives up is the list, the search over it
   * and the credit figure — none of which survive 64px, all of which are one
   * press away. 계정 stays because the way out of an account cannot be behind
   * a state the account is already in.
   */
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
      {/* 이름은 이름일 뿐입니다. 시작하는 행동은 바로 아래 새로 만들기가 맡습니다. */}
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
            className="h-8 bg-transparent pl-8 text-base"
          />
        </div>
      </div>

      {/* 시작하는 곳 하나와 대화가 놓이는 두 곳. 세 줄이 한 덩어리로 읽히도록
          같은 간격을 씁니다 — 예전에는 스크롤 경계가 사이에 있어 첫 줄과 둘째
          줄만 16px 떨어져 있었습니다. 워크스페이스가 여덟 줄이던 시절 목록의
          높이를 지키려고 스크롤 안에 두었던 것이고, 지금은 두 줄입니다. */}
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

      {/* 관리자 항목은 계정 메뉴 안에 있다. 대기 건수는 계정 버튼에 붙어 있어,
          내비게이션을 한 벌 더 두지 않고도 승인 큐가 스스로 드러난다. */}

      {/* `min-h-0`, not a floor: with a minimum height the region refuses to
          shrink, and on a short window it pushes the credits and the account
          below the fold — the two things that must not go missing. */}
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
          {/* Reaching the end of the list is the request for more of it. The
              count stays as the label, so a reader still knows how much is
              behind them, and a keyboard can page without a pointer. */}
          {hidden > 0 && (
            <button
              ref={moreRef}
              onClick={() => setShown((n) => n + PAGE)}
              className="mt-1 w-full rounded-control px-2.5 py-1.5 text-sm text-muted transition-colors hover:bg-elevated hover:text-fg"
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

      {/* 크레딧 + 계정 — always on screen, at any height. */}
      <div className="shrink-0 border-t border-line p-2">
        <button
          aria-label={t('이번 달 사용량')}
          onClick={() => navigate('/usage')}
          /* A number with no unit and no consequence. Somebody seeing 크레딧
             for the first time cannot tell whether 1,812,679 is a lot, what
             spends it, or what happens at zero — and the answer is one
             sentence, so there is no reason for it to be a page away. */
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
