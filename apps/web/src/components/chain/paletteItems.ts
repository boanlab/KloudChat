import { Bot, Calculator, Download, GitFork, MessageCircleQuestion, Play, ScanSearch, ShieldCheck, Sparkles, type LucideIcon } from 'lucide-react'
import { SELF } from '@/lib/process'
import { agentOutput } from '@/lib/userPipelines'
import type { Agent, PipelineStep, Skill } from '@/types'
import type { ConditionStep, GraphCatalogue } from '@/types/pipelineGraph'

/** What a palette item adds: a step, or (a skill) an agent draft made from it. */
export type PaletteMake =
  | { step: Omit<PipelineStep, 'id'> | Omit<ConditionStep, 'id'> }
  | { skill: string }
  | { start: true }

export interface PaletteItem {
  key: string
  name: string
  icon: LucideIcon
  tone: string
  make: PaletteMake
}

export const newCondition = (): Omit<ConditionStep, 'id'> => ({
  kind: 'condition',
  name: '',
  mode: 'rule',
  rule: { step: '', value: '', op: 'gt', target: '' },
  branches: [
    { key: 'yes', label: '예' },
    { key: 'no', label: '아니오' },
  ],
})

/** Every item, grouped as the palette shows them. */
export function paletteGroups(self: Agent, agents: Agent[], skills: Skill[], cat: GraphCatalogue | null, me: string | undefined) {
  const runnable = agents.filter((a) => a.id !== self.id && agentOutput(a) && (a.ownerId === me || a.visibility === 'org'))
  const step = (s: Omit<PipelineStep, 'id'>) => ({ step: s })
  return [
    {
      label: '시작',
      items: [{ key: 'start', name: '대화·첨부로 시작', icon: Play, tone: 'text-success', make: { start: true } as const }],
    },
    {
      label: '내 에이전트',
      items: [
        { key: 'self', name: self.name || '이 에이전트', icon: Bot, tone: 'text-accent', make: step({ kind: 'agent', ref: SELF, settings: {} }) },
        ...runnable.map((a) => ({ key: `agent:${a.id}`, name: a.name, icon: Bot, tone: 'text-accent', make: step({ kind: 'agent', ref: a.id, settings: {} }) })),
      ],
    },
    {
      label: '스킬',
      items: skills
        .filter((s) => s.enabled)
        .map((s) => ({ key: `skill:${s.id}`, name: s.name, icon: Sparkles, tone: 'text-accent', make: { skill: s.id } })),
      store: true,
    },
    {
      label: '확인·내보내기',
      items: [
        ...(cat?.checks ?? []).map((c) => ({ key: `check:${c.ref}`, name: c.name, icon: ShieldCheck, tone: 'text-success', make: step({ kind: 'check', ref: c.ref, settings: {} }) })),
        ...(cat?.human ?? []).map((c) => ({ key: `human:${c.ref}`, name: c.name, icon: MessageCircleQuestion, tone: 'text-warn', make: step({ kind: 'human', ref: c.ref, settings: {} }) })),
        { key: 'review', name: '검토', icon: ScanSearch, tone: 'text-success', make: step({ kind: 'review', ref: '', settings: { model: '', repair: false } }) },
        { key: 'export', name: 'PDF·Word 내보내기', icon: Download, tone: 'text-accent', make: step({ kind: 'export', ref: '', settings: { formats: ['docx'] } }) },
      ],
    },
    {
      label: '계산',
      items: (cat?.skills ?? []).map((c) => ({ key: `calc:${c.ref}`, name: c.name, icon: Calculator, tone: 'text-accent', make: step({ kind: 'skill', ref: c.ref, settings: {} }) })),
    },
    {
      label: '조건',
      items: [{ key: 'condition', name: '조건', icon: GitFork, tone: 'text-warn', make: { step: newCondition() } }],
    },
  ] as { label: string; items: PaletteItem[]; store?: boolean }[]
}

