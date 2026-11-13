/**
 * The composer's context gauge: how much of the model's window the next turn takes.
 * The server counts the conversation (`POST /sessions/context-usage`, `routers/context_usage.py`);
 * the unsent draft is counted here as it is typed, with the server's estimator.
 */

/** Breakdown parts, in the order the popover lists them. */
export const CONTEXT_PARTS = ['system', 'instructions', 'memory', 'files', 'history', 'draft', 'tools'] as const
export type ContextPart = (typeof CONTEXT_PARTS)[number]

export interface ContextQuery {
  sessionId?: string | null
  kind?: 'chat' | 'report' | 'slides'
  projectId?: string | null
  agentId?: string | null
  model?: string | null
  draft?: string
  attachments?: string[]
}

export interface ContextUsage {
  model: string
  /** The model's own window. */
  statedWindow: number
  /** The window the conversation is fitted to, in estimate units (smaller for an external tokenizer). */
  window: number
  foreignTokenizer: boolean
  /** Kept free for the answer: past `window - reserve` older turns are summarised. */
  reserve: number
  used: number
  parts: Record<ContextPart, number>
  summarised: boolean
  summarisedMessages: number
  /** History tokens that still fit before the next cut. */
  headroom: number
  lastTurn: { inputTokens: number; model?: string | null } | null
  /** The model is chosen per turn (Auto); the window is the last answering model's or the default's. */
  routed: boolean
  routedBasis: 'last' | 'default' | null
}

// `context.estimate_tokens`: deliberately high, CJK 0.7 and the rest 0.3 tokens a character.
const CJK = /[ᄀ-ᇿ　-鿿가-힯豈-﫿＀-￯]/g
const TOKENS_PER_MESSAGE = 4

export function estimateTokens(text: string): number {
  if (!text) return 0
  const cjk = text.match(CJK)?.length ?? 0
  return Math.floor(cjk * 0.7 + (text.length - cjk) * 0.3) + 1
}

/** `usage` with `draft` as the next question in place of the one it was counted with. */
export function withDraft(usage: ContextUsage, draft: string): ContextUsage {
  const counted = draft.trim() ? estimateTokens(draft) + TOKENS_PER_MESSAGE : 0
  const used = Math.max(0, usage.used - usage.parts.draft + counted)
  return { ...usage, used, parts: { ...usage.parts, draft: counted } }
}

/** Where older turns start to be summarised: the window less the answer's reserve. */
export function summaryLimit(usage: Pick<ContextUsage, 'window' | 'reserve'>): number {
  return Math.max(0, usage.window - usage.reserve)
}

export type GaugeLevel = 'ok' | 'near' | 'full'

/** Share of the summary limit from which the gauge warns. */
export const NEAR_SHARE = 0.8

/** `full`: this turn cuts older messages; `near`: close to it, or already running on a summary. */
export function gaugeLevel(usage: Pick<ContextUsage, 'used' | 'window' | 'reserve' | 'summarised'>): GaugeLevel {
  const limit = summaryLimit(usage)
  if (limit <= 0 || usage.used >= limit) return 'full'
  if (usage.summarised || usage.used >= limit * NEAR_SHARE) return 'near'
  return 'ok'
}

/** Whole percent of the window; anything in use shows at least 1. */
export function gaugePercent(used: number, window: number): number {
  if (window <= 0) return 100
  if (used <= 0) return 0
  return Math.min(100, Math.max(1, Math.round((used / window) * 100)))
}

/** 「54k」, 「131k」, 「850」: whole thousands, one decimal under ten thousand. */
export function compactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 10_000) return `${Math.round(n / 1_000)}k`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  return String(n)
}
