/** A voice conversation outlives the composer that shows it: a first question moves the page from the new
 * chat to its conversation, and the answer must still be read. Held here, not in a component. */
import { useSyncExternalStore } from 'react'
import { speakApi } from './api'
import { BlockFeed, type SpokenBlock } from './speechBlocks'
import { unlockAudio } from './speakProblems'
import { createVoicePlayer, type VoicePlayer } from './voicePlayer'

export interface VoiceTurn {
  /** Replies that were there before the question; they are not read. */
  seen: Set<string>
  begun: boolean
  since: number
  /** Everything of the answer has been handed to the voice. */
  done: boolean
}

export const session: { voice: string; feed: BlockFeed; turn: VoiceTurn | null } = {
  voice: 'sohee',
  feed: new BlockFeed(),
  turn: null,
}

let on = false
let player: VoicePlayer | null = null
let leaving: ReturnType<typeof setTimeout> | null = null
const modeSubs = new Set<() => void>()
const changeSubs = new Set<() => void>()

export const subscribeChange = (fn: () => void) => {
  changeSubs.add(fn)
  return () => changeSubs.delete(fn)
}

export function getPlayer(): VoicePlayer {
  player ??= createVoicePlayer(
    (block: SpokenBlock, signal: AbortSignal) =>
      speakApi.speak(block.text, session.voice, { spoken: block.source !== '', source: block.source, signal }),
    () => changeSubs.forEach((fn) => fn()),
  )
  return player
}

export function resetTurn() {
  session.turn = null
  session.feed = new BlockFeed()
}

export function setVoiceMode(next: boolean | ((was: boolean) => boolean)) {
  const value = typeof next === 'function' ? next(on) : next
  if (value === on) return
  // Opened by a click: the answers read later, after a wait, may play on a strict browser.
  if (value) unlockAudio()
  on = value
  if (!value) {
    player?.stop()
    resetTurn()
  }
  modeSubs.forEach((fn) => fn())
}

/** Whether voice mode is on right now (read in an event handler; a component uses `useVoiceMode`). */
export const voiceModeOn = () => on

/** Whether voice mode is on; survives the composer being rebuilt. */
export function useVoiceMode(): boolean {
  return useSyncExternalStore(
    (fn) => {
      modeSubs.add(fn)
      return () => modeSubs.delete(fn)
    },
    () => on,
  )
}

/** A panel is showing: a pending end (the composer was being rebuilt) is called off. */
export function panelArrived() {
  if (leaving) clearTimeout(leaving)
  leaving = null
}

/** The panel is gone; with none back shortly (the person left the chat) voice mode ends. */
export function panelLeft() {
  if (leaving) clearTimeout(leaving)
  leaving = setTimeout(() => {
    leaving = null
    setVoiceMode(false)
  }, 1500)
}
