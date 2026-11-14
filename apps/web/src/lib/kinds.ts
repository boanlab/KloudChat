import { Clapperboard, FileText, Image, MessageSquare, Presentation } from 'lucide-react'
import type { Agent, SessionKind, Skill } from '@/types'

/** Single source of truth for the five surfaces: nav, empty states, badges and routing read from here. */
export const kindMeta: Record<
  SessionKind,
  {
    label: string
    icon: typeof MessageSquare
    color: string
    /** One line saying what this surface makes; all five must read the same shape. */
    tagline: string
    panelLabel: string
  }
> = {
  chat: {
    label: '챗',
    icon: MessageSquare,
    color: '#5b53e8',
    tagline: '묻고, 파일을 올리고, 필요하면 검색과 코드까지 씁니다',
    panelLabel: '아티팩트 열기',
  },
  report: {
    label: '보고서',
    icon: FileText,
    color: '#0e8a5f',
    tagline: '웹 보고서·한글·Word로 씁니다. 형식과 템플릿을 고르고 주제를 적으세요',
    panelLabel: '보고서 열기',
  },
  slides: {
    label: '슬라이드',
    icon: Presentation,
    color: '#b4780a',
    tagline: '웹 슬라이드·PowerPoint로 만듭니다. 형식과 템플릿을 고르고 주제를 적으세요',
    panelLabel: '덱 열기',
  },
  image: {
    label: '이미지',
    icon: Image,
    color: '#8b5cf6',
    tagline: '비율과 스타일을 정해 여러 장을 한 번에 만듭니다',
    panelLabel: '이미지 열기',
  },
  av: {
    label: '오디오/동영상',
    icon: Clapperboard,
    color: '#c0392b',
    tagline: '길이와 형식을 정해 만들고 대화 안에서 바로 확인합니다',
    panelLabel: '미디어 열기',
  },
}

export const kindOrder: SessionKind[] = ['chat', 'report', 'slides', 'image', 'av']

/** Surfaces an agent offers, in `kindOrder`; all of them when it names none. */
export function agentKinds(agent: Pick<Agent, 'kinds'>): SessionKind[] {
  return agent.kinds.length ? kindOrder.filter((k) => agent.kinds.includes(k)) : kindOrder
}

/** Chosen skills whose `kinds` share no surface with the agent: they are skipped there. */
export function skillsOffSurface(ids: string[], skills: Skill[], surfaces: SessionKind[]): Skill[] {
  return ids
    .map((id) => skills.find((s) => s.id === id))
    .filter((s): s is Skill => !!s && s.kinds.length > 0 && !s.kinds.some((k) => surfaces.includes(k)))
}

/** Surface a new conversation with this agent opens on: bound surface, else the current one if offered, else the first. */
export function agentStartKind(agent: Pick<Agent, 'kinds' | 'surface'>, current?: SessionKind): SessionKind {
  const offered = agentKinds(agent)
  if (agent.surface && offered.includes(agent.surface)) return agent.surface
  if (current && offered.includes(current)) return current
  return offered[0] ?? 'chat'
}

/** A copy and its catalogue original fold into one row (the copy kept); self-written agents never fold. */
export function distinctAgents<T extends Pick<Agent, 'name' | 'ownerId' | 'catalogKey' | 'official'>>(
  agents: T[],
  me?: string | null,
): T[] {
  const authored = (a: T) => !!me && a.ownerId === me && !a.catalogKey && !a.official
  const rank = (a: T) => (me && a.ownerId === me ? 2 : 0) + (a.catalogKey || a.official ? 0 : 1)
  const key = (a: T) => a.name.trim().toLowerCase()
  const best = new Map<string, T>()
  for (const a of agents) {
    const held = best.get(key(a))
    if (!held || rank(a) > rank(held)) best.set(key(a), a)
  }
  return agents.filter((a) => {
    const kept = best.get(key(a))
    return kept === a || (authored(a) && kept !== undefined && authored(kept))
  })
}

/** Icons a project can wear. Shared so the create and edit forms agree. */
export const PROJECT_EMOJIS = ['🧪', '📚', '🛠️', '📊', '🚀', '🧠', '🗂️', '🔬'] as const
