import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { durationOf, type ReadRange } from './mediaDuration'

const reader = (bytes: Uint8Array): ReadRange => async (start, end) => bytes.subarray(start, Math.min(end, bytes.length))
const of = (bytes: Uint8Array) => durationOf(reader(bytes), bytes.length)

function box(type: string, body: Uint8Array): Uint8Array {
  const out = new Uint8Array(8 + body.length)
  new DataView(out.buffer).setUint32(0, out.length)
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i)
  out.set(body, 8)
  return out
}
const join = (...parts: Uint8Array[]) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const part of parts) {
    out.set(part, at)
    at += part.length
  }
  return out
}
function mvhd(version: 0 | 1, scale: number, duration: number): Uint8Array {
  const body = new Uint8Array(version ? 4 + 16 + 4 + 8 + 80 : 4 + 8 + 4 + 4 + 80)
  const data = new DataView(body.buffer)
  body[0] = version
  if (version) {
    data.setUint32(20, scale)
    data.setBigUint64(24, BigInt(duration))
  } else {
    data.setUint32(12, scale)
    data.setUint32(16, duration)
  }
  return box('mvhd', body)
}
const ftyp = box('ftyp', new TextEncoder().encode('isom\0\0\0\0isomiso2'))

describe('MP4 / MOV duration from the movie header', () => {
  it('reads mvhd version 0 and 1, with moov first or after a large mdat', async () => {
    expect(await of(join(ftyp, box('moov', mvhd(0, 1000, 754_500))))).toBeCloseTo(754.5)
    expect(await of(join(ftyp, box('moov', mvhd(1, 90_000, 90_000 * 3600))))).toBeCloseTo(3600)
    // moov at the end, past the first megabyte the head read covers
    const media = box('mdat', new Uint8Array(3 << 20))
    expect(await of(join(ftyp, media, box('moov', join(box('trak', new Uint8Array(16)), mvhd(0, 600, 600 * 125)))))).toBeCloseTo(125)
  })

  it('gives up on a broken or missing header', async () => {
    expect(await of(join(ftyp, box('mdat', new Uint8Array(64))))).toBeUndefined()
    expect(await of(join(ftyp, box('moov', mvhd(0, 0, 100))))).toBeUndefined()
    expect(await of(new TextEncoder().encode('RIFF....WAVEfmt '))).toBeUndefined()
    expect(await of(new Uint8Array(0))).toBeUndefined()
  })
})

describe('WebM / Matroska duration from the segment info', () => {
  const ebmlHeader = Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3, 0x84, 0x42, 0x82, 0x81, 0x77])
  const durationEl = (seconds: number) => {
    const out = new Uint8Array(3 + 8)
    out.set([0x44, 0x89, 0x88])
    new DataView(out.buffer).setFloat64(3, seconds * 1000)
    return out
  }
  const scaleEl = Uint8Array.from([0x2a, 0xd7, 0xb1, 0x83, 0x0f, 0x42, 0x40])
  const info = (body: Uint8Array) => join(Uint8Array.from([0x15, 0x49, 0xa9, 0x66, 0x80 | body.length]), body)
  // a live-written segment: size "unknown"
  const segment = (body: Uint8Array) => join(Uint8Array.from([0x18, 0x53, 0x80, 0x67, 0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff]), body)

  it('reads Duration with the TimecodeScale', async () => {
    expect(await of(join(ebmlHeader, segment(info(join(scaleEl, durationEl(612.25))))))).toBeCloseTo(612.25)
  })

  it('a recording without a Duration is unknown', async () => {
    expect(await of(join(ebmlHeader, segment(info(scaleEl))))).toBeUndefined()
  })

  it('reads the thirteen-minute fixture the e2e tests upload', async () => {
    const bytes = new Uint8Array(readFileSync(new URL('../../e2e/fixtures/thirteen-minutes.webm', import.meta.url)))
    expect(await of(bytes)).toBeCloseTo(780, 0)
  })
})
