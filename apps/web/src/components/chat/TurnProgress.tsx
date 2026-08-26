import { Loader2, Square } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** Counts from when the turn started. Ticks once a second; nothing needs finer. */
function useElapsed(since: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return Math.max(0, Math.floor((now - since) / 1000))
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * What is happening, while it is happening.
 *
 * The whole of this used to be the word 생각하는 중… blinking on its own. It
 * says that something is running and nothing else — not who is running it, not
 * for how long, and not whether it is still going. Combined with a turn that
 * could hang forever, that left the one question a person actually has
 * unanswerable: is this working, or is it stuck?
 *
 * So the line now carries the three facts that answer it. The model or agent
 * doing the work, because on a workspace with several of them "which one is
 * this" is a real question. The elapsed time, because a number that keeps
 * moving is the difference between slow and frozen — and it is what makes a
 * long answer bearable and a stalled one obvious. And the stop button, on the
 * turn itself rather than only at the far end of the composer, because the
 * moment somebody wants it is the moment they are looking here.
 */
export function TurnProgress({
  sessionId,
  startedAt,
  label,
  model,
}: {
  sessionId: string
  /** Epoch milliseconds. The turn's own start, not this component's mount. */
  startedAt: number
  label: string
  model?: string
}) {
  const t = useT()
  const elapsed = useElapsed(startedAt)
  const stopStreaming = useStore((s) => s.stopStreaming)
  const agents = useStore((s) => s.agents)
  const sessions = useStore((s) => s.sessions)
  const session = sessions.find((s) => s.id === sessionId)
  const agent = agents.find((a) => a.id === session?.agentId)

  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-md">
      <span className="flex items-center gap-2 text-faint">
        <Loader2 size={13} className="shrink-0 animate-spin text-accent" />
        <span className="animate-blink">{label}</span>
      </span>
      {/* Who is doing it. The agent's name wins when there is one: on an
          orchestrated run that is the thing being waited on, and the model
          underneath it is an implementation detail of that. */}
      {(agent || model) && (
        <span className="truncate text-sm text-faint">{agent ? agent.name : model}</span>
      )}
      <span className="text-sm text-faint tabular-nums">{clock(elapsed)}</span>
      <button
        onClick={() => stopStreaming(sessionId)}
        className="flex items-center gap-1 rounded-control px-1.5 py-0.5 text-sm text-faint transition-colors hover:bg-elevated hover:text-fg"
      >
        <Square size={9} fill="currentColor" />
        {t('중단')}
      </button>
    </div>
  )
}
