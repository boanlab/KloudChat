/**
 * The length of a local audio/video file, read from its container header in the page (no media
 * element, nothing played): MP4/MOV/M4A from the movie header (`moov` › `mvhd`), WebM/Matroska
 * from the segment's `Info` (`TimecodeScale`, `Duration`). Anything else, or a header that does
 * not say (a live recording without a duration), is undefined: the server probes it after upload.
 */

/** A part of the file read as bytes. */
export type ReadRange = (start: number, end: number) => Promise<Uint8Array>

const HEAD_BYTES = 1 << 20
/** `moov` at the end of a file is read whole up to this size. */
const MOOV_MOST = 8 << 20

export async function mediaDuration(file: Blob): Promise<number | undefined> {
  const read: ReadRange = async (start, end) =>
    new Uint8Array(await file.slice(start, Math.min(end, file.size)).arrayBuffer())
  try {
    return await durationOf(read, file.size)
  } catch {
    return undefined
  }
}

/** The duration in seconds from a reader over a file of `size` bytes, or undefined. */
export async function durationOf(read: ReadRange, size: number): Promise<number | undefined> {
  const head = await read(0, Math.min(size, HEAD_BYTES))
  if (head.length >= 4 && head[0] === 0x1a && head[1] === 0x45 && head[2] === 0xdf && head[3] === 0xa3) {
    return sane(matroskaDuration(head))
  }
  if (head.length >= 8 && ascii(head, 4, 8) === 'ftyp') return sane(await mp4Duration(read, size, head))
  return undefined
}

function sane(seconds: number | undefined): number | undefined {
  return seconds !== undefined && Number.isFinite(seconds) && seconds > 0 && seconds < 30 * 24 * 3600
    ? seconds
    : undefined
}

function ascii(bytes: Uint8Array, start: number, end: number): string {
  let out = ''
  for (let i = start; i < end; i++) out += String.fromCharCode(bytes[i])
  return out
}

function view(bytes: Uint8Array): DataView {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
}

// ── MP4 / MOV ──

/** Top-level boxes walked by their headers; `moov` read and its `mvhd` taken. */
async function mp4Duration(read: ReadRange, size: number, head: Uint8Array): Promise<number | undefined> {
  let at = 0
  for (let boxes = 0; at + 8 <= size && boxes < 64; boxes++) {
    const header = at + 16 <= head.length ? head.subarray(at, at + 16) : await read(at, at + 16)
    if (header.length < 8) return undefined
    const data = view(header)
    let length = data.getUint32(0)
    const type = ascii(header, 4, 8)
    let skip = 8
    if (length === 1) {
      if (header.length < 16) return undefined
      length = Number(data.getBigUint64(8))
      skip = 16
    } else if (length === 0) {
      length = size - at
    }
    if (length < skip) return undefined
    if (type === 'moov') {
      if (length > MOOV_MOST) return undefined
      const box = at + length <= head.length ? head.subarray(at, at + length) : await read(at, at + length)
      return mvhd(box.subarray(skip))
    }
    at += length
  }
  return undefined
}

/** `mvhd` among the children of `moov`: duration / timescale. */
function mvhd(moov: Uint8Array): number | undefined {
  let at = 0
  while (at + 8 <= moov.length) {
    const data = view(moov.subarray(at))
    const length = data.getUint32(0)
    if (length < 8 || at + length > moov.length) return undefined
    if (ascii(moov, at + 4, at + 8) === 'mvhd') {
      const version = moov[at + 8]
      if (version === 1) {
        if (length < 8 + 4 + 16 + 4 + 8) return undefined
        const scale = data.getUint32(28)
        const duration = Number(data.getBigUint64(32))
        return scale ? duration / scale : undefined
      }
      if (length < 8 + 4 + 8 + 4 + 4) return undefined
      const scale = data.getUint32(20)
      const duration = data.getUint32(24)
      return scale && duration !== 0xffffffff ? duration / scale : undefined
    }
    at += length
  }
  return undefined
}

// ── WebM / Matroska ──

const EBML = 0x1a45dfa3
const SEGMENT = 0x18538067
const INFO = 0x1549a966
const TIMECODE_SCALE = 0x2ad7b1
const DURATION = 0x4489
/** Level-1 elements read past while looking for `Info`. */
const CLUSTER = 0x1f43b675

/** An element id (its marker bits kept) and where its size starts. */
function readId(bytes: Uint8Array, at: number): [number, number] | null {
  const first = bytes[at]
  if (first === undefined) return null
  let width = 1
  while (width <= 4 && !(first & (0x80 >> (width - 1)))) width++
  if (width > 4 || at + width > bytes.length) return null
  let id = 0
  for (let i = 0; i < width; i++) id = id * 256 + bytes[at + i]
  return [id, at + width]
}

/** A size (marker bit dropped; -1 for "unknown") and where the data starts. */
function readSize(bytes: Uint8Array, at: number): [number, number] | null {
  const first = bytes[at]
  if (first === undefined) return null
  let width = 1
  while (width <= 8 && !(first & (0x80 >> (width - 1)))) width++
  if (width > 8 || at + width > bytes.length) return null
  let value = first & (0xff >> width)
  let unknown = value === (0xff >> width)
  for (let i = 1; i < width; i++) {
    value = value * 256 + bytes[at + i]
    if (bytes[at + i] !== 0xff) unknown = false
  }
  return [unknown ? -1 : value, at + width]
}

function matroskaDuration(bytes: Uint8Array): number | undefined {
  let at = 0
  // the EBML header, then the segment
  for (let guard = 0; guard < 8 && at < bytes.length; guard++) {
    const id = readId(bytes, at)
    const size = id && readSize(bytes, id[1])
    if (!id || !size) return undefined
    const [element] = id
    const [length, data] = size
    if (element === EBML) {
      if (length < 0) return undefined
      at = data + length
      continue
    }
    if (element === SEGMENT) return segmentInfo(bytes, data, length < 0 ? bytes.length : Math.min(bytes.length, data + length))
    if (length < 0) return undefined
    at = data + length
  }
  return undefined
}

function segmentInfo(bytes: Uint8Array, start: number, end: number): number | undefined {
  let at = start
  for (let guard = 0; guard < 256 && at < end; guard++) {
    const id = readId(bytes, at)
    const size = id && readSize(bytes, id[1])
    if (!id || !size) return undefined
    const [element] = id
    const [length, data] = size
    if (element === INFO) return info(bytes, data, length < 0 ? end : Math.min(end, data + length))
    if (element === CLUSTER || length < 0) return undefined
    at = data + length
  }
  return undefined
}

function info(bytes: Uint8Array, start: number, end: number): number | undefined {
  let scale = 1_000_000
  let duration: number | undefined
  let at = start
  for (let guard = 0; guard < 64 && at < end; guard++) {
    const id = readId(bytes, at)
    const size = id && readSize(bytes, id[1])
    if (!id || !size) break
    const [element] = id
    const [length, data] = size
    if (length < 0 || data + length > bytes.length) break
    if (element === TIMECODE_SCALE && length >= 1 && length <= 8) {
      let value = 0
      for (let i = 0; i < length; i++) value = value * 256 + bytes[data + i]
      scale = value
    } else if (element === DURATION && (length === 4 || length === 8)) {
      const data_ = view(bytes.subarray(data, data + length))
      duration = length === 4 ? data_.getFloat32(0) : data_.getFloat64(0)
    }
    at = data + length
  }
  return duration === undefined ? undefined : (duration * scale) / 1e9
}
