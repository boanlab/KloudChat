import { useMemo } from 'react'
import { agentKinds } from '@/lib/kinds'
import { isListed } from '@/lib/listed'
import { useStore } from '@/store/useStore'
import type { Agent, SessionKind, Skill, StoreSkill } from '@/types'
import type { ItemType } from './itemIcons'

/** Hub tabs in order; templates picked in agent form and composer, themes and memory in 설정. */
export const HUB_TABS = ['agents', 'skills', 'connectors'] as const
export type HubTab = (typeof HUB_TABS)[number]

/** Card-list tabs: 내 항목 / 탐색, search, filter, sort. */
export const CARD_TABS: HubTab[] = ['agents', 'skills']

export const TAB_LABEL: Record<HubTab, string> = {
  agents: '에이전트',
  skills: '스킬',
  connectors: '커넥터',
}

/** Destination of a tab that left the hub, for old `?tab=` links. */
export const MOVED_TABS: Record<string, string> = {
  workflows: '/customize?tab=agents',
  templates: '/customize?tab=agents',
  designs: '/settings/themes',
  memory: '/settings/memory',
}

export type ItemStatus = 'on' | 'off' | 'draft' | 'review'

export const STATUS_LABEL: Record<ItemStatus, string> = {
  on: '켜짐',
  off: '꺼짐',
  draft: '초안',
  review: '검토 중',
}

/** One hub card row, whatever it is underneath. */
export interface HubItem {
  key: string
  type: ItemType
  id: string
  name: string
  summary: string
  surfaces: SessionKind[]
  color: string | null
  /** 「공식」 or the author's name. */
  maker: string
  official: boolean
  installs: number
  runs: number | null
  updatedAt: string
  status: ItemStatus
  shared: boolean
  /** In 내 항목. */
  mine: boolean
  /** Store row the person already holds a copy of. */
  added: boolean
  deletable: boolean
  /** 숨김: shipped part with `listed: false`, shown only with 「숨김 포함」. */
  hidden: boolean
  /** Detail screen; null when none (a shipped flow opens its locked section). */
  to: string | null
  agent?: Agent
  skill?: Skill | StoreSkill
}

export function agentItem(a: Agent, me: string | undefined): HubItem {
  const mine = a.ownerId === me
  return {
    key: `agent:${a.id}`,
    type: 'agent',
    id: a.id,
    name: a.name,
    summary: a.description,
    surfaces: agentKinds(a),
    color: a.color,
    maker: a.official ? '공식' : a.ownerName,
    official: a.official || (!!a.catalogKey && !mine),
    installs: a.installs,
    runs: mine ? a.runs : null,
    updatedAt: a.updatedAt,
    status: a.enabled ? 'on' : 'off',
    shared: a.visibility === 'org',
    mine,
    added: a.installed,
    deletable: mine,
    hidden: false,
    to: `/customize/agents/${a.id}`,
    agent: a,
  }
}

export function skillItem(s: Skill | StoreSkill, store: boolean): HubItem {
  const row = s as StoreSkill
  // shared skills, shipped ones included, are labelled by who shares them
  const official = store && row.official
  return {
    key: `skill:${s.id}`,
    type: 'skill',
    id: s.id,
    name: s.name,
    summary: s.description,
    surfaces: s.kinds,
    color: null,
    maker: store ? row.ownerName : s.originOwnerName || (s.source === 'workspace' ? '워크스페이스' : ''),
    official,
    installs: s.installs,
    runs: null,
    updatedAt: s.updatedAt,
    status: s.enabled ? 'on' : s.source === 'personal' ? 'draft' : 'off',
    shared: s.visibility === 'org',
    mine: !store,
    added: store ? row.installed : true,
    deletable: !store,
    hidden: !isListed(s),
    to: `/customize/skills/${s.id}`,
    skill: s,
  }
}

/** A card tab's items as 내 항목 and 탐색; hidden ones only with `showHidden` (`hiddenMine` counts held ones). */
export function useHubItems(tab: HubTab, showHidden = false) {
  const agents = useStore((s) => s.agents)
  const skills = useStore((s) => s.skills)
  const skillStore = useStore((s) => s.skillStore)
  const me = useStore((s) => s.user?.id)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const skillStoreLoaded = useStore((s) => s.skillStoreLoaded)

  const items = useMemo(() => {
    if (tab === 'agents') {
      return {
        mine: agents.filter((a) => a.ownerId === me).map((a) => agentItem(a, me)),
        explore: agents.filter((a) => a.visibility === 'org' && a.ownerId !== me).map((a) => agentItem(a, me)),
        locked: [] as HubItem[],
      }
    }
    if (tab === 'skills') {
      return {
        mine: skills.map((s) => skillItem(s, false)),
        explore: skillStore.map((s) => skillItem(s, true)),
        locked: [] as HubItem[],
      }
    }
    return { mine: [] as HubItem[], explore: [] as HubItem[], locked: [] as HubItem[] }
  }, [tab, agents, skills, skillStore, me])

  const shown = (rows: HubItem[]) => (showHidden ? rows : rows.filter((i) => !i.hidden))
  return {
    mine: shown(items.mine),
    explore: shown(items.explore),
    locked: items.locked,
    hiddenMine: items.mine.filter((i) => i.hidden).length,
    hiddenAny: items.mine.some((i) => i.hidden) || items.explore.some((i) => i.hidden),
    // Before the lists answer: loading, not empty.
    loading: workspaceLoading || (tab === 'skills' && !skillStoreLoaded),
  }
}

export type SortKey = 'popular' | 'recent' | 'name'

export function sortItems(rows: HubItem[], sort: SortKey): HubItem[] {
  const out = [...rows]
  if (sort === 'name') out.sort((a, b) => a.name.localeCompare(b.name, 'ko'))
  else if (sort === 'recent') out.sort((a, b) => +new Date(b.updatedAt || 0) - +new Date(a.updatedAt || 0))
  else out.sort((a, b) => b.installs - a.installs || (b.runs ?? 0) - (a.runs ?? 0) || a.name.localeCompare(b.name, 'ko'))
  return out
}

export interface ItemFilter {
  q: string
  /** 상태: on | off | draft | review | shared. */
  status: string[]
  /** 화면: chat | report | slides. */
  kinds: SessionKind[]
}

export function filterItems(rows: HubItem[], filter: ItemFilter, translate: (s: string) => string): HubItem[] {
  const needle = filter.q.trim().toLowerCase()
  return rows.filter((row) => {
    if (needle) {
      const text = `${row.name} ${translate(row.name)} ${row.summary} ${translate(row.summary)} ${row.maker}`.toLowerCase()
      if (!text.includes(needle)) return false
    }
    if (filter.status.length > 0) {
      const hit = filter.status.some((s) => (s === 'shared' ? row.shared : row.status === s))
      if (!hit) return false
    }
    if (filter.kinds.length > 0 && !filter.kinds.some((k) => row.surfaces.includes(k))) return false
    return true
  })
}

/** 「제작: 공식 · n회 추가」 for 탐색, 「n회 사용 · 켜짐」 for 내 항목. */
export function itemMeta(item: HubItem, view: 'mine' | 'explore', t: (s: string) => string): string {
  const parts: string[] = []
  if (view === 'explore') {
    if (item.maker) parts.push(`${t('제작')}: ${t(item.maker)}`)
    if (item.installs > 0) parts.push(t('{n}회 추가').replace('{n}', item.installs.toLocaleString()))
  } else {
    if (item.runs !== null) parts.push(t('{n}회 사용').replace('{n}', item.runs.toLocaleString()))
    else if (item.maker) parts.push(`${t('제작')}: ${t(item.maker)}`)
  }
  return parts.join(' · ')
}
