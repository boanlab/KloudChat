import { describe, expect, it } from 'vitest'
import { withAccountPicks } from './modelPicks'
import { startingMode } from './superAgent'

const usable = { available: true, reason: null, estimate: null }
const unusable = { available: false, reason: 'no_models' as const, estimate: null }
const offered = { default: usable, strict: usable }

describe('startingMode', () => {
  it('starts chat, report and slides on Super Agent by default', () => {
    for (const kind of ['chat', 'report', 'slides'] as const) {
      expect(startingMode({ kind, superAgent: offered })).toBe('super')
    }
  })
  it('keeps other surfaces, an agent with its own model and a home-screen pick on their model', () => {
    expect(startingMode({ kind: 'image', superAgent: offered })).toBe('manual')
    expect(startingMode({ kind: 'chat', agentModel: 'local/qwen3.8-27b', superAgent: offered })).toBe('manual')
    expect(startingMode({ kind: 'chat', agentModel: '', superAgent: offered })).toBe('super')
    expect(startingMode({ kind: 'chat', homePick: 'local/qwen3.8-27b', superAgent: offered })).toBe('manual')
  })
  it('follows the admin start per surface', () => {
    const defaultRouting = { chat: 'super_strict', report: 'manual', slides: 'super' } as const
    expect(startingMode({ kind: 'chat', superAgent: offered, defaultRouting })).toBe('super_strict')
    expect(startingMode({ kind: 'report', superAgent: offered, defaultRouting })).toBe('manual')
    expect(startingMode({ kind: 'slides', superAgent: offered, defaultRouting })).toBe('super')
  })
  it('falls back when the allowed models leave an entry nothing, never widening strict-local', () => {
    expect(startingMode({ kind: 'chat', superAgent: { default: unusable, strict: usable } })).toBe('super_strict')
    expect(startingMode({ kind: 'chat', superAgent: { default: unusable, strict: unusable } })).toBe('manual')
    expect(startingMode({
      kind: 'chat', superAgent: { default: usable, strict: unusable }, defaultRouting: { chat: 'super_strict' },
    })).toBe('manual')
  })
  it('starts on the entry the person picked for the surface, over the admin default', () => {
    const defaultRouting = { chat: 'manual' } as const
    expect(startingMode({ kind: 'chat', homePick: 'super_strict', superAgent: offered, defaultRouting })).toBe('super_strict')
    expect(startingMode({ kind: 'chat', homePick: 'super', superAgent: offered, defaultRouting })).toBe('super')
    // a strict-local pick never widens to the external entry
    expect(startingMode({ kind: 'chat', homePick: 'super_strict', superAgent: { default: usable, strict: unusable } })).toBe('manual')
    expect(startingMode({ kind: 'chat', homePick: 'super', superAgent: { default: unusable, strict: usable } })).toBe('super_strict')
  })
  it('takes the start as usable until the catalogue answers', () => {
    expect(startingMode({ kind: 'chat', superAgent: { default: unusable, strict: unusable, pending: true } })).toBe('super')
  })
})

describe('withAccountPicks', () => {
  it('shows the account pick that is served for the surface, not the browser default', () => {
    const current = { chat: 'local/flash', report: 'local/27b', slides: 'local/27b', image: '', av: '' }
    const models = [{ id: 'local/flash', kinds: ['chat'] }, { id: 'local/27b', kinds: ['chat', 'report'] }] as const
    const next = withAccountPicks(current, models.map((m) => ({ ...m, kinds: [...m.kinds] })) as never, { chat: 'local/27b', slides: 'gone/model' })
    expect(next.chat).toBe('local/27b')
    expect(next.slides).toBe('local/27b')
    expect(withAccountPicks(current, [], undefined)).toBe(current)
  })
})
