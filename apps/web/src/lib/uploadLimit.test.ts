import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, agentsApi, filesApi } from '@/lib/api'
import { attachRefusal } from '@/lib/attachable'
import { knowledgeRefusal } from '@/lib/knowledgeFiles'
import { overUploadLimit, setMaxUploadMb, tooLargeMessage } from '@/lib/uploadLimit'

const MB = 1024 * 1024
const t = (text: string) => text
const file = (size: number, name = 'big.pdf') => new File([new Uint8Array(size)], name)

beforeEach(() => {
  vi.stubGlobal('document', { documentElement: { lang: 'ko' } })
  setMaxUploadMb(200)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the upload limit', () => {
  it('is the server default until the config names another', () => {
    expect(tooLargeMessage()).toBe('파일이 너무 큽니다. 한 파일에 200MB까지 올릴 수 있습니다.')
    setMaxUploadMb(1)
    expect(overUploadLimit({ size: MB })).toBe(false)
    expect(overUploadLimit({ size: MB + 1 })).toBe(true)
    // An older server says nothing: the last known limit stays.
    setMaxUploadMb(undefined)
    expect(tooLargeMessage()).toContain('1MB')
  })

  it('refuses an over-limit file in the composer and the project shelf', () => {
    setMaxUploadMb(1)
    expect(attachRefusal(file(MB + 1), t)).toBe(tooLargeMessage())
    expect(attachRefusal(file(MB), t)).toBeNull()
    expect(knowledgeRefusal(file(MB + 1, 'a.pdf'), t)).toBe(`a.pdf: ${tooLargeMessage()}`)
  })

  it('never sends an over-limit file', async () => {
    setMaxUploadMb(1)
    const sent = vi.fn()
    vi.stubGlobal('fetch', sent)
    await expect(filesApi.upload(file(MB + 1))).rejects.toMatchObject({ status: 413, detail: tooLargeMessage() })
    await expect(agentsApi.knowledge.upload('a1', file(MB + 1))).rejects.toBeInstanceOf(ApiError)
    expect(sent).not.toHaveBeenCalled()
  })

  it('reads a proxy 413 page and the API code as the same sentence, keeping the request id', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>413</html>', {
      status: 413, headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'edge-1' },
    })))
    const proxied = await filesApi.upload(file(10)).catch((err) => err)
    expect(proxied.detail).toBe(tooLargeMessage())
    expect(proxied.requestId).toBe('edge-1')

    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'file_too_large_120mb' }, { status: 413 })))
    const refused = await filesApi.upload(file(10)).catch((err) => err)
    expect(refused.detail).toBe('파일이 너무 큽니다. 한 파일에 120MB까지 올릴 수 있습니다.')
    // No id on the answer: none carried over from the last failure.
    expect(refused.requestId).toBeUndefined()
  })

  it('leaves other 413 codes alone', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'file_too_large' }, { status: 413 })))
    const refused = await filesApi.upload(file(10)).catch((err) => err)
    expect(refused.detail).toBe('file_too_large')
  })
})
