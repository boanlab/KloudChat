import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, artifactsApi, errorMessage } from '@/lib/api'
import { guardEdits, type AskReason } from '@/lib/officeGuard'
import { useMarkUnsaved } from '@/lib/unsaved'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { DeckArtifact, Slide } from '@/types'

/** What a hand edit of the web slides changes: the slides and the deck's design. */
export interface DeckContent {
  slides: Slide[]
  design: DeckArtifact['design']
}

/** Why the panel asks: the shared reasons, plus a server step on this deck and a download. */
export type DeckAskReason = AskReason | 'op' | 'export'

type Ask = { resolve: (go: boolean) => void; reason: DeckAskReason }

/** Undo steps kept, deck-wide. */
const HISTORY = 100
/** Edits under one key this close together are one undo step (typing in one field). */
const COALESCE_MS = 1200

const snapshot = (content: DeckContent) => JSON.stringify(content)

/**
 * The web slides as the office documents work (`useOfficeDocument`): hand edits stay in the
 * browser until 저장, which saves the whole deck as a new version conditional on the version
 * the edits started from; a lost race shows the conflict notice; the agent's version comes in
 * when nothing is unsaved (else the 'behind' notice); leaving or sending a turn with unsaved
 * edits asks through the shared guard (`lib/officeGuard`). Undo covers the whole deck and
 * belongs to the version it opened: another version coming in starts it afresh.
 */
export function useDeckDocument(deck: DeckArtifact) {
  const t = useT()
  const noteArtifactVersion = useStore((s) => s.noteArtifactVersion)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const agentWorking = useStore((s) => !!deck.sessionId && !!s.running[deck.sessionId])
  /** The edited copy; null while it is the saved deck. */
  const [work, setWork] = useState<DeckContent | null>(null)
  const [past, setPast] = useState<DeckContent[]>([])
  const [future, setFuture] = useState<DeckContent[]>([])
  /** The version the edits started from and its content, as saved. */
  const started = useRef<{ version: number; json: string } | null>(null)
  const lastEdit = useRef<{ key: string; at: number } | null>(null)
  const [saving, setSaving] = useState(false)
  const busy = useRef(false)
  const [notice, setNotice] = useState<'conflict' | 'behind' | null>(null)
  const [error, setError] = useState('')
  const [asking, setAsking] = useState<Ask | null>(null)
  /** Text typed into a box on the slide, committed when the box is left. */
  const [typing, setTyping] = useState(false)
  /** Versions this panel saved itself: their arrival is not another version. */
  const own = useRef(new Set<number>())
  const seenVersion = useRef(deck.version)

  const saved: DeckContent = { slides: deck.slides, design: deck.design ?? null }
  const content = work ?? saved
  const dirty = typing ||
    (work !== null && started.current !== null && snapshot(work) !== started.current.json)
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  const latestContent = useRef(content)
  latestContent.current = content

  /** Drops the edits and the undo history: the saved deck shows again. */
  const reset = useCallback(() => {
    setTyping(false)
    setWork(null)
    setPast([])
    setFuture([])
    started.current = null
    lastEdit.current = null
    setNotice(null)
  }, [])

  // Another version came in (the agent, a restore, another tab): shown when nothing is unsaved.
  useEffect(() => {
    if (deck.version === seenVersion.current) return
    seenVersion.current = deck.version
    if (own.current.has(deck.version)) return
    if (dirtyRef.current) setNotice('behind')
    else reset()
  }, [deck.version, reset])

  /** One edit: `change` gets the deck as it stands and returns it changed. */
  const edit = useCallback((change: (now: DeckContent) => DeckContent, key = '') => {
    const now = latestContent.current
    const next = change(now)
    if (next === now || snapshot(next) === snapshot(now)) return
    if (!started.current) started.current = { version: deck.version, json: snapshot(now) }
    const at = Date.now()
    const joined = key && lastEdit.current?.key === key && at - lastEdit.current.at < COALESCE_MS
    lastEdit.current = { key, at }
    if (!joined) setPast((rows) => [...rows.slice(-(HISTORY - 1)), now])
    setFuture([])
    latestContent.current = next
    setWork(next)
  }, [deck.version])

  /** One slide changed, by id. */
  const editSlide = useCallback((id: string, change: (slide: Slide) => Slide, key = '') =>
    edit((now) => ({ ...now, slides: now.slides.map((slide) => (slide.id === id ? change(slide) : slide)) }), key),
  [edit])

  const undo = useCallback(() => {
    if (!past.length) return
    const previous = past[past.length - 1]
    const current = latestContent.current
    setPast((rows) => rows.slice(0, -1))
    setFuture((rows) => [current, ...rows])
    lastEdit.current = null
    if (!started.current) started.current = { version: deck.version, json: snapshot(current) }
    latestContent.current = previous
    setWork(previous)
  }, [past, deck.version])

  const redo = useCallback(() => {
    if (!future.length) return
    const next = future[0]
    const current = latestContent.current
    setFuture((rows) => rows.slice(1))
    setPast((rows) => [...rows, current])
    lastEdit.current = null
    latestContent.current = next
    setWork(next)
  }, [future])

  /**
   * Saves the deck as a new version, conditional on the one the edits started from. True when
   * saved (or nothing to save).
   */
  const save = useCallback(async (): Promise<boolean> => {
    if (busy.current) return false
    // a box being typed in commits its text as it is left
    const active = document.activeElement
    if (active instanceof HTMLElement && active.isContentEditable) active.blur()
    const from = started.current
    const edited = latestContent.current
    if (!from || snapshot(edited) === from.json) return true
    busy.current = true
    setSaving(true)
    setError('')
    try {
      const latest = await artifactsApi.get(deck.id)
      if (latest.version !== from.version) {
        setNotice('conflict')
        return false
      }
      const data = (latest.data ?? {}) as Partial<DeckArtifact>
      const row = await artifactsApi.update(deck.id, {
        data: { ...data, kind: 'deck', theme: data.theme ?? deck.theme, slides: edited.slides,
          ...(edited.design ? { design: edited.design } : {}) },
        summary: t('편집기에서 저장'),
        expectedVersion: from.version,
      })
      own.current.add(row.version)
      // the panel's copy is the store's row: what is shown when nothing is unsaved
      deck.slides = edited.slides
      if (edited.design) deck.design = edited.design
      deck.version = row.version
      seenVersion.current = row.version
      noteArtifactVersion(deck.id, row.version)
      started.current = { version: row.version, json: snapshot(edited) }
      setWork(null)
      setNotice(null)
      void refreshArtifact(deck.id)
      return true
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setNotice('conflict')
      else setError(errorMessage(err, t('저장하지 못했습니다.')))
      return false
    } finally {
      busy.current = false
      setSaving(false)
    }
  }, [deck, noteArtifactVersion, refreshArtifact, t])

  /** The newest saved version, the edits dropped (「최신 판 불러오기」, 버리기). */
  const load = useCallback(async (): Promise<boolean> => {
    try {
      const fresh = await refreshArtifact(deck.id)
      if (fresh && fresh.kind === 'deck') {
        deck.slides = fresh.slides
        deck.design = fresh.design
        deck.version = fresh.version
        seenVersion.current = fresh.version
      }
      reset()
      setError('')
      return true
    } catch (err) {
      setError(errorMessage(err, t('최신 내용을 불러오지 못했습니다.')))
      return false
    }
  }, [deck, refreshArtifact, reset, t])

  /**
   * A version this panel added without the editor (a review note): the edits that started
   * from the version before still apply on top of it.
   */
  const adopt = useCallback((from: number, to: number) => {
    own.current.add(to)
    seenVersion.current = to
    if (started.current?.version === from) started.current = { ...started.current, version: to }
  }, [])

  // A tab close or reload asks while edits are unsaved; keyed as an office document, so the
  // shared guard (not the generic form guard) asks on in-app navigation.
  useMarkUnsaved(`office:${deck.id}`, dirty, () => dirtyRef.current)

  const ask = useCallback((reason: DeckAskReason) =>
    new Promise<boolean>((resolve) => setAsking({ resolve, reason })), [])

  const latest = useRef({ save })
  useEffect(() => {
    latest.current = { save }
  })
  useEffect(() => guardEdits(deck.sessionId, deck.id, {
    dirty: () => dirtyRef.current,
    ask: (reason) => ask(reason),
    save: () => latest.current.save(),
  }), [deck.sessionId, deck.id, ask])

  /** Before a step that changes the saved deck on the server: unsaved edits are settled first. */
  const settle = useCallback(async (reason: DeckAskReason = 'op') =>
    dirtyRef.current ? ask(reason) : true, [ask])

  const answer = async (choice: 'save' | 'discard' | 'cancel') => {
    const now = asking
    setAsking(null)
    if (!now) return
    const { resolve, reason } = now
    if (choice === 'cancel') return resolve(false)
    if (choice === 'save') return resolve(await latest.current.save())
    // a download of the saved version keeps the edits
    if (reason === 'export') return resolve(true)
    reset()
    resolve(true)
  }

  return {
    content, dirty, dirtyRef, markTyping: setTyping, saving, notice, setNotice, error, setError, edit, editSlide, undo,
    redo, canUndo: past.length > 0, canRedo: future.length > 0, save, load, reset, adopt, ask,
    settle, asking, answer, agentWorking,
  }
}

export type DeckDocument = ReturnType<typeof useDeckDocument>
