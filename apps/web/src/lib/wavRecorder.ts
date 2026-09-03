/**
 * 마이크를 16 kHz 모노 WAV 로 녹음한다.
 *
 * `MediaRecorder` was the first version, and what it produces — WebM/Opus in
 * Chrome, MP4/AAC in Safari — is exactly what the deployment's Whisper (vLLM)
 * refuses: 「Invalid or unsupported audio file」. There is no ffmpeg in the API
 * image to transcode with, so the browser makes the file Whisper reads
 * natively: PCM samples off an `AudioContext` opened at 16 kHz, written into a
 * WAV header. Small enough for a minute of speech and the same bytes on every
 * browser.
 */

const SAMPLE_RATE = 16_000

export interface WavRecorder {
  /** Stops the microphone and returns the recording as a WAV blob. */
  stop: () => Promise<Blob>
}

export async function startWavRecording(): Promise<WavRecorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  })
  // Asked for 16 kHz; a browser that cannot open the context at that rate
  // reports its own, and the header below says whichever it was.
  const context = new AudioContext({ sampleRate: SAMPLE_RATE })
  const source = context.createMediaStreamSource(stream)
  // ScriptProcessorNode is deprecated in favour of AudioWorklet, but it needs
  // no separate module file and every browser still ships it.
  const processor = context.createScriptProcessor(4096, 1, 1)
  const chunks: Float32Array[] = []
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
  }
  source.connect(processor)
  processor.connect(context.destination)

  return {
    stop: async () => {
      processor.disconnect()
      source.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      const rate = context.sampleRate
      await context.close()
      return encodeWav(chunks, rate)
    },
  }
}

function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
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
