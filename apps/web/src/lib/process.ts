import { useEffect, useState } from 'react'
import { processApi, userPipelinesApi } from '@/lib/api'
import type { Agent, AgentBase, Bundle, BundleSelection, PipelineStep, PipelineVerdict, ProcessMode } from '@/types'

/** The agent's own step in its 직접 구성 or a 묶음: it runs that agent's own flow. */
export const SELF = '@self'

/** The stored shape of a step, without the server's display fields. */
export function bareSteps(steps: PipelineStep[]): PipelineStep[] {
  return steps.map(({ title: _title, output: _output, ...step }) => step)
}

/** What the form holds of an agent's 진행 과정 until it is saved. */
export interface ProcessDraft {
  mode: ProcessMode
  base: string | null
  bundles: BundleSelection[]
  steps: PipelineStep[]
}

/** The agent's 진행 과정 as the form starts it. */
export function processOf(agent: Agent): ProcessDraft {
  const p = agent.process
  if (!p) return { mode: agent.baseKey || agent.catalogKey ? 'base' : 'direct', base: agent.baseKey ?? agent.catalogKey ?? null, bundles: agent.bundles ?? [], steps: [] }
  return { mode: p.mode, base: p.base, bundles: p.bundles ?? [], steps: bareSteps(p.steps ?? []) }
}

/** The bases and the 묶음 a person may tick, fetched once per page. */
let catalogue: Promise<{ bases: AgentBase[]; bundles: Bundle[]; exportFormats: string[] } | null> | null = null
export function reloadProcessCatalogue() {
  catalogue = null
}
export function useProcessCatalogue(version = 0) {
  const [value, setValue] = useState<{ bases: AgentBase[]; bundles: Bundle[]; exportFormats: string[] } | null>(null)
  useEffect(() => {
    let current = true
    if (version > 0) catalogue = null
    catalogue ??= processApi.bases().catch(() => {
      catalogue = null
      return null
    })
    void catalogue.then((v) => current && setValue(v))
    return () => {
      current = false
    }
  }, [version])
  return value
}

/** The server's verdict on an agent's own steps, refreshed as they change. */
export function useStepVerdict(steps: PipelineStep[], agentId: string | null, enabled = true) {
  const [verdict, setVerdict] = useState<PipelineVerdict | null>(null)
  useEffect(() => {
    if (!enabled) return
    let current = true
    const timer = window.setTimeout(() => {
      void userPipelinesApi
        .validate(steps, undefined, agentId ?? undefined)
        .then((v) => current && setVerdict(v))
        .catch(() => undefined)
    }, 250)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [steps, agentId, enabled])
  return verdict
}

/** The payload `PUT /agents/{id}/process` takes for a draft. */
export function processPayload(value: ProcessDraft) {
  return value.mode === 'custom'
    ? { mode: value.mode, base: value.base, stages: value.steps }
    : { mode: value.mode, base: value.mode === 'base' ? value.base : null, bundles: value.bundles }
}
