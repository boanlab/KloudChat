import { Workflow } from 'lucide-react'
import { useState } from 'react'
import { StageList } from '@/components/chat/StageList'
import { Button, Modal } from '@/components/ui'
import { FLOW_LABEL, stageTone } from '@/lib/stages'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Message } from '@/types'

/** Latest run in the session that wrote or revised the document (not a waiting card). */
function latestRun(messages: Message[]) {
  for (let i = messages.length - 1; i >= 0; i--) {
    const run = messages[i].run
    if (run && run.stages.length > 0 && run.flow !== 'pre') return run
  }
  return null
}

/** 「작업 단계」 on the 검토 tab: this version's run stages with evidence and 「이 단계부터 다시」. */
export function RunStagesButton({ sessionId }: { sessionId: string | null }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const run = useStore((s) => latestRun(s.sessions.find((c) => c.id === sessionId)?.messages ?? []))
  const busy = useStore((s) => !!(sessionId && s.running[sessionId]))
  if (!sessionId || !run) return null
  const flagged = run.stages.filter((s) => ['findings', 'failed', 'waiting'].includes(stageTone(s))).length
  return (
    <>
      <Button size="sm" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <Workflow size={13} />
        {t('작업 단계')}
        {flagged > 0 && <span className="text-warn">· {flagged}</span>}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('작업 단계')}
        description={[
          run.flow ? t(FLOW_LABEL[run.flow] ?? run.flow) : '',
          run.fromStage ? t('「{stage}」부터 다시 만든 판').replace('{stage}', run.stages.find((s) => s.name === run.fromStage)?.label ?? run.fromStage) : '',
        ].filter(Boolean).join(' · ') || undefined}
        width="max-w-xl"
      >
        <div className="max-h-[60vh] overflow-y-auto">
          <StageList sessionId={sessionId} run={run} live={busy} rerunnable />
        </div>
        {busy && <p className="mt-2 text-sm text-muted">{t('다시 만드는 중입니다. 끝나면 새 판이 열립니다.')}</p>}
      </Modal>
    </>
  )
}
