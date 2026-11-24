/** Opens the microphone for a voice conversation and cuts it into utterances by loudness. */
import { Vad } from './vad'
import { encodeWav } from './wavRecorder'

const SAMPLE_RATE = 16_000
const FRAME = 2048
/** Sound kept from before the detector decided speech had begun. */
const PRE_ROLL_FRAMES = 3
/** Under this much speech (a cough, a key tap) is dropped. */
const MIN_UTTERANCE_MS = 400

export interface VoiceListener {
  /** Ends the microphone and everything attached to it. */
  stop: () => Promise<void>
  /** The held button: speech starts now, regardless of loudness. */
  holdStart: () => void
  /** The held button is up: what was held becomes one utterance. */
  holdEnd: () => void
}

export async function startVoiceListener(handlers: {
  /** 0–1 loudness for a meter, every frame. */
  onLevel?: (level: number) => void
  /** A person began speaking. */
  onSpeechStart: () => void
  /** One finished utterance as 16 kHz mono WAV. */
  onUtterance: (wav: Blob) => void
  /** Louder bar while this app is playing sound, so it does not hear itself. */
  bar?: () => number
  /** The microphone was unplugged, switched or taken by another app. */
  onEnded?: () => void
}): Promise<VoiceListener> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  })
  const context = new AudioContext({ sampleRate: SAMPLE_RATE })
  // A browser that holds sound until a gesture starts the context suspended.
  if (context.state === 'suspended') void context.resume().catch(() => undefined)
  stream.getAudioTracks()[0]?.addEventListener('ended', () => handlers.onEnded?.())
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(FRAME, 1, 1)
  const vad = new Vad()
  const rate = context.sampleRate
  const frameMs = (FRAME / rate) * 1000
  let preRoll: Float32Array[] = []
  let kept: Float32Array[] = []
  let held = false
  let heldMs = 0

  const finish = (ms: number) => {
    const frames = kept
    kept = []
    vad.reset()
    if (ms >= MIN_UTTERANCE_MS && frames.length) handlers.onUtterance(encodeWav(frames, rate))
  }

  processor.onaudioprocess = (event) => {
    const frame = new Float32Array(event.inputBuffer.getChannelData(0))
    let sum = 0
    for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i]
    const rms = Math.sqrt(sum / frame.length)
    handlers.onLevel?.(Math.min(1, rms * 8))
    if (held) {
      kept.push(frame)
      heldMs += frameMs
      return
    }
    const wasSpeaking = vad.speaking
    const verdict = vad.feed(rms, frameMs, handlers.bar?.() ?? 1)
    if (verdict === 'start') {
      kept = [...preRoll, frame]
      handlers.onSpeechStart()
    } else if (wasSpeaking) {
      kept.push(frame)
      if (verdict === 'end') finish(kept.length * frameMs)
    }
    preRoll.push(frame)
    if (preRoll.length > PRE_ROLL_FRAMES) preRoll = preRoll.slice(-PRE_ROLL_FRAMES)
  }
  source.connect(processor)
  processor.connect(context.destination)

  return {
    stop: async () => {
      processor.onaudioprocess = null
      processor.disconnect()
      source.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      await context.close().catch(() => undefined)
    },
    holdStart: () => {
      if (held) return
      held = true
      heldMs = 0
      kept = [...preRoll]
      vad.reset()
      handlers.onSpeechStart()
    },
    holdEnd: () => {
      if (!held) return
      held = false
      finish(heldMs)
    },
  }
}
