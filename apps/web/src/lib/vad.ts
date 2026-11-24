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
}

export class Vad {
  private noise = 0.004
  private voiced = 0
  private quiet = 0
  private length = 0
  speaking = false

  private readonly opt: Required<VadOptions>

  constructor(opt: VadOptions = {}) {
    this.opt = { floor: 0.02, startMs: 240, endMs: 1100, maxMs: 30_000, ...opt }
  }

  /** Loudness needed now; `bar` raises it while the app itself is playing sound. */
  threshold(bar = 1): number {
    return Math.max(this.opt.floor, this.noise * 3.5) * bar
  }

  /** One reading of `ms` milliseconds at loudness `rms`. */
  feed(rms: number, ms: number, bar = 1): VadEvent {
    const loud = rms > this.threshold(bar)
    if (!this.speaking) {
      // The background level follows quiet readings only, slowly.
      if (!loud) this.noise = this.noise * 0.95 + rms * 0.05
      this.voiced = loud ? this.voiced + ms : 0
      if (this.voiced >= this.opt.startMs) {
        this.speaking = true
        this.quiet = 0
        this.length = this.voiced
        return 'start'
      }
      return null
    }
    this.length += ms
    this.quiet = loud ? 0 : this.quiet + ms
    if (this.quiet >= this.opt.endMs || this.length >= this.opt.maxMs) {
      this.speaking = false
      this.voiced = 0
      return 'end'
    }
    return null
  }

  /** A held button decides the ends; the detector starts over. */
  reset() {
    this.speaking = false
    this.voiced = 0
    this.quiet = 0
    this.length = 0
  }
}
