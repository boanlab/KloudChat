import { describe, expect, it } from 'vitest'
import type { AuditRow } from '@/lib/api'
import { targetParts } from './auditLabels'

const row = (over: Partial<AuditRow>): AuditRow => ({
  id: 'x', at: '2026-11-16T09:00:00Z', actor: 'a@b.c', action: 'login', target: '', detail: '',
  ip: '', region: '', userAgent: '', severity: 'info', metadata: null, ...over,
} as AuditRow)

describe('targetParts', () => {
  it('leaves a target that only repeats the account empty', () => {
    expect(targetParts(row({ target: 'a@b.c' }))).toEqual([])
    expect(targetParts(row({ action: 'user.suspend', target: 'x@y.z' }))).toEqual(['x@y.z'])
  })
  it('names the picked model for a routing row, not the conversation hash', () => {
    const r = row({ action: 'routing.super', target: '294871d1de59bec5', detail: 'super-agent-1', metadata: { selectedModel: 'm/1' } })
    expect(targetParts(r, (id) => `Model ${id}`)).toEqual(['Model m/1'])
    expect(targetParts({ ...r, metadata: null })).toEqual([])
  })
})
