import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactsApi } from '@/lib/api'
import { leaveAllBlanks, leaveBlank, rememberBlank, sessionOfPath } from '@/lib/blankDocs'
import { blankSettled } from '@/lib/blankPending'
import { useStore } from '@/store/useStore'

// the real store needs a browser; the lists it holds are all `blankDocs` touches
vi.mock('@/store/useStore', () => {
  let state: Record<string, unknown> = {}
  return {
    useStore: {
      getState: () => state,
      setState: (next: unknown) => {
        state = { ...state, ...(typeof next === 'function' ? (next as (s: unknown) => object)(state) : (next as object)) }
      },
    },
  }
})

const row = (id: string) => ({ id, title: '새 PowerPoint 문서' }) as never

beforeEach(() => {
  vi.stubGlobal('document', { documentElement: { lang: 'ko' } })
  useStore.setState({
    sessions: [row('s1'), row('s2')],
    artifacts: [{ id: 'a1', sessionId: 's1' }, { id: 'a2', sessionId: 's2' }] as never,
    goneSessions: {},
    activeSessionId: 's1',
    projects: [],
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('sessionOfPath', () => {
  it('reads the conversation of a /s/:id address and nothing else', () => {
    expect(sessionOfPath('/s/abc')).toBe('abc')
    expect(sessionOfPath('/s/abc/')).toBe('abc')
    expect(sessionOfPath('/files')).toBeNull()
    expect(sessionOfPath('/')).toBeNull()
  })
})

describe('leaveBlank', () => {
  it('drops an untouched blank at once from every list, and asks the server once', async () => {
    const ask = vi.spyOn(artifactsApi, 'discardIfBlank').mockResolvedValue({ discarded: true })
    rememberBlank('s1', 'a1')
    leaveBlank('s1')
    expect(useStore.getState().sessions.map((c) => c.id)).toEqual(['s2'])
    expect(useStore.getState().artifacts.map((a) => a.id)).toEqual(['a2'])
    expect(useStore.getState().goneSessions.s1).toBe('deleted')
    await blankSettled()
    expect(ask).toHaveBeenCalledExactlyOnceWith('a1')
    // asked about once: leaving again asks nothing
    leaveBlank('s1')
    expect(ask).toHaveBeenCalledTimes(1)
  })

  it('puts them back when the server kept the document (it was touched)', async () => {
    vi.spyOn(artifactsApi, 'discardIfBlank').mockResolvedValue({ discarded: false })
    rememberBlank('s1', 'a1')
    leaveBlank('s1')
    await blankSettled()
    expect(useStore.getState().sessions.map((c) => c.id).sort()).toEqual(['s1', 's2'])
    expect(useStore.getState().artifacts.map((a) => a.id).sort()).toEqual(['a1', 'a2'])
    expect(useStore.getState().goneSessions.s1).toBeUndefined()
  })

  it('puts them back when the request fails', async () => {
    vi.spyOn(artifactsApi, 'discardIfBlank').mockRejectedValue(new Error('offline'))
    rememberBlank('s1', 'a1')
    leaveBlank('s1')
    await blankSettled()
    expect(useStore.getState().sessions.map((c) => c.id).sort()).toEqual(['s1', 's2'])
  })

  it('ignores a conversation that was not started blank', () => {
    const ask = vi.spyOn(artifactsApi, 'discardIfBlank')
    leaveBlank('s2')
    expect(ask).not.toHaveBeenCalled()
    expect(useStore.getState().sessions).toHaveLength(2)
  })

  it('as the page goes away it asks without redrawing the lists', async () => {
    const ask = vi.spyOn(artifactsApi, 'discardIfBlank').mockResolvedValue({ discarded: true })
    rememberBlank('s1', 'a1')
    rememberBlank('s2', 'a2')
    leaveAllBlanks()
    expect(ask).toHaveBeenCalledTimes(2)
    expect(useStore.getState().sessions).toHaveLength(2)
    await blankSettled()
  })
})
