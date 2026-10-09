import {
  Calculator,
  ChevronDown,
  ChevronRight,
  Download,
  FolderOpen,
  MessageCircleQuestion,
  PenLine,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import { Fragment, useState } from 'react'
import {
  CHAIN_LABEL,
  FLOW_LABEL,
  chainStepOf,
  chainSummary,
  checkName,
  orderedFlows,
  pipelineChain,
  stageLabel,
  type ChainStep,
} from '@/lib/stages'
import { useAgentPipeline } from '@/lib/useAgentPipeline'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { PipelineDefinition } from '@/types'

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

/**
 * An agent's workflow as a person reads it: its few chain steps, each with one line of what
 * happens. With `details`, every stage sits behind one collapsed 「자세히 보기」.
 */
export function PipelineSteps({
  agentId,
  details = false,
  title,
  className,
}: {
  agentId: string
  details?: boolean
  /** A heading above the steps, shown only when there are steps. */
  title?: string
  className?: string
}) {
  const t = useT()
  const pipeline = useAgentPipeline(agentId)
  const [open, setOpen] = useState(false)
  if (pipeline === undefined) return details ? <p className="text-sm text-faint">{t('불러오는 중…')}</p> : null
  if (pipeline === null) {
    return details ? <p className="text-sm text-faint">{t('이 에이전트의 워크플로를 불러오지 못했습니다.')}</p> : null
  }
  const chain = pipelineChain(pipeline)
  if (chain.length === 0) return null
  const spoken = chain.map((step) => t(CHAIN_LABEL[step])).join(' → ')
  const total = Object.values(pipeline.flows ?? {}).reduce((n, stages) => n + (stages?.length ?? 0), 0)
  return (
    <div className={cn('space-y-2', className)} data-testid="workflow-steps">
      {title && <p className="text-sm font-medium text-faint">{title}</p>}
      <ol className="space-y-1.5" aria-label={t('워크플로: {chain}').replace('{chain}', spoken)}>
        {chain.map((step) => {
          const Icon = CHAIN_ICON[step]
          return (
            <li key={step} className="flex items-start gap-2 text-sm" data-testid="workflow-step">
              <span className="mt-px grid size-5 shrink-0 place-items-center rounded-full bg-accent-soft text-accent" aria-hidden>
                <Icon size={11} />
              </span>
              <span className="min-w-0">
                <span className="font-medium text-fg">{t(CHAIN_LABEL[step])}</span>
                <span className="text-muted"> · {t(chainSummary(pipeline, step))}</span>
              </span>
            </li>
          )
        })}
      </ol>
      {details && total > 0 && (
        <div>
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="inline-flex items-center gap-1 rounded-control text-sm text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
          >
            <ChevronDown size={13} aria-hidden className={cn('transition-transform', open && 'rotate-180')} />
            {open ? t('간단히 보기') : t('자세히 보기 (단계 {n}개)').replace('{n}', String(total))}
          </button>
          {open && (
            <div className="mt-2 rounded-control border border-line p-3" data-testid="workflow-details">
              <PipelineFlowView pipeline={pipeline} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Every stage of a shipped pipeline, grouped by flow: Korean names first, ids only as 기술 정보. */
export function PipelineFlowView({ pipeline }: { pipeline: PipelineDefinition }) {
  const t = useT()
  const attached = pipeline.checks ?? []
  return (
    <div className="space-y-3">
      {orderedFlows(pipeline).map((flow) => {
        const stages = pipeline.flows[flow] ?? []
        if (stages.length === 0) return null
        return (
          <section key={flow} aria-label={t(FLOW_LABEL[flow] ?? flow)}>
            <h3 className="mb-1 text-sm font-semibold text-muted">{t(FLOW_LABEL[flow] ?? '그 밖의 단계')}</h3>
            <ul className="space-y-0.5">
              {stages.map((stage, i) => {
                const step = chainStepOf(stage)
                const Icon = step ? CHAIN_ICON[step] : null
                const checks = stage.checks ?? []
                return (
                  <li key={`${stage.name}-${i}`} className="flex items-start gap-2 text-sm" title={stage.name}>
                    {Icon ? <Icon size={12} className="mt-1 shrink-0 text-accent" aria-hidden /> : <span className="size-3 shrink-0" />}
                    <span className="min-w-0">
                      <span className="text-fg">{t(stageLabel(stage))}</span>
                      {checks.length > 0 && (
                        <span className="text-faint" title={checks.join(', ')}>
                          {' '}
                          · {checks.map((c) => t(checkName(pipeline, c))).join(', ')}
                        </span>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          </section>
        )
      })}
      {attached.length > 0 && (
        <p className="text-sm text-muted">
          {t('붙은 검사')}: {attached.map((c) => t(checkName(pipeline, c))).join(', ')}
        </p>
      )}
      <p className="break-all font-mono text-xs text-faint" data-testid="workflow-tech">
        {t('기술 정보')}: {pipeline.name}
        {attached.length > 0 && ` · ${attached.join(', ')}`}
      </p>
    </div>
  )
}
