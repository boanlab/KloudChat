import { uid } from '@/lib/utils'
import type { Agent, Skill, SkillServer } from '@/types'

/** Blank agent; `model` '' follows the surface's default model. */
export function emptyAgent(model: string): Agent {
  return {
    id: uid('ag'),
    name: '',
    slug: '',
    description: '',
    model,
    systemPrompt: '',
    guide: '',
    contact: '',
    starters: [],
    shareMode: 'open',
    sealed: false,
    tools: null,
    skillIds: null,
    activeSkillIds: [],
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
    outputLanguage: 'auto',
    webSearchDefault: 'auto',
    useMemory: true,
    color: '#5b53e8',
    enabled: true,
    runs: 0,
    hasKnowledge: false,
    updatedAt: new Date().toISOString(),
  }
}

/** Server's slug rule mirrored for preview and early collision checks; server decides. */
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

/** Skill requiring nothing from the server. */
export const blankServer = (): SkillServer => ({
  stage: '',
  entry: '',
  postCheck: [],
  postJudge: false,
  copyCheck: false,
  copyKinds: [],
  tables: false,
  ownStage: false,
  uses: [],
  template: '',
  checks: [],
  checkOptions: [],
  questions: [],
  process: {},
})
