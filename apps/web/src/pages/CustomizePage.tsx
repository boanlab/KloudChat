import {
  ArrowRight,
  ArrowUpDown,
  Check,
  ChevronDown,
  Layers,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Upload,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  CARD_TABS,
  HUB_TABS,
  MOVED_TABS,
  STATUS_LABEL,
  TAB_LABEL,
  filterItems,
  sortItems,
  useHubItems,
  type HubItem,
  type HubTab,
  type ItemFilter,
  type SortKey,
} from '@/components/customize/hubItems'
import { ItemCard } from '@/components/customize/ItemCard'
import { useItemActions } from '@/components/customize/tryItem'
import { Button, Dropdown, EmptyState, Input, LoadingState, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { BulkBar, useBulkSelect } from '@/components/ui/BulkSelect'
import { kindMeta } from '@/lib/kinds'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT, useTIn } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { ConnectorsPage } from '@/pages/ConnectorsPage'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

type View = 'mine' | 'explore'
type Section = 'popular' | 'new'

const STATUS_KEYS = ['on', 'off', 'draft', 'shared', 'review'] as const
const SURFACES: SessionKind[] = ['chat', 'report', 'slides']
const SORT_LABEL: Record<SortKey, string> = { popular: '인기', recent: '최신', name: '이름' }
const PER_SECTION = 6

const statusLabel = (key: string) => (key === 'shared' ? '공유됨' : STATUS_LABEL[key as keyof typeof STATUS_LABEL])

const list = (raw: string | null) => (raw ? raw.split(',').filter(Boolean) : [])

/** The hub's state lives in the address: tab, view, query, filters, sort, a section opened whole. */
function useHubParams() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab: HubTab = HUB_TABS.includes(raw as HubTab) ? (raw as HubTab) : 'agents'
  const rawSort = params.get('sort')
  const sort: SortKey = rawSort === 'recent' || rawSort === 'name' ? rawSort : 'popular'
  const filter: ItemFilter = {
    q: params.get('q') ?? '',
    status: list(params.get('status')),
    kinds: list(params.get('kind')).filter((k): k is SessionKind => SURFACES.includes(k as SessionKind)),
  }
  const rawAll = params.get('all')
  const all: Section | null = rawAll === 'popular' || rawAll === 'new' ? rawAll : null
  const update = (patch: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (!next.has('tab')) next.set('tab', tab)
        for (const [key, value] of Object.entries(patch)) {
          if (value === null || value === '') next.delete(key)
          else next.set(key, value)
        }
        return next
      },
      { replace: true },
    )
  return { params, tab, sort, filter, all, update }
}

/** Tabs as pills; on a phone they scroll sideways. */
function TabStrip({ tab, onChange }: { tab: HubTab; onChange: (next: HubTab) => void }) {
  const t = useT()
  const tHub = useTIn('hub')
  const strip = useRef<HTMLDivElement>(null)
  // On a phone the strip scrolls: keep the open tab in view.
  useEffect(() => {
    const on = strip.current?.querySelector<HTMLElement>('[aria-selected="true"]')
    on?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [tab])
  return (
    <div ref={strip} role="tablist" aria-label={t('사용자 지정')} className="-mx-1 flex min-w-0 gap-1 overflow-x-auto px-1 [scrollbar-width:none]">
      {HUB_TABS.map((id) => (
        <button
          key={id}
          role="tab"
          aria-selected={tab === id}
          onClick={() => onChange(id)}
          className={cn(
            'h-9 shrink-0 rounded-control px-3 text-base font-medium whitespace-nowrap transition-colors',
            tab === id ? 'bg-selected text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
          )}
        >
          {tHub(TAB_LABEL[id])}
        </button>
      ))}
    </div>
  )
}

/** 내 항목 / 탐색. */
function Segment({ view, onChange }: { view: View; onChange: (next: View) => void }) {
  const t = useT()
  const tHub = useTIn('hub')
  return (
    <div className="inline-flex shrink-0 rounded-control border border-line bg-elevated p-0.5" role="group" aria-label={t('보기')}>
      {(['mine', 'explore'] as const).map((id) => (
        <button
          key={id}
          type="button"
          aria-pressed={view === id}
          onClick={() => onChange(id)}
          className={cn(
            'h-8 rounded-control px-3 text-base font-medium whitespace-nowrap transition-colors',
            view === id ? 'bg-panel text-fg shadow-raised' : 'text-muted hover:text-fg',
          )}
        >
          {id === 'mine' ? t('내 항목') : tHub('탐색')}
        </button>
      ))}
    </div>
  )
}

/** 「+ 추가 ▾」: what can be made on this tab. */
function AddMenu({ tab, compact = false }: { tab: HubTab; compact?: boolean }) {
  const t = useT()
  const navigate = useNavigate()
  const items: { label: string; icon: ReactNode; run: () => void }[] =
    tab === 'agents'
      ? [
          { label: '에이전트 만들기', icon: <Pencil size={14} />, run: () => navigate('/customize/agents/new') },
          { label: '묶음 만들기', icon: <Layers size={14} />, run: () => navigate('/customize/bundles?new=1') },
        ]
      : tab === 'skills'
        ? [
            { label: '스킬 만들기', icon: <Pencil size={14} />, run: () => navigate('/customize/skills/new') },
            { label: '스킬 업로드', icon: <Upload size={14} />, run: () => navigate('/customize/skills/upload') },
          ]
        : []
  if (items.length === 0) return null
  return (
    <Dropdown
      align="right"
      trigger={({ open }) =>
        compact ? (
          <Button variant="ghost" size="icon" aria-label={t('추가')} title={t('추가')}>
            <Plus size={17} />
          </Button>
        ) : (
          <button
            type="button"
            aria-label={t('추가')}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-control bg-fg px-3.5 text-base font-medium text-bg transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <Plus size={16} />
            {t('추가')}
            <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
          </button>
        )
      }
    >
      {items.map((item) => (
        <MenuItem key={item.label} icon={item.icon} onClick={item.run}>
          {t(item.label)}
        </MenuItem>
      ))}
    </Dropdown>
  )
}

function FilterMenu({
  filter,
  onStatus,
  onKind,
  onClear,
}: {
  filter: ItemFilter
  onStatus: (key: string) => void
  onKind: (kind: SessionKind) => void
  onClear: () => void
}) {
  const t = useT()
  const enabledKinds = useStore((s) => s.enabledKinds)
  const count = filter.status.length + filter.kinds.length
  return (
    <Dropdown
      align="right"
      trigger={() => (
        <Button variant="ghost" size="icon" aria-label={t('필터')} title={t('필터')} className="relative">
          <SlidersHorizontal size={16} />
          {count > 0 && <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-accent" />}
        </Button>
      )}
    >
      <MenuLabel>{t('상태')}</MenuLabel>
      {STATUS_KEYS.map((key) => (
        <MenuItem key={key} keepOpen checked={filter.status.includes(key)} onClick={() => onStatus(key)}>
          {t(statusLabel(key))}
        </MenuItem>
      ))}
      <MenuSeparator />
      <MenuLabel>{t('화면')}</MenuLabel>
      {SURFACES.filter((k) => enabledKinds.includes(k)).map((k) => (
        <MenuItem key={k} keepOpen checked={filter.kinds.includes(k)} onClick={() => onKind(k)}>
          {t(kindMeta[k].label)}
        </MenuItem>
      ))}
      {count > 0 && (
        <>
          <MenuSeparator />
          <MenuItem icon={<X size={14} />} onClick={onClear}>
            {t('필터 지우기')}
          </MenuItem>
        </>
      )}
    </Dropdown>
  )
}

function SortMenu({ sort, onSort }: { sort: SortKey; onSort: (next: SortKey) => void }) {
  const t = useT()
  return (
    <Dropdown
      align="right"
      trigger={() => (
        <Button variant="ghost" size="icon" aria-label={`${t('정렬')} · ${t(SORT_LABEL[sort])}`} title={t('정렬')}>
          <ArrowUpDown size={16} />
        </Button>
      )}
    >
      {(Object.keys(SORT_LABEL) as SortKey[]).map((key) => (
        <MenuItem key={key} checked={sort === key} onClick={() => onSort(key)}>
          {t(SORT_LABEL[key])}
        </MenuItem>
      ))}
    </Dropdown>
  )
}

/** The phone's single 「필터·정렬」 sheet. */
function FilterSheet({
  open,
  onClose,
  filter,
  sort,
  onStatus,
  onKind,
  onSort,
  onClear,
}: {
  open: boolean
  onClose: () => void
  filter: ItemFilter
  sort: SortKey
  onStatus: (key: string) => void
  onKind: (kind: SessionKind) => void
  onSort: (next: SortKey) => void
  onClear: () => void
}) {
  const t = useT()
  const enabledKinds = useStore((s) => s.enabledKinds)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  const chip = (on: boolean, label: string, act: () => void) => (
    <button
      key={label}
      type="button"
      aria-pressed={on}
      onClick={act}
      className={cn(
        'h-9 rounded-full border px-3.5 text-base',
        on ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted',
      )}
    >
      {label}
    </button>
  )
  return (
    <div className="fixed inset-0 z-[150]">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('필터·정렬')}
        className="animate-fade-up absolute inset-x-0 bottom-0 max-h-[80vh] overflow-y-auto rounded-t-panel border-t border-line bg-panel px-4 pt-4"
        style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-md font-semibold">{t('필터·정렬')}</h2>
          <Button variant="ghost" size="icon" aria-label={t('닫기')} onClick={onClose}>
            <X size={16} />
          </Button>
        </div>
        <p className="mb-2 text-sm font-medium text-faint">{t('상태')}</p>
        <div className="mb-4 flex flex-wrap gap-2">
          {STATUS_KEYS.map((key) => chip(filter.status.includes(key), t(statusLabel(key)), () => onStatus(key)))}
        </div>
        <p className="mb-2 text-sm font-medium text-faint">{t('화면')}</p>
        <div className="mb-4 flex flex-wrap gap-2">
          {SURFACES.filter((k) => enabledKinds.includes(k)).map((k) => chip(filter.kinds.includes(k), t(kindMeta[k].label), () => onKind(k)))}
        </div>
        <p className="mb-2 text-sm font-medium text-faint">{t('정렬')}</p>
        <div className="mb-5 flex flex-wrap gap-2">
          {(Object.keys(SORT_LABEL) as SortKey[]).map((key) => chip(sort === key, t(SORT_LABEL[key]), () => onSort(key)))}
        </div>
        <div className="flex gap-2">
          <Button className="flex-1" onClick={onClear}>
            {t('필터 지우기')}
          </Button>
          <Button variant="primary" className="flex-1" onClick={onClose}>
            {t('완료')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function SearchBox({
  value,
  onChange,
  phone,
  className,
}: {
  value: string
  onChange: (next: string) => void
  phone: boolean
  className?: string
}) {
  const t = useT()
  const [open, setOpen] = useState(!!value)
  if (phone && !open) {
    return (
      <Button variant="ghost" size="icon" aria-label={t('검색')} onClick={() => setOpen(true)}>
        <Search size={16} />
      </Button>
    )
  }
  return (
    <div className={cn('relative min-w-0', phone ? 'flex-1' : 'w-72 max-lg:w-56', className)}>
      <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
      <Input
        type="search"
        autoFocus={phone}
        aria-label={t('검색')}
        placeholder={t('검색')}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => phone && !value && setOpen(false)}
        className="pl-9"
      />
    </div>
  )
}

function Grid({ children }: { children: ReactNode }) {
  return <div className="grid gap-3 lg:grid-cols-2">{children}</div>
}

function SectionHead({ title, onAll }: { title: string; onAll?: () => void }) {
  const t = useT()
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="text-md font-semibold">{title}</h2>
      {onAll && (
        <button type="button" onClick={onAll} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
          {t('모두 보기')}
          <ArrowRight size={14} aria-hidden />
        </button>
      )}
    </div>
  )
}

/** /customize — 사용자 지정: agents (with the 묶음 they tick), skills and connectors. */
export function CustomizePage() {
  const [params] = useSearchParams()
  const moved = MOVED_TABS[params.get('tab') ?? '']
  if (moved) return <Navigate to={moved} replace />
  return <Hub />
}

function Hub() {
  const t = useT()
  const navigate = useNavigate()
  const phone = useMediaQuery('(max-width: 40rem)')
  const { params, tab, sort, filter, all, update } = useHubParams()
  const ensureWorkspace = useStore((s) => s.ensureWorkspace)
  const loadSkillStore = useStore((s) => s.loadSkillStore)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const deleteMany = useStore((s) => s.deleteMany)
  const [picking, setPicking] = useState(false)
  const [sheet, setSheet] = useState(false)

  useEffect(() => {
    void ensureWorkspace()
    void loadSkillStore()
  }, [ensureWorkspace, loadSkillStore])

  const showHidden = params.get('hidden') === '1'
  const admin = useStore((s) => s.user?.role === 'admin')
  const hub = useHubItems(tab, showHidden)
  // 숨김 포함: administrators, and anyone holding a hidden part, may show what lists leave out.
  const hiddenToggle = tab === 'skills' && (admin || hub.hiddenMine > 0) ? (
    <Button
      size="sm"
      variant={showHidden ? 'secondary' : 'ghost'}
      aria-pressed={showHidden}
      data-testid="show-hidden"
      title={t('목록에서 숨긴 스킬도 보여 줍니다. 숨긴 스킬도 이름으로 계속 쓸 수 있습니다.')}
      onClick={() => update({ hidden: showHidden ? null : '1' })}
    >
      {showHidden ? <Check size={13} /> : null}
      {t('숨김 포함')}
    </Button>
  ) : null
  const { tryItem, addItem } = useItemActions()
  const cardTab = CARD_TABS.includes(tab)
  const hasView = cardTab
  const rawView = params.get('view')
  const fallback: View = hub.mine.length > 0 || hub.loading || workspaceLoading ? 'mine' : 'explore'
  const view: View = rawView === 'mine' || rawView === 'explore' ? rawView : fallback

  const kindWord = t(TAB_LABEL[tab])
  const filtered = useMemo(
    () => sortItems(filterItems(view === 'mine' ? hub.mine : hub.explore, filter, t), sort),
    [view, hub.mine, hub.explore, filter, sort, t],
  )
  const deletable = view === 'mine' && (tab === 'agents' || tab === 'skills') ? filtered.filter((i) => i.deletable) : []
  const pick = useBulkSelect(deletable)

  const toggleIn = (key: 'status' | 'kind', current: string[], value: string) =>
    update({ [key]: (current.includes(value) ? current.filter((x) => x !== value) : [...current, value]).join(',') })
  const onStatus = (key: string) => toggleIn('status', filter.status, key)
  const onKind = (kind: SessionKind) => toggleIn('kind', filter.kinds, kind)
  const clearFilters = () => update({ status: null, kind: null })
  const setTab = (next: HubTab) => {
    setPicking(false)
    pick.clear()
    navigate(`/customize?tab=${next}`, { replace: false })
  }

  const onAdd = async (item: HubItem) => {
    await addItem(item)
  }
  const card = (item: HubItem, v: View) => (
    <ItemCard
      key={item.key}
      item={item}
      view={v}
      onTry={tryItem}
      onAdd={onAdd}
      picking={picking}
      picked={pick.picked.has(item.id)}
      onPick={(i) => pick.toggle(i.id)}
    />
  )

  const anyFilter = filter.q.trim() !== '' || filter.status.length > 0 || filter.kinds.length > 0

  const mineBody = () => {
    if (hub.loading && hub.mine.length === 0) return <LoadingState />
    const empty = filtered.length === 0
    return (
      <div className="space-y-4">
        {/* Quick status chips, the same filter the popover sets. */}
        <div className="flex flex-wrap items-center gap-1.5">
          {STATUS_KEYS.filter((key) => hub.mine.some((i) => (key === 'shared' ? i.shared : i.status === key))).map((key) => {
            const on = filter.status.includes(key)
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                onClick={() => onStatus(key)}
                className={cn(
                  'h-8 rounded-full border px-3 text-sm transition-colors',
                  on ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-elevated',
                )}
              >
                {t(statusLabel(key))}
                <span className="ml-1 text-faint">
                  {hub.mine.filter((i) => (key === 'shared' ? i.shared : i.status === key)).length}
                </span>
              </button>
            )
          })}
          {hiddenToggle && <span className="ml-auto">{hiddenToggle}</span>}
          {(tab === 'agents' || tab === 'skills') && hub.mine.some((i) => i.deletable) && (
            <Button
              size="sm"
              variant={picking ? 'secondary' : 'ghost'}
              className={hiddenToggle ? undefined : 'ml-auto'}
              aria-pressed={picking}
              onClick={() => {
                if (picking) pick.clear()
                setPicking(!picking)
              }}
            >
              {picking ? <Check size={13} /> : null}
              {t('선택')}
            </Button>
          )}
        </div>
        {picking && (
          <BulkBar
            count={pick.count}
            allPicked={pick.allPicked}
            onToggleAll={pick.toggleAll}
            onClear={pick.clear}
            title={kindWord}
            note={tab === 'agents' ? t('붙여 둔 자료와 검색 색인도 함께 지워집니다.') : undefined}
            onDelete={async () => {
              await deleteMany(tab === 'agents' ? 'agents' : 'skills', pick.ids)
              pick.clear()
              setPicking(false)
            }}
          />
        )}
        {empty ? (
          anyFilter ? (
            <EmptyState icon={<Search size={18} />} title={t('조건에 맞는 항목이 없습니다')} />
          ) : (
            <EmptyState
              icon={<Plus size={18} />}
              title={t('아직 내 {kind} 항목이 없습니다').replace('{kind}', kindWord)}
              description={t('탐색에서 마음에 드는 것을 추가하거나, 직접 만들어 보세요.')}
              action={
                <Button variant="primary" onClick={() => update({ view: 'explore' })}>
                  {t('탐색하기')}
                </Button>
              }
            />
          )
        ) : (
          <Grid>{filtered.map((item) => card(item, 'mine'))}</Grid>
        )}
        {tab === 'agents' && (
          // 묶음 live with the agents that tick them, not in a tab of their own.
          <Link
            to="/customize/bundles"
            className="flex items-center gap-2 rounded-card border border-line px-4 py-3 text-base hover:bg-elevated focus-visible:outline-2 focus-visible:outline-accent"
            data-testid="bundles-link"
          >
            <Layers size={14} aria-hidden className="text-faint" />
            <span className="font-medium">{t('묶음')}</span>
            <span className="min-w-0 flex-1 truncate text-sm text-faint">
              {t('에이전트 설정의 「추가로 할 일」에서 체크하는 할 일 묶음')}
            </span>
            <ArrowRight size={14} aria-hidden className="text-faint" />
          </Link>
        )}
      </div>
    )
  }

  const exploreBody = () => {
    if (hub.loading && hub.explore.length === 0) return <LoadingState />
    const toggleRow = hiddenToggle && <div className="flex justify-end">{hiddenToggle}</div>
    if (hub.explore.length === 0) {
      return (
        <>
          {toggleRow}
          <EmptyState
            icon={<Search size={18} />}
            title={t('아직 공유된 {kind} 항목이 없습니다').replace('{kind}', kindWord)}
            description={t('내 항목을 모두에게 공개하면 여기에 올라옵니다.')}
          />
        </>
      )
    }
    if (anyFilter || all) {
      const rows = filtered
      return (
        <div className="space-y-4">
          {toggleRow}
          {all && (
            <button type="button" onClick={() => update({ all: null })} className="text-sm text-muted hover:text-fg">
              ← {t('탐색으로 돌아가기')}
            </button>
          )}
          {rows.length === 0 ? (
            <EmptyState icon={<Search size={18} />} title={t('조건에 맞는 항목이 없습니다')} />
          ) : (
            <Grid>{rows.map((item) => card(item, 'explore'))}</Grid>
          )}
        </div>
      )
    }
    const popular = sortItems(hub.explore.filter((i) => i.installs > 0), 'popular')
    const fresh = sortItems(hub.explore, 'recent')
    return (
      <div className="space-y-10">
        {toggleRow}
        {popular.length > 0 && (
          <section aria-label={t('가장 많이 추가된 {kind}').replace('{kind}', kindWord)}>
            <SectionHead
              title={t('가장 많이 추가된 {kind}').replace('{kind}', kindWord)}
              onAll={() => update({ all: 'popular', sort: 'popular' })}
            />
            <Grid>{popular.slice(0, PER_SECTION).map((item) => card(item, 'explore'))}</Grid>
          </section>
        )}
        <section aria-label={t('새 {kind}').replace('{kind}', kindWord)}>
          <SectionHead title={t('새 {kind}').replace('{kind}', kindWord)} onAll={() => update({ all: 'new', sort: 'recent' })} />
          <Grid>{fresh.slice(0, PER_SECTION).map((item) => card(item, 'explore'))}</Grid>
        </section>
      </div>
    )
  }

  const searchable = cardTab
  const filterCount = filter.status.length + filter.kinds.length

  return (
    <>
      <TopBar
        left={<span className="text-base font-medium">{t('사용자 지정')}</span>}
        right={phone && cardTab ? <AddMenu tab={tab} compact /> : undefined}
      />
      <PageBody>
        <div className="mb-5 flex items-center justify-between gap-3 max-sm:mb-3">
          <h1 className="text-2xl font-semibold tracking-tight">{t('사용자 지정')}</h1>
          {!phone && <AddMenu tab={tab} />}
        </div>

        {phone ? (
          <div className="mb-5 space-y-2.5">
            <TabStrip tab={tab} onChange={setTab} />
            {(hasView || searchable) && (
              <div className="flex items-center gap-1.5">
                {hasView && <Segment view={view} onChange={(next) => update({ view: next, all: null })} />}
                <span className="flex-1" />
                {searchable && <SearchBox value={filter.q} onChange={(q) => update({ q })} phone />}
                {cardTab && (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={t('필터·정렬')}
                    onClick={() => setSheet(true)}
                    className="relative"
                  >
                    <SlidersHorizontal size={15} />
                    {filterCount > 0 && <span className="absolute top-1 right-1 size-2 rounded-full bg-accent" />}
                  </Button>
                )}
              </div>
            )}
          </div>
        ) : (
          // Too narrow for one row, the search box takes the second line; filter and sort stay up top.
          <div className="@container mb-6">
            <div className="flex flex-wrap items-center gap-3">
              <TabStrip tab={tab} onChange={setTab} />
              {hasView && <span aria-hidden className="h-6 w-px bg-line" />}
              {hasView && <Segment view={view} onChange={(next) => update({ view: next, all: null })} />}
              <span className="flex-1" />
              {searchable && (
                <SearchBox
                  value={filter.q}
                  onChange={(q) => update({ q })}
                  phone={false}
                  className={cardTab ? '@max-[72rem]:order-last' : undefined}
                />
              )}
              {cardTab && <FilterMenu filter={filter} onStatus={onStatus} onKind={onKind} onClear={clearFilters} />}
              {cardTab && <SortMenu sort={sort} onSort={(next) => update({ sort: next === 'popular' ? null : next })} />}
            </div>
          </div>
        )}

        <div role="tabpanel" aria-label={t(TAB_LABEL[tab])}>
          {tab === 'connectors' && <ConnectorsPage embedded />}
          {cardTab && (view === 'mine' ? mineBody() : exploreBody())}
        </div>
      </PageBody>

      <FilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filter={filter}
        sort={sort}
        onStatus={onStatus}
        onKind={onKind}
        onSort={(next) => update({ sort: next === 'popular' ? null : next })}
        onClear={clearFilters}
      />
    </>
  )
}
