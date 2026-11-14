import { describe, expect, it } from 'vitest'
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
  it('takes the start as usable until the catalogue answers', () => {
    expect(startingMode({ kind: 'chat', superAgent: { default: unusable, strict: unusable, pending: true } })).toBe('super')
  })
})
