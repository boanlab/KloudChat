import { useEffect, useState } from 'react'
import { processApi, userPipelinesApi } from '@/lib/api'
import { useResource, type Resource } from '@/lib/useResource'
import type { Agent, BundleSelection, GapQuestion, PipelineStep, PipelineVerdict, ProcessCatalogue, ProcessMode } from '@/types'
import type { PipelineEdge } from '@/types/pipelineGraph'

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
  /** Lines of a 직접 구성 with conditions (set on the canvas); none: linear in `steps` order. */
  edges?: PipelineEdge[] | null
}

/** The agent's 진행 과정 as the form starts it. */
export function processOf(agent: Agent): ProcessDraft {
  const p = agent.process
  if (!p) return { mode: agent.baseKey || agent.catalogKey ? 'base' : 'direct', base: agent.baseKey ?? agent.catalogKey ?? null, bundles: agent.bundles ?? [], steps: [] }
  const edges = (p as { edges?: PipelineEdge[] | null }).edges ?? null
  return { mode: p.mode, base: p.base, bundles: p.bundles ?? [], steps: bareSteps(p.steps ?? []), ...(edges ? { edges } : {}) }
}

/** The bases and the 묶음 a person may tick, fetched once per page; a failure is not kept. */
let catalogue: Promise<ProcessCatalogue> | null = null
export function reloadProcessCatalogue() {
  catalogue = null
}
function fetchCatalogue(): Promise<ProcessCatalogue> {
  catalogue ??= processApi.bases().catch((err: unknown) => {
    catalogue = null
    throw err
  })
  return catalogue
}

/** The catalogue as a `Resource`: loading, failed (with 다시 시도), or here. */
export function useProcessCatalogueState(version = 0): Resource<ProcessCatalogue> {
  return useResource(() => {
    if (version > 0) catalogue = null
    return fetchCatalogue()
  }, [version])
}

/** The catalogue, or null while it loads or after it failed. */
export function useProcessCatalogue(version = 0) {
  return useProcessCatalogueState(version).data
}

/** The server's verdict on an agent's own steps, refreshed as they change. */
export function useStepVerdict(
  steps: PipelineStep[],
  agentId: string | null,
  enabled = true,
  /** A new agent (no id yet): what its own step makes, so `@self` is judged as it will run. */
  self?: { surface: string; name: string },
  /** The form's 빠진 정보 질문 (undefined: the saved agent's): 확인 질문 is judged with them. */
  questions?: GapQuestion[],
) {
  const [verdict, setVerdict] = useState<PipelineVerdict | null>(null)
  const selfSurface = self?.surface
  const selfName = self?.name
  // Only the questions that ask something; a string, so an equal list does not ask again.
  const askedKey = questions === undefined ? '' : JSON.stringify(questions.filter((q) => q.question.trim()))
  useEffect(() => {
    if (!enabled) return
    let current = true
    const timer = window.setTimeout(() => {
      void userPipelinesApi
        .validate(
          steps,
          undefined,
          agentId ?? undefined,
          agentId ? undefined : { surface: selfSurface ?? 'chat', name: selfName ?? '' },
          askedKey ? (JSON.parse(askedKey) as GapQuestion[]) : undefined,
        )
        .then((v) => current && setVerdict(v))
        .catch(() => undefined)
    }, 250)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [steps, agentId, enabled, selfSurface, selfName, askedKey])
  return verdict
}

/** The payload `PUT /agents/{id}/process` takes for a draft. */
export function processPayload(value: ProcessDraft) {
  return value.mode === 'custom'
    ? { mode: value.mode, base: value.base, stages: value.steps, ...(value.edges ? { edges: value.edges } : {}) }
    : { mode: value.mode, base: value.mode === 'base' ? value.base : null, bundles: value.bundles }
}
