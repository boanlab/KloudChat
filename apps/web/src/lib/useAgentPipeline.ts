import { useEffect, useState } from 'react'
import { pipelinesApi } from '@/lib/api'
import type { PipelineDefinition } from '@/types'

/** One request per agent per page load; cards and the editor share it. */
const cache = new Map<string, Promise<PipelineDefinition | null>>()

function load(agentId: string) {
  let found = cache.get(agentId)
  if (!found) {
    found = pipelinesApi.forAgent(agentId).catch(() => null)
    cache.set(agentId, found)
  }
  return found
}

/** The agent's pipeline: `undefined` while loading, `null` when the server has none to give. */
export function useAgentPipeline(agentId: string): PipelineDefinition | null | undefined {
  const [state, setState] = useState<{ id: string; pipeline: PipelineDefinition | null } | null>(null)
  useEffect(() => {
    let current = true
    void load(agentId).then((pipeline) => { if (current) setState({ id: agentId, pipeline }) })
    return () => { current = false }
  }, [agentId])
  return state?.id === agentId ? state.pipeline : undefined
}
