import { describe, expect, it } from 'vitest'
import { startingMode } from './superAgent'

const offered = { enabled: true, default: { available: true, reason: null, estimate: null } }
const notOffered = { enabled: true, default: { available: false, reason: 'no_models' as const, estimate: null } }

describe('startingMode', () => {
  it('starts chat, report and slides on Super Agent when it is offered', () => {
    for (const kind of ['chat', 'report', 'slides'] as const) {
      expect(startingMode({ kind, superAgent: offered })).toBe('super')
    }
  })
  it('keeps other surfaces, agents and a home-screen pick on their model', () => {
    expect(startingMode({ kind: 'image', superAgent: offered })).toBe('manual')
    expect(startingMode({ kind: 'chat', agentId: 'a1', superAgent: offered })).toBe('manual')
    expect(startingMode({ kind: 'chat', homePick: 'local/qwen3.8-27b', superAgent: offered })).toBe('manual')
  })
  it('starts manual when the admin turned it off or nothing this person may use fits', () => {
    expect(startingMode({ kind: 'chat', superAgent: { ...offered, enabled: false } })).toBe('manual')
    expect(startingMode({ kind: 'chat', superAgent: notOffered })).toBe('manual')
  })
})
