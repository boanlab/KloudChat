/** Microphone as 16 kHz mono WAV: Whisper rejects WebM/MP4 and the API has no ffmpeg. */

const SAMPLE_RATE = 16_000

/** Longest dictation: 16 kHz 16-bit mono is 32,000 bytes a second, so this stays far under the upload limit. */
export const DICTATION_MAX_MS = 120_000

export interface WavRecorder {
  /** Stops the microphone and returns the recording as a WAV blob. */
  stop: () => Promise<Blob>
  /** Stops the microphone and throws the recording away (the page is being left). */
  cancel: () => void
}

/** Why a microphone could not be opened: refused by the person or the browser, not there, or something else. */
export function micFailure(error: unknown): 'denied' | 'missing' | 'failed' {
  const name = error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'denied'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'missing'
  return 'failed'
}

export async function startWavRecording(
  opts: { maxMs?: number; onLimit?: () => void } = {},
): Promise<WavRecorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  })
  let context: AudioContext
  let source: MediaStreamAudioSourceNode
  let processor: ScriptProcessorNode
  try {
    // A browser that cannot open 16 kHz reports its own rate, used in the header.
    context = new AudioContext({ sampleRate: SAMPLE_RATE })
    source = context.createMediaStreamSource(stream)
    // ScriptProcessor needs no separate worklet module file.
    processor = context.createScriptProcessor(4096, 1, 1)
  } catch (error) {
    // Opened but unusable: close the microphone, or its light stays on.
    stream.getTracks().forEach((track) => track.stop())
    throw error
  }
  const chunks: Float32Array[] = []
  let ended = false
  let limit: ReturnType<typeof setTimeout> | null = null
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
  }
  source.connect(processor)
  processor.connect(context.destination)

  const release = async () => {
    if (ended) return
    ended = true
    if (limit) clearTimeout(limit)
    processor.onaudioprocess = null
    processor.disconnect()
    source.disconnect()
    stream.getTracks().forEach((track) => track.stop())
    await context.close().catch(() => undefined)
  }
  if (opts.maxMs) limit = setTimeout(() => opts.onLimit?.(), opts.maxMs)

  return {
    stop: async () => {
      const rate = context.sampleRate
      await release()
      return encodeWav(chunks, rate)
    },
    cancel: () => void release(),
  }
}

export function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
  const length = chunks.reduce((n, c) => n + c.length, 0)
  const buffer = new ArrayBuffer(44 + length * 2)
  const view = new DataView(buffer)
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  write(0, 'RIFF')
  view.setUint32(4, 36 + length * 2, true)
  write(8, 'WAVE')
  write(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  write(36, 'data')
  view.setUint32(40, length * 2, true)
  let offset = 44
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[i]))
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      offset += 2
    }
  }
  return new Blob([buffer], { type: 'audio/wav' })
}
