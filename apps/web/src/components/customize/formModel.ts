import { uid } from '@/lib/utils'
import type { Agent, Skill } from '@/types'

/** A blank agent on the cheapest chat model, the rule the surface defaults use. */
export function emptyAgent(model: string): Agent {
  return {
    id: uid('ag'),
    name: '',
    slug: '',
    description: '',
    model,
    systemPrompt: '',
    guide: '',
    starters: [],
    shareMode: 'open',
    sealed: false,
    tools: null,
    skillIds: null,
    kinds: ['chat'],
    surface: null,
    renderTemplateId: null,
    docFormat: null,
    designSystemId: null,
    visibility: 'private',
    ownerId: '',
    ownerName: '',
    installs: 0,
    catalogKey: null,
    originId: null,
    official: false,
    installed: false,
    temperature: 0.5,
    color: '#5b53e8',
    enabled: true,
    runs: 0,
    hasKnowledge: false,
    updatedAt: new Date().toISOString(),
  }
}

/** Mirrors the server's slug rule for preview and early collision checks; the server still decides. */
export function slugify(text: string): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_]+/gu, '-')
    .replace(/^-+|-+$/g, '')
  return base.slice(0, 60) || 'item'
}

export interface SkillDraft {
  name: string
  description: string
  whenToUse: string
  body: string
  requiredTools: string[]
  visibility: Skill['visibility']
  enabled: boolean
}

export const blankSkill = (): SkillDraft => ({
  name: '',
  description: '',
  whenToUse: '',
  body: '',
  requiredTools: [],
  visibility: 'private',
  enabled: true,
})
