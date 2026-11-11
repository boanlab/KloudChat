import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  RequestTimeoutError,
  auth,
  modelsApi,
  setAccessToken,
  setSessionRenewer,
} from '@/lib/api'

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' }, ...init })

/** The `Authorization` header a mocked `fetch` call carried. */
const bearer = (call: unknown[]) =>
  ((call[1] as RequestInit).headers as Record<string, string>).Authorization

beforeEach(() => {
  vi.stubGlobal('document', { documentElement: { lang: 'ko' } })
  setAccessToken('old', 3600)
  setSessionRenewer(null)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('call', () => {
  it('renews the token on a 401 and sends the request once more with the new one', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(json({ detail: 'token_expired' }, { status: 401 }))
      .mockResolvedValueOnce(json({ id: 'u1' }))
    vi.stubGlobal('fetch', fetch)
    const renew = vi.fn(async () => {
      setAccessToken('new', 3600)
      return true
    })
    setSessionRenewer(renew)
    await expect(auth.me()).resolves.toEqual({ id: 'u1' })
    expect(renew).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls.map(bearer)).toEqual(['Bearer old', 'Bearer new'])
  })

  it('renews a lapsed token before sending, not after a refusal', async () => {
    setAccessToken('old', -1)
    const fetch = vi.fn().mockResolvedValue(json({ id: 'u1' }))
    vi.stubGlobal('fetch', fetch)
    setSessionRenewer(async () => {
      setAccessToken('new', 3600)
      return true
    })
    await auth.me()
    expect(fetch.mock.calls.map(bearer)).toEqual(['Bearer new'])
  })

  it('does not renew for a refused sign-in', async () => {
    const refused = json({ detail: 'invalid_credentials' }, { status: 401 })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(refused))
    const renew = vi.fn(async () => true)
    setSessionRenewer(renew)
    await expect(auth.login('a@example.test', 'wrong')).rejects.toBeInstanceOf(ApiError)
    expect(renew).not.toHaveBeenCalled()
  })

  it('carries Retry-After on the error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      json({ detail: 'account_locked' }, { status: 429, headers: { 'Retry-After': '300' } })))
    const err = await auth.login('a@example.test', 'pw').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).retryAfter).toBe(300)
  })

  it('stops a read that gets no answer and says so', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
    const pending = modelsApi.list().catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(60_000)
    const err = await pending
    expect(err).toBeInstanceOf(RequestTimeoutError)
    expect((err as ApiError).detail).toBe('응답이 늦어 요청을 멈췄습니다. 잠시 뒤 다시 시도하세요.')
  })

  it('sends a read once more when the network dropped it, a write never', async () => {
    const read = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(json({ models: [] }))
    vi.stubGlobal('fetch', read)
    await expect(modelsApi.list()).resolves.toEqual({ models: [] })
    expect(read).toHaveBeenCalledTimes(2)

    const write = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', write)
    await expect(auth.updateMe({ name: 'x' })).rejects.toBeInstanceOf(TypeError)
    expect(write).toHaveBeenCalledTimes(1)
  })
})

describe('auth.refresh', () => {
  it('presents the refresh cookie once for callers that ask together', async () => {
    const fetch = vi.fn().mockResolvedValue(json({ accessToken: 't', expiresIn: 900, user: {} }))
    vi.stubGlobal('fetch', fetch)
    const [a, b] = await Promise.all([auth.refresh(), auth.refresh()])
    expect(a).toBe(b)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
