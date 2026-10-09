import {
  Calculator,
  ChevronRight,
  Download,
  FolderOpen,
  MessageCircleQuestion,
  PenLine,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import { Fragment } from 'react'
import { Badge } from '@/components/ui'
import { CHAIN_LABEL, FLOW_LABEL, KIND_LABEL, chainStepOf, orderedFlows, pipelineChain, type ChainStep } from '@/lib/stages'
import { useAgentPipeline } from '@/lib/useAgentPipeline'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

const CHAIN_ICON: Record<ChainStep, LucideIcon> = {
  material: FolderOpen,
  question: MessageCircleQuestion,
  compute: Calculator,
  write: PenLine,
  check: ShieldCheck,
  export: Download,
}

/** 자료 → 질문 → 계산 → 작성 → 검사 → 내보내기: the steps this agent's pipeline has. */
export function PipelineChain({ agentId, className, iconsOnly = false }: { agentId: string; className?: string; iconsOnly?: boolean }) {
  const t = useT()
  const pipeline = useAgentPipeline(agentId)
  if (!pipeline) return null
  const chain = pipelineChain(pipeline)
  if (chain.length === 0) return null
  const spoken = chain.map((step) => t(CHAIN_LABEL[step])).join(' → ')
  return (
    // One line: names show when the card is wide enough for all of them, icons alone otherwise.
    <div className={cn('@container min-w-0', className)}>
    <p
      className="flex min-w-0 flex-nowrap items-center gap-x-0.5 overflow-hidden whitespace-nowrap text-xs text-muted"
      aria-label={t('워크플로: {chain}').replace('{chain}', spoken)}
      title={spoken}
    >
      {chain.map((step, i) => {
        const Icon = CHAIN_ICON[step]
        return (
          <Fragment key={step}>
            {i > 0 && <ChevronRight size={10} className="shrink-0 text-faint" aria-hidden />}
            <span className="flex shrink-0 items-center gap-1 rounded-control bg-elevated px-1.5 py-0.5" aria-hidden title={t(CHAIN_LABEL[step])}>
              <Icon size={11} className="shrink-0 text-accent" />
              <span className={iconsOnly ? 'hidden' : '@max-[25rem]:hidden'}>{t(CHAIN_LABEL[step])}</span>
            </span>
          </Fragment>
        )
      })}
    </p>
    </div>
  )
}

/** An agent's shipped pipeline: each flow's stages with their kind and checks. Locked; `AgentFlowTab` copies it to edit. */
export function PipelineFlowView({ agentId }: { agentId: string }) {
  const t = useT()
  const pipeline = useAgentPipeline(agentId)
  if (pipeline === undefined) return <p className="py-6 text-center text-base text-faint">{t('불러오는 중…')}</p>
  if (pipeline === null) {
    return <p className="py-6 text-center text-base text-faint">{t('이 에이전트의 워크플로를 불러오지 못했습니다.')}</p>
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-base font-medium">
            {t('워크플로')} <span className="font-mono text-sm text-faint">{pipeline.name}</span>
          </p>
          <PipelineChain agentId={agentId} className="mt-1" />
        </div>
      </div>
      {pipeline.checks && pipeline.checks.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
          {t('붙은 검사')}
          {pipeline.checks.map((check) => <Badge key={check}>{check}</Badge>)}
        </p>
      )}
      {orderedFlows(pipeline).map((flow) => {
        const stages = pipeline.flows[flow] ?? []
        if (stages.length === 0) return null
        return (
          <section key={flow} aria-label={t(FLOW_LABEL[flow] ?? flow)}>
            <h3 className="mb-1.5 text-sm font-semibold text-muted">
              {t(FLOW_LABEL[flow] ?? flow)} <span className="font-mono font-normal text-faint">{flow}</span>
            </h3>
            <ol className="divide-y divide-line rounded-card border border-line">
              {stages.map((stage, i) => {
                const step = chainStepOf(stage)
                const Icon = step ? CHAIN_ICON[step] : null
                return (
                  <li key={`${stage.name}-${i}`} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-1.5 text-sm">
                    <span className="w-5 shrink-0 text-right tabular-nums text-faint">{i + 1}</span>
                    {Icon ? <Icon size={12} className="shrink-0 text-accent" aria-hidden /> : <span className="size-3 shrink-0" />}
                    <span className="font-medium text-fg">{t(stage.label)}</span>
                    <span className="font-mono text-xs text-faint max-sm:hidden">{stage.name}</span>
                    <span className="ml-auto flex flex-wrap items-center gap-1">
                      <Badge>{t(KIND_LABEL[stage.kind] ?? stage.kind)}</Badge>
                      {(stage.checks ?? []).map((check) => <Badge key={check} tone="accent">{check}</Badge>)}
                    </span>
                  </li>
                )
              })}
            </ol>
          </section>
        )
      })}
    </div>
  )
}
