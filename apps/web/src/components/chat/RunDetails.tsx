import { InfoButton, InfoPanel, useInfoPanel } from '@/components/ui/InfoPanel'
import { answerRows } from '@/lib/runInfo'
import { useT } from '@/lib/useT'
import type { AnswerRunInfo } from '@/types'

/** 「이번 답」 details under an answer: who answered (provider, quantization, fallback) and the
 *  call's settings. Public information: shown to everyone who can read the answer. */
export function RunDetails({
  run,
  modelLabel,
}: {
  run: AnswerRunInfo
  modelLabel: (id: string) => string
}) {
  const t = useT()
  const panel = useInfoPanel()
  const rows = answerRows(run, modelLabel, t)
  return (
    <div ref={panel.wrap} className="mb-2 max-w-md" data-testid="run-details">
      <div className="flex items-center gap-0.5 text-xs text-muted">
        <span>{run.provider ?? t('이번 답 실행 정보')}</span>
        <InfoButton
          panel={panel}
          label={t('이번 답 실행 정보')}
          className="size-7"
          testId="run-details-button"
        />
      </div>
      <InfoPanel
        panel={panel}
        rows={rows}
        note={t('시스템 프롬프트는 내용 대신 해시를 보여 줍니다.')}
        testId="run-details-panel"
      />
    </div>
  )
}
