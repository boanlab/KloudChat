/** Where speech starts and ends in a stream of loudness readings; no audio in here. */

export type VadEvent = 'start' | 'end' | null

export interface VadOptions {
  /** Least loudness (RMS, 0–1) that can count as speech. */
  floor?: number
  /** Voiced time before speech counts as started. */
  startMs?: number
  /** Quiet time after speech that ends it. */
  endMs?: number
  /** Longest single utterance. */
  maxMs?: number
  /** Sound with no pause for this long is the room, not a person. */
  steadyMs?: number
}

/** Speech must be this many times the background to start. */
const START_RATIO = 3.5
/** It ends when it falls under this many times the background (lower than the start, so it holds). */
const END_RATIO = 2.2
/** The room's level is never taken above this, so a loud room cannot make speech undetectable. */
const NOISE_CAP = 0.05
/** An utterance whose loud frames average under this many times the background is not speech. */
const SPEECH_SNR = 3
/** Share of an utterance that must be loud for it to be speech. */
const SPEECH_SHARE = 0.2

export class Vad {
  private noise = 0.004
  private voiced = 0
  private quiet = 0
  private length = 0
  private loudMs = 0
  private loudSum = 0
  private paused = false
  private noiseAtStart = 0.004
  speaking = false
  /** The utterance that just ended is not speech: steady sound with no pause (a fan, a car), or too
   * faint against the room to be a voice. It is dropped. */
  noisy = false
  /** The last one ended because the room never let it end, not because anyone stopped talking. */
  steady = false

  private readonly opt: Required<VadOptions>

  constructor(opt: VadOptions = {}) {
    // A laptop microphone gives ordinary speech an RMS near 0.01–0.03.
    this.opt = { floor: 0.012, startMs: 240, endMs: 1100, maxMs: 20_000, steadyMs: 6000, ...opt }
  }

  /** Loudness needed now; `bar` raises it while the app itself is playing sound. */
  threshold(bar = 1): number {
    return Math.max(this.opt.floor, this.noise * START_RATIO) * bar
  }

  private endThreshold(bar: number): number {
    return Math.max(this.opt.floor * 0.7, this.noise * END_RATIO) * bar
  }

  /** One reading of `ms` milliseconds at loudness `rms`. */
  feed(rms: number, ms: number, bar = 1): VadEvent {
    if (!this.speaking) {
      const loud = rms > this.threshold(bar)
      // The background level follows quiet readings, and creeps up through loud ones (a room that
      // got noisier), the cap keeping a voice audible above it.
      // While this app plays sound the readings are its own echo, not the room.
      if (bar <= 1) this.noise += (Math.min(rms, NOISE_CAP) - this.noise) * (loud ? 0.01 : 0.05)
      this.voiced = loud ? this.voiced + ms : 0
      if (this.voiced >= this.opt.startMs) {
        this.speaking = true
        this.quiet = 0
        this.length = this.voiced
        this.loudMs = this.voiced
        this.loudSum = rms * this.voiced
        this.noiseAtStart = this.noise
        this.paused = false
        this.noisy = false
        this.steady = false
        return 'start'
      }
      return null
    }
    const loud = rms > this.endThreshold(bar)
    this.length += ms
    if (loud) {
      this.loudMs += ms
      this.loudSum += rms * ms
      this.quiet = 0
    } else {
      this.quiet += ms
      this.paused = true
    }
    if (this.quiet >= this.opt.endMs) return this.finish(false)
    if (!this.paused && this.length >= this.opt.steadyMs) return this.finish(true)
    if (this.length >= this.opt.maxMs) return this.finish(!this.paused)
    return null
  }

  private finish(steady: boolean): VadEvent {
    this.speaking = false
    this.voiced = 0
    this.steady = steady
    const mean = this.loudMs ? this.loudSum / this.loudMs : 0
    if (steady) {
      // Taken as the background from here on, so the next reading is measured against it.
      this.noise = Math.min(NOISE_CAP, mean)
    }
    const share = this.length ? this.loudMs / this.length : 0
    this.noisy = steady || share < SPEECH_SHARE || mean < this.noiseAtStart * SPEECH_SNR
    return 'end'
  }

  /** A held button decides the ends; the detector starts over. */
  reset() {
    this.speaking = false
    this.voiced = 0
    this.quiet = 0
    this.length = 0
  }
}
