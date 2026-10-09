import {
  ArrowRight,
  ArrowUpDown,
  Check,
  ChevronDown,
  FilePlus2,
  FileUp,
  Lock,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { DesignExplore } from '@/components/customize/DesignsTab'
import {
  CARD_TABS,
  HUB_TABS,
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
import { ItemCard, ItemIconTile } from '@/components/customize/ItemCard'
import { ShippedWorkflows } from '@/components/customize/ShippedWorkflows'
import { useItemActions } from '@/components/customize/tryItem'
import { Button, Dropdown, EmptyState, Input, LoadingState, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { BulkBar, useBulkSelect } from '@/components/ui/BulkSelect'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { kindMeta } from '@/lib/kinds'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { ConnectorsPage } from '@/pages/ConnectorsPage'
import { MemoryPage } from '@/pages/MemoryPage'
import { DesignsSection } from '@/pages/settings/DesignsSection'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

type View = 'mine' | 'explore'
type Section = 'featured' | 'popular' | 'new'

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
  const all: Section | null = rawAll === 'featured' || rawAll === 'popular' || rawAll === 'new' ? rawAll : null
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
  return (
    <div role="tablist" aria-label={t('사용자 지정')} className="-mx-1 flex min-w-0 gap-1 overflow-x-auto px-1 [scrollbar-width:none]">
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
          {t(TAB_LABEL[id])}
        </button>
      ))}
    </div>
  )
}

/** 내 항목 / 탐색. */
function Segment({ view, onChange }: { view: View; onChange: (next: View) => void }) {
  const t = useT()
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
          {id === 'mine' ? t('내 항목') : t('탐색')}
        </button>
      ))}
    </div>
  )
}

/** 「+ 추가 ▾」: what can be made on this tab. */
function AddMenu({ tab, compact = false }: { tab: HubTab; compact?: boolean }) {
  const t = useT()
  const navigate = useNavigate()
  const setNotice = useStore((s) => s.setNotice)
  const createWorkflow = async () => {
    try {
      const made = await userPipelinesApi.create({ name: t('새 워크플로') })
      navigate(`/customize/workflows/${made.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t('워크플로를 만들지 못했습니다.')))
    }
  }
  const items: { label: string; icon: ReactNode; run: () => void }[] =
    tab === 'agents'
      ? [{ label: '에이전트 만들기', icon: <Pencil size={14} />, run: () => navigate('/customize/agents/new') }]
      : tab === 'skills'
        ? [{ label: '스킬 만들기', icon: <Pencil size={14} />, run: () => navigate('/customize/skills/new') }]
        : tab === 'workflows'
          ? [{ label: '워크플로 만들기', icon: <Pencil size={14} />, run: () => void createWorkflow() }]
          : tab === 'designs'
            ? [
                { label: '디자인 시스템 만들기', icon: <FilePlus2 size={14} />, run: () => navigate('/customize?tab=designs&view=mine&add=new') },
                { label: '파일에서 읽어 오기', icon: <FileUp size={14} />, run: () => navigate('/customize?tab=designs&view=mine&add=extract') },
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

function SearchBox({ value, onChange, phone }: { value: string; onChange: (next: string) => void; phone: boolean }) {
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
    <div className={cn('relative min-w-0', phone ? 'flex-1' : 'w-72 max-lg:w-56')}>
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

/** The one item the 탐색 page leads with. */
function Banner({ item, onTry, onAdd }: { item: HubItem; onTry: (item: HubItem) => Promise<void>; onAdd: (item: HubItem) => Promise<void> }) {
  const t = useT()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const act = async (fn: (item: HubItem) => Promise<void>) => {
    setBusy(true)
    try {
      await fn(item)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section
      aria-label={t('추천 항목')}
      data-testid="hub-banner"
      className="flex items-center gap-6 rounded-panel border border-accent/15 bg-accent-soft px-8 py-7 max-sm:px-5 max-sm:py-5"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm text-muted">{item.official ? t('공식 제공') : `${t('제작')}: ${item.maker}`}</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-tight">
          <button type="button" className="text-left hover:underline" onClick={() => item.to && navigate(item.to)}>
            {t(item.name)}
          </button>
        </h2>
        {item.summary && <p className="mt-1.5 line-clamp-2 text-md text-muted">{t(item.summary)}</p>}
        <div className="mt-4 flex gap-2">
          {item.added ? (
            <Button variant="primary" disabled={busy} onClick={() => void act(onTry)}>
              {t('체험')}
            </Button>
          ) : (
            <>
              <Button variant="primary" disabled={busy} onClick={() => void act(onAdd)}>
                <Plus size={15} />
                {t('추가')}
              </Button>
              <Button disabled={busy} onClick={() => void act(onTry)}>
                {t('체험')}
              </Button>
            </>
          )}
        </div>
      </div>
      <span className="max-sm:hidden">
        <ItemIconTile item={item} size="lg" />
      </span>
    </section>
  )
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

/** /customize — 사용자 지정: agents, skills, workflows, connectors, memory and designs in one place. */
export function CustomizePage() {
  const t = useT()
  const navigate = useNavigate()
  const phone = useMediaQuery('(max-width: 40rem)')
  const { params, tab, sort, filter, all, update } = useHubParams()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const loadSkillStore = useStore((s) => s.loadSkillStore)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const designs = useStore((s) => s.designs)
  const deleteMany = useStore((s) => s.deleteMany)
  const [picking, setPicking] = useState(false)
  const [sheet, setSheet] = useState(false)

  useEffect(() => {
    void loadWorkspace()
    void loadSkillStore()
  }, [loadWorkspace, loadSkillStore])

  const hub = useHubItems(tab)
  const { tryItem, addItem } = useItemActions(hub.reloadPipelines)
  const cardTab = CARD_TABS.includes(tab)
  const hasView = cardTab || tab === 'designs'
  const rawView = params.get('view')
  const fallback: View =
    tab === 'designs'
      ? designs.some((d) => d.mine)
        ? 'mine'
        : 'explore'
      : hub.mine.length > 0 || hub.loading || workspaceLoading
        ? 'mine'
        : 'explore'
  const view: View = rawView === 'mine' || rawView === 'explore' ? rawView : fallback
  const focus = params.get('focus')
  const add = params.get('add')

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
    const to = await addItem(item)
    // A shipped flow is copied to be edited: open the copy.
    if (to && item.shippedAgent) navigate(to)
  }
  const openShipped = (item: HubItem) => update({ view: 'mine', focus: item.id, all: null })
  const card = (item: HubItem, v: View) => (
    <ItemCard
      key={item.key}
      item={item}
      view={v}
      onTry={tryItem}
      onAdd={onAdd}
      onOpen={item.shippedAgent ? openShipped : undefined}
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
          {(tab === 'agents' || tab === 'skills') && hub.mine.some((i) => i.deletable) && (
            <Button
              size="sm"
              variant={picking ? 'secondary' : 'ghost'}
              className="ml-auto"
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
        {tab === 'workflows' && hub.locked.length > 0 && (
          <details className="group rounded-card border border-line" open={!!focus}>
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-base font-medium select-none [&::-webkit-details-marker]:hidden">
              <Lock size={14} aria-hidden className="text-faint" />
              {t('기본 워크플로 (잠김)')}
              <span className="text-faint">{hub.locked.length}</span>
              <ChevronDown size={15} aria-hidden className="ml-auto text-faint transition-transform group-open:rotate-180" />
            </summary>
            <div className="border-t border-line p-4">
              <ShippedWorkflows agents={hub.locked.map((i) => i.shippedAgent!)} focus={focus} />
            </div>
          </details>
        )}
      </div>
    )
  }

  const exploreBody = () => {
    if (hub.loading && hub.explore.length === 0) return <LoadingState />
    if (hub.explore.length === 0) {
      return (
        <EmptyState
          icon={<Search size={18} />}
          title={t('아직 공유된 {kind} 항목이 없습니다').replace('{kind}', kindWord)}
          description={t('내 항목을 모두에게 공개하면 여기에 올라옵니다.')}
        />
      )
    }
    if (anyFilter || all) {
      const rows = all === 'featured' ? filtered.filter((i) => i.official) : filtered
      return (
        <div className="space-y-4">
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
    const official = sortItems(hub.explore.filter((i) => i.official), 'popular')
    const popular = sortItems(hub.explore.filter((i) => i.installs > 0), 'popular')
    const fresh = sortItems(hub.explore, 'recent')
    const featured = official[0] ?? popular[0] ?? null
    return (
      <div className="space-y-10">
        {featured && <Banner item={featured} onTry={tryItem} onAdd={onAdd} />}
        {official.length > 0 && (
          <section aria-label={t('추천')}>
            <SectionHead title={t('추천')} onAll={official.length > PER_SECTION ? () => update({ all: 'featured', sort: 'popular' }) : undefined} />
            <Grid>{official.slice(0, PER_SECTION).map((item) => card(item, 'explore'))}</Grid>
          </section>
        )}
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

  const searchable = cardTab || (tab === 'designs' && view === 'explore')
  const filterCount = filter.status.length + filter.kinds.length

  return (
    <>
      <TopBar
        left={<span className="text-base font-medium">{t('사용자 지정')}</span>}
        right={phone && (cardTab || tab === 'designs') ? <AddMenu tab={tab} compact /> : undefined}
      />
      <PageBody>
        <div className="mb-5 flex items-center justify-between gap-3 max-sm:mb-3">
          <h1 className="text-2xl font-semibold tracking-tight">{t('사용자 지정')}</h1>
          {!phone && <AddMenu tab={tab} />}
        </div>

        {phone ? (
          <div className="mb-5 space-y-2.5">
            <TabStrip tab={tab} onChange={setTab} />
            {hasView && (
              <div className="flex items-center gap-1.5">
                <Segment view={view} onChange={(next) => update({ view: next, all: null })} />
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
          <div className="mb-6 flex flex-wrap items-center gap-3">
            <TabStrip tab={tab} onChange={setTab} />
            {hasView && <span aria-hidden className="h-6 w-px bg-line" />}
            {hasView && <Segment view={view} onChange={(next) => update({ view: next, all: null })} />}
            <span className="flex-1" />
            {searchable && <SearchBox value={filter.q} onChange={(q) => update({ q })} phone={false} />}
            {cardTab && <FilterMenu filter={filter} onStatus={onStatus} onKind={onKind} onClear={clearFilters} />}
            {cardTab && <SortMenu sort={sort} onSort={(next) => update({ sort: next === 'popular' ? null : next })} />}
          </div>
        )}

        <div role="tabpanel" aria-label={t(TAB_LABEL[tab])}>
          {tab === 'connectors' && <ConnectorsPage embedded />}
          {tab === 'memory' && <MemoryPage embedded />}
          {tab === 'designs' &&
            (view === 'mine' ? (
              <DesignsSection key={add ?? 'list'} initialMode={add === 'new' || add === 'extract' ? add : undefined} />
            ) : (
              <DesignExplore query={filter.q} />
            ))}
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
