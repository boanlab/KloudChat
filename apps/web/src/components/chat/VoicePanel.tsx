import { AudioLines, Hand, Loader2, Mic, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage, speakApi, transcriptionsApi } from '@/lib/api'
import { micFailure } from '@/lib/wavRecorder'
import { startVoiceListener, type VoiceListener } from '@/lib/voiceListener'
import { getPlayer, panelArrived, panelLeft, resetTurn, session, setVoiceMode, subscribeChange } from '@/lib/voiceSession'
import { VOICE_KEY, VOICE_TONE } from '@/lib/voices'
import { isStray, wavSeconds } from '@/lib/whisperStray'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

type Phase = 'starting' | 'listening' | 'hearing' | 'transcribing' | 'thinking' | 'speaking' | 'blocked' | 'hidden'

/** Seconds the answer may take to begin before the turn is given up quietly. */
const BEGIN_MS = 15_000

const storedVoice = () => {
  try {
    return localStorage.getItem(VOICE_KEY) ?? ''
  } catch {
    return ''
  }
}

/** The model stays loaded for a while after its last use; a panel rebuilt soon after needs no second warm-up. */
const WARM_MS = 5 * 60_000
let warmedAt = 0

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Talk to the chat: speech is cut into utterances, written out by the deployment's Whisper and sent as an
 * ordinary turn; the answer is read back sentence by sentence by the self-hosted voice. Speaking over it
 * stops it. The conversation keeps the words as text.
 */
export function VoicePanel({
  sessionId,
  voices,
  defaultVoice,
  send,
}: {
  sessionId: string | null
  voices: readonly string[]
  defaultVoice: string
  /** Sends the words as the next turn of the conversation. */
  send: (text: string) => void
}) {
  const t = useT()
  const [phase, setPhase] = useState<Phase>(session.turn ? 'thinking' : 'starting')
  const [heard, setHeard] = useState('')
  const [holding, setHolding] = useState(false)
  /** Why the microphone is not open, when it is not. */
  const [micIssue, setMicIssue] = useState<'denied' | 'missing' | 'failed'>('denied')
  /** Whisper gave back nothing: once is a mumble, three in a row is a microphone too far away. */
  const [empties, setEmpties] = useState(0)
  /** Sound that never paused was cut this many times in a row: the room is too loud for the sentence ends. */
  const [crowded, setCrowded] = useState(0)
  const [voice, setVoice] = useState(() => {
    const picked = storedVoice()
    return voices.includes(picked) ? picked : defaultVoice
  })
  const level = useRef<HTMLSpanElement>(null)
  const listener = useRef<VoiceListener | null>(null)
  const voiceRef = useRef(voice)
  voiceRef.current = voice
  session.voice = voice
  const sendRef = useRef(send)
  sendRef.current = send
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId
  const alive = useRef(true)
  const [, bump] = useState(0)

  const running = useStore((s) => (sessionId ? !!s.running[sessionId] : false))
  const messages = useStore((s) => s.sessions.find((c) => c.id === sessionId)?.messages)

  const replies = () =>
    (useStore.getState().sessions.find((c) => c.id === sessionRef.current)?.messages ?? []).filter(
      (m) => m.role === 'assistant',
    )

  const settle = useCallback(() => {
    if (!alive.current) return
    setPhase((p) => (p === 'hearing' || p === 'transcribing' ? p : 'listening'))
  }, [])

  // The deployment's speech model needs a warm-up; this one is thrown away.
  useEffect(() => {
    if (Date.now() - warmedAt < WARM_MS) return
    warmedAt = Date.now()
    void speakApi.speak('네.', voiceRef.current, { warm: true }).catch(() => undefined)
  }, [])

  /** Opens the microphone and starts listening; called when the panel opens, again after a retry or a hidden tab. */
  const open = useCallback(() => {
    const sound = getPlayer()
    setPhase('starting')
    void startVoiceListener({
      onLevel: (value) => {
        if (level.current) level.current.style.transform = `scaleY(${Math.max(0.12, value)})`
      },
      onSpeechStart: () => {
        // Speaking over the answer: it stops, and the turn that was being read is let go.
        sound.stop()
        resetTurn()
        const id = sessionRef.current
        if (id && useStore.getState().running[id]) useStore.getState().stopStreaming(id)
        setPhase('hearing')
      },
      onUtterance: (wav) => {
        // One at a time, in the order spoken: a second sentence waits for the first to be written out.
        pending.current.push(wav)
        void drain()
      },
      // Only sound in the room raises the bar; a reply still being voiced is silent.
      bar: () => (sound.sounding() ? 2.5 : 1),
      onEnded: () => {
        if (!alive.current) return
        setMicIssue('failed')
        setPhase('blocked')
      },
      onSteady: () => {
        if (!alive.current) return
        setCrowded((n) => n + 1)
        // The line below the status is for this now, not for the last thing heard.
        setHeard('')
      },
      onHoldLimit: () => setHolding(false),
    })
      .then((l) => {
        if (!alive.current || document.hidden) {
          void l.stop()
          if (alive.current) setPhase('hidden')
          return
        }
        listener.current = l
        // Rebuilt in the middle of an answer: still on it.
        setPhase(session.turn ? (getPlayer().busy() ? 'speaking' : 'thinking') : 'listening')
      })
      .catch((error: unknown) => {
        if (!alive.current) return
        setMicIssue(micFailure(error))
        setPhase('blocked')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const close = () => {
    const l = listener.current
    listener.current = null
    void l?.stop()
  }

  useEffect(() => {
    alive.current = true
    panelArrived()
    const sound = getPlayer()
    const follow = () => {
      if (!alive.current) return
      bump((n) => n + 1)
      if (!sound.busy() && session.turn?.done) {
        session.turn = null
        settle()
      } else if (sound.busy()) {
        setPhase((p) => (p === 'hearing' || p === 'transcribing' ? p : 'speaking'))
      }
    }
    const unfollow = subscribeChange(follow)
    open()
    // A hidden tab does not keep the microphone: it is let go, and opened again when the tab is back.
    const visibility = () => {
      if (!alive.current) return
      if (document.hidden) {
        close()
        setHolding(false)
        setPhase('hidden')
      } else if (!listener.current) {
        open()
      }
    }
    document.addEventListener('visibilitychange', visibility)
    return () => {
      alive.current = false
      document.removeEventListener('visibilitychange', visibility)
      unfollow()
      panelLeft()
      close()
    }
    // One microphone and one player for the panel's whole life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pending = useRef<Blob[]>([])
  const working = useRef(false)
  const drain = async () => {
    if (working.current) return
    working.current = true
    try {
      for (let wav = pending.current.shift(); wav; wav = pending.current.shift()) {
        if (!alive.current) break
        await utterance(wav)
      }
    } finally {
      working.current = false
      pending.current = []
    }
  }

  const utterance = async (wav: Blob) => {
    setPhase('transcribing')
    let text = ''
    try {
      // No `prompt`: Whisper conditions on it, and a long earlier answer used as the hint made it
      // return nothing for a short question.
      text = (await transcriptionsApi.transcribe(wav, 'speech.wav')).text.trim()
    } catch (err) {
      if (!alive.current) return
      if (err instanceof ApiError && err.status === 422) {
        // The sound arrived and made no words (the server answers 422 for that and nothing else
        // here): a status, not an error.
        setHeard('')
        setEmpties((n) => n + 1)
      } else if (err instanceof ApiError && err.status === 413) {
        setHeard(t('한 번에 너무 길게 말씀하셨습니다. 나눠서 말씀해 주세요.'))
      } else {
        setHeard(errorMessage(err, t('받아쓰지 못했습니다. 다시 말씀해 주세요.')))
      }
      setPhase('listening')
      return
    }
    if (!alive.current) return
    // A stock phrase from a short clip is what Whisper makes of noise; nobody said it.
    if (!/[\p{L}\p{N}]/u.test(text) || isStray(text, wavSeconds(wav.size))) {
      setHeard('')
      setEmpties((n) => n + 1)
      setPhase('listening')
      return
    }
    setEmpties(0)
    setCrowded(0)
    setHeard(text)
    // A turn still running is let go before the next is sent.
    const id = sessionRef.current
    if (id && useStore.getState().running[id]) {
      useStore.getState().stopStreaming(id)
      for (let i = 0; i < 40 && useStore.getState().running[id]; i += 1) await sleep(100)
    }
    if (!alive.current) return
    resetTurn()
    session.turn = {
      seen: new Set(replies().map((m) => m.id)),
      begun: false,
      since: Date.now(),
      done: false,
    }
    setPhase('thinking')
    sendRef.current(text)
  }

  // The answer as it grows: each finished sentence is voiced.
  useEffect(() => {
    const current = session.turn
    if (!current || current.done) return
    const reply = replies().findLast((m) => !current.seen.has(m.id))
    if (reply || running) current.begun = true
    if (reply?.content) {
      for (const block of session.feed.push(reply.content, false)) getPlayer().add(block)
    }
    if (current.begun && !running) {
      if (reply?.content) {
        for (const block of session.feed.push(reply.content, true)) getPlayer().add(block)
      }
      current.done = true
      if (!getPlayer().busy()) {
        session.turn = null
        settle()
      }
    }
    // `replies` reads the store at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, running])

  // An answer that never begins (a refused or lost send) is given up without a word.
  useEffect(() => {
    if (phase !== 'thinking') return
    const timer = setInterval(() => {
      const current = session.turn
      if (current && !current.begun && Date.now() - current.since > BEGIN_MS) {
        session.turn = null
        settle()
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [phase, settle])

  const pick = (next: string) => {
    setVoice(next)
    try {
      localStorage.setItem(VOICE_KEY, next)
    } catch {
      // no storage: the choice lasts until the panel closes
    }
  }

  const status: Record<Phase, string> = {
    starting: t('마이크를 준비하는 중…'),
    listening: t('듣고 있습니다. 말씀하세요'),
    hearing: t('말씀을 듣는 중…'),
    transcribing: t('받아쓰는 중…'),
    thinking: t('답을 쓰는 중…'),
    speaking: t('읽어 드리는 중. 말씀하시면 멈춥니다'),
    blocked:
      micIssue === 'missing'
        ? t('마이크를 찾지 못했습니다. 마이크 연결을 확인해 주세요.')
        : micIssue === 'failed'
          ? t('마이크를 열지 못했습니다. 다른 앱이 쓰고 있지 않은지 확인해 주세요.')
          : t('마이크를 쓸 수 없습니다. 브라우저의 마이크 권한을 확인해 주세요.'),
    hidden: t('화면이 가려져 마이크를 멈췄습니다'),
  }
  const voiceProblem = getPlayer().problem()
  const silent = getPlayer().failed() === true
  const problemLines: Record<string, string> = {
    limit: t('읽기 요청이 많아 글로만 보여 드립니다. 잠시 뒤 다시 말씀해 주세요.'),
    credits: t('크레딧이 부족해 글로만 보여 드립니다.'),
    unavailable: t('읽어 줄 음성 모델이 준비되어 있지 않아 글로만 보여 드립니다.'),
    playback: t('소리를 재생하지 못했습니다. 화면을 한 번 누른 뒤 다시 말씀해 주세요.'),
  }
  const line = silent
    ? problemLines[voiceProblem] ?? t('음성을 만들지 못해 글로만 보여 드립니다.')
    : heard ||
      (crowded >= 2
        ? t('주변이 시끄러워 말의 끝을 잡기 어렵습니다. 「누르고 말하기」를 쓰세요.')
        : empties >= 3
        ? t('잘 들리지 않습니다. 마이크 가까이에서 말씀하세요.')
        : empties > 0
          ? t('말씀은 들렸지만 글로 옮기지 못했습니다. 다시 말씀해 주세요.')
          : t('말씀하시면 여기에 받아쓴 글이 보입니다.'))

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-3 py-2.5"
      data-testid="voice-panel"
      data-phase={phase}
    >
      <span
        className={cn(
          'grid size-9 shrink-0 place-items-center rounded-full',
          phase === 'blocked' ? 'bg-elevated text-muted' : 'bg-accent-soft text-accent',
        )}
        aria-hidden
      >
        {phase === 'transcribing' || phase === 'thinking' || phase === 'starting' ? (
          <Loader2 size={16} className="animate-spin" />
        ) : (
          <AudioLines size={16} className={phase === 'speaking' || phase === 'hearing' ? 'animate-pulse' : undefined} />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p
          role={phase === 'blocked' ? 'alert' : 'status'}
          aria-live={phase === 'blocked' ? 'assertive' : 'polite'}
          className="text-sm font-medium"
          data-testid="voice-status"
        >
          {status[phase]}
        </p>
        <p className="truncate text-xs text-muted" data-testid="voice-heard">
          {line}
        </p>
      </div>
      <span className="flex h-6 w-3 shrink-0 items-end" aria-hidden>
        <span ref={level} className="block h-full w-1.5 origin-bottom rounded-full bg-accent transition-transform" style={{ transform: 'scaleY(0.12)' }} />
      </span>
      {voices.length > 1 && (
        <select
          value={voice}
          onChange={(e) => pick(e.target.value)}
          aria-label={t('목소리')}
          className="h-9 max-w-[9rem] rounded-control border border-line bg-surface px-2 text-sm"
        >
          {voices.map((v) => (
            <option key={v} value={v}>
              {v}
              {VOICE_TONE[v] ? ` · ${t(VOICE_TONE[v])}` : ''}
            </option>
          ))}
        </select>
      )}
      <button
        type="button"
        disabled={phase === 'blocked' || phase === 'starting' || phase === 'hidden'}
        aria-pressed={holding}
        aria-label={t('누르고 있는 동안 말하기')}
        title={t('소리로 알아듣기 어려우면 누른 채로 말하세요')}
        data-testid="voice-hold"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          setHolding(true)
          listener.current?.holdStart()
        }}
        onPointerUp={() => {
          setHolding(false)
          listener.current?.holdEnd()
        }}
        onPointerCancel={() => {
          setHolding(false)
          listener.current?.holdEnd()
        }}
        onKeyDown={(e) => {
          if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
            e.preventDefault()
            setHolding(true)
            listener.current?.holdStart()
          }
        }}
        onKeyUp={(e) => {
          if (e.key === ' ' || e.key === 'Enter') {
            setHolding(false)
            listener.current?.holdEnd()
          }
        }}
        className={cn(
          'flex h-9 touch-none select-none items-center gap-1.5 rounded-control border px-2.5 text-sm transition-colors disabled:opacity-50',
          holding || crowded >= 2
            ? 'border-accent bg-accent-soft text-accent'
            : 'border-line text-muted hover:bg-elevated hover:text-fg',
        )}
      >
        {holding ? <Mic size={14} /> : <Hand size={14} />}
        {t('누르고 말하기')}
      </button>
      {(phase === 'blocked' || phase === 'hidden') && (
        <button
          type="button"
          onClick={() => {
            close()
            open()
          }}
          data-testid="voice-retry"
          className="h-9 shrink-0 rounded-control border border-line px-2.5 text-sm text-muted transition-colors hover:bg-elevated hover:text-fg"
        >
          {t('다시 시도')}
        </button>
      )}
      <button
        type="button"
        onClick={() => setVoiceMode(false)}
        aria-label={t('음성 대화 끝내기')}
        data-testid="voice-end"
        className="grid size-9 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg"
      >
        <X size={16} />
      </button>
    </div>
  )
}
