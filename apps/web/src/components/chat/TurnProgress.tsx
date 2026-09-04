import { Loader2, Square } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useT } from '@/lib/useT'
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

/** Active model or agent, elapsed time, and cancellation control. */
export function TurnProgress({
  sessionId,
  startedAt,
  label,
  model,
}: {
  sessionId: string
  /** Epoch ms of the turn's start. */
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
