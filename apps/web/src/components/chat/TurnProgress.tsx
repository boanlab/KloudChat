import { Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useStore } from '@/store/useStore'

/** Seconds since `since`, ticking once a second. */
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
 * What the turn is doing and for how long: 「답을 쓰는 중… 0:06」. The agent's name when one runs
 * it; the model is named on the answer's own line, and the composer's ■ stops the turn.
 */
export function TurnProgress({
  sessionId,
  startedAt,
  label,
}: {
  sessionId: string
  /** Epoch ms of the turn's start. */
  startedAt: number
  label: string
}) {
  const elapsed = useElapsed(startedAt)
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
      {agent && <span className="truncate text-sm text-faint">{agent.name}</span>}
      <span className="text-sm text-faint tabular-nums">{clock(elapsed)}</span>
    </div>
  )
}
