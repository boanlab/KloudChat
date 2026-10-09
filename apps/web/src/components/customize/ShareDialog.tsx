import { Globe, Lock } from 'lucide-react'
import { useState } from 'react'
import { Button, Modal } from '@/components/ui'
import { ApiError, errorMessage } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Agent, Skill } from '@/types'
import { AgentSharing } from './AgentForm'

/** 「공유」 for an agent (나만 / 모두에게, open or sealed) or a skill (나만 / 모두에게). */
export function ShareDialog({
  open,
  onClose,
  agent,
  skill,
}: {
  open: boolean
  onClose: () => void
  agent?: Agent
  skill?: Skill
}) {
  const t = useT()
  const upsertAgent = useStore((s) => s.upsertAgent)
  const upsertSkill = useStore((s) => s.upsertSkill)
  const [agentDraft, setAgentDraft] = useState<Agent | null>(agent ?? null)
  const [visibility, setVisibility] = useState<Skill['visibility']>(skill?.visibility ?? 'private')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      if (agentDraft) await upsertAgent(agentDraft)
      else if (skill) await upsertSkill({ ...skill, visibility })
      onClose()
    } catch (err) {
      setError(
        // Sharing re-runs the upload scan: a secret or hidden direction marks stop it.
        err instanceof ApiError && err.detail.startsWith('skill_share_blocked')
          ? t('공유하기 전에 고칠 곳이 있습니다. 키·토큰처럼 보이는 문자열이나 보이지 않는 방향 제어 문자를 지워 주세요.')
          : errorMessage(err, t('저장하지 못했습니다.')),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('공유')}
      width="max-w-lg"
      footer={
        <>
          <Button onClick={onClose}>{t('취소')}</Button>
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            {t('저장')}
          </Button>
        </>
      }
    >
      {error && <p role="status" className="text-base text-danger">{error}</p>}
      {agentDraft ? (
        <AgentSharing draft={agentDraft} onChange={setAgentDraft} />
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            {(
              [
                { id: 'private', label: t('나만 쓰기'), icon: Lock },
                { id: 'org', label: t('모두에게 공개'), icon: Globe },
              ] as const
            ).map((o) => {
              const Icon = o.icon
              return (
                <button
                  type="button"
                  key={o.id}
                  aria-pressed={visibility === o.id}
                  onClick={() => setVisibility(o.id)}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-1.5 rounded-control border px-3 py-2 text-base transition-colors',
                    visibility === o.id ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  <Icon size={14} />
                  {o.label}
                </button>
              )
            })}
          </div>
          <p className="text-sm text-faint">
            {t('공유하면 워크스페이스 스토어에 올라가고, 다른 사용자가 각자 사본을 가져갑니다. 내가 고쳐도 이미 가져간 사본은 그대로입니다.')}
          </p>
        </div>
      )}
    </Modal>
  )
}
