import { Bot, Copy, Lock, Share2, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PipelineSteps } from '@/components/agents/PipelineChain'
import { Markdown } from '@/components/chat/Markdown'
import { PageBody } from '@/components/layout/AppShell'
import { AgentForm } from '@/components/customize/AgentForm'
import { DetailLayout, DetailSection, HubTopBar, type MenuAction } from '@/components/customize/DetailLayout'
import { agentItem } from '@/components/customize/hubItems'
import { ShareDialog } from '@/components/customize/ShareDialog'
import { useItemActions } from '@/components/customize/tryItem'
import { ConfirmDialog, EmptyState, LoadingState, Switch } from '@/components/ui'
import { startFailure } from '@/lib/failures'
import { agentStartKind } from '@/lib/kinds'
import { useT, useTIn } from '@/lib/useT'
import { uid } from '@/lib/utils'
import { useStore } from '@/store/useStore'

/** /customize/agents/:id — what an agent does, its instructions, and (for its owner) its settings. */
export function AgentDetailPage() {
  const t = useT()
  const tHub = useTIn('hub')
  const navigate = useNavigate()
  const { agentId = '' } = useParams()
  const agents = useStore((s) => s.agents)
  const me = useStore((s) => s.user?.id)
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const ensureDesignTemplates = useStore((s) => s.ensureDesignTemplates)
  const upsertAgent = useStore((s) => s.upsertAgent)
  const deleteAgent = useStore((s) => s.deleteAgent)
  const newSession = useStore((s) => s.newSession)
  const send = useStore((s) => s.send)
  const setNotice = useStore((s) => s.setNotice)
  const { tryItem, addItem } = useItemActions()
  const [sharing, setSharing] = useState(false)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    void loadWorkspace()
    void ensureDesignTemplates()
  }, [loadWorkspace, ensureDesignTemplates])

  const agent = agents.find((a) => a.id === agentId)
  if (!agent) {
    return (
      <>
        <HubTopBar tab="agents" tabLabel="에이전트" title="" />
        <PageBody>
          {workspaceLoading || agents.length === 0 ? (
            <LoadingState label={t('에이전트를 불러오는 중…')} />
          ) : (
            <EmptyState icon={<Bot size={18} />} title={t('이 에이전트를 찾을 수 없습니다')} description={t('지워졌거나 볼 수 없는 에이전트입니다.')} />
          )}
        </PageBody>
      </>
    )
  }

  const item = agentItem(agent, me)
  const mine = agent.ownerId === me
  // A sealed copy runs the original's instructions without being able to read them.
  const hidden = agent.sealed || (!mine && agent.shareMode === 'sealed')
  const editable = mine && !agent.sealed

  const startWith = async (line: string) => {
    try {
      const kind = agentStartKind(agent)
      const id = await newSession(kind, { agentId: agent.id })
      navigate(`/s/${id}`)
      void send(id, kind, line)
    } catch (err) {
      setNotice(startFailure(err, t))
    }
  }

  const menu: MenuAction[] = []
  if (editable) menu.push({ label: '공유', icon: <Share2 size={14} />, run: () => setSharing(true) })
  if (editable) {
    menu.push({
      label: '복제',
      icon: <Copy size={14} />,
      run: async () => {
        try {
          const copy = await upsertAgent({
            ...agent,
            id: uid('ag'),
            name: `${t(agent.name)} ${t('사본')}`.slice(0, 80),
            slug: '',
            visibility: 'private',
          })
          navigate(`/customize/agents/${copy.id}`)
        } catch (err) {
          setNotice(startFailure(err, t))
        }
      },
    })
  }
  if (mine) menu.push({ label: '삭제', icon: <Trash2 size={14} />, run: () => setConfirming(true), danger: true })

  return (
    <>
      <DetailLayout
        item={item}
        tab="agents"
        tabLabel="에이전트"
        onTry={() => tryItem(item)}
        primary={
          mine
            ? editable
              ? { label: '편집', run: () => document.getElementById('settings')?.scrollIntoView({ behavior: 'smooth' }) }
              : undefined
            : {
                label: agent.installed ? '추가됨' : '추가',
                added: agent.installed,
                run: async () => {
                  const to = await addItem(item)
                  if (to) navigate(to)
                },
              }
        }
        menu={menu}
        aside={
          mine && (
            <Switch
              checked={agent.enabled}
              onChange={(v) => void upsertAgent({ ...agent, enabled: v })}
              label={t('{name} 활성화').replace('{name}', t(agent.name))}
            />
          )
        }
      >
        <DetailSection title={tHub('개요')}>
          {agent.description && <p className="text-base text-fg">{t(agent.description)}</p>}
          {agent.guide && (
            <div className="rounded-card border border-line bg-panel px-4 py-3">
              <p className="mb-1 text-sm font-medium text-faint">{t('사용법')}</p>
              <p className="whitespace-pre-wrap text-base text-muted">{agent.guide}</p>
            </div>
          )}
          {agent.starters.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium text-faint">{t('이렇게 시작해 보세요')}</p>
              <div className="flex flex-wrap gap-2">
                {agent.starters.map((line) => (
                  <button
                    key={line}
                    type="button"
                    onClick={() => void startWith(line)}
                    className="max-w-full rounded-full border border-line bg-panel px-3.5 py-1.5 text-left text-sm text-fg transition-colors hover:border-accent/40 hover:bg-accent-soft"
                  >
                    {line}
                  </button>
                ))}
              </div>
            </div>
          )}
          <PipelineSteps agentId={agent.id} details title={t('이렇게 일합니다')} />
          {!agent.description && !agent.guide && agent.starters.length === 0 && (
            <p className="text-base text-faint">{t('설명이 아직 없습니다.')}</p>
          )}
        </DetailSection>

        <DetailSection title={t('지시')}>
          {hidden ? (
            <p className="flex items-center gap-2 rounded-card border border-line bg-elevated px-4 py-3 text-base text-muted">
              <Lock size={14} aria-hidden />
              {t('내용 비공개')} · {t('내용은 작성자만 봅니다. 가져간 복사본은 편집할 수 없고 원본의 지침으로 동작합니다.')}
            </p>
          ) : agent.systemPrompt.trim() ? (
            <div className="max-h-[28rem] overflow-y-auto rounded-card border border-line bg-panel px-4 py-3">
              <Markdown>{agent.systemPrompt}</Markdown>
            </div>
          ) : (
            <p className="text-base text-faint">{t('지시가 비어 있습니다.')}</p>
          )}
        </DetailSection>

        {editable && (
          <DetailSection id="settings" title={t('설정')}>
            <AgentForm key={`${agent.id}:${agent.visibility}:${agent.shareMode}`} initial={agent} creating={false} onSaved={() => undefined} aboveBottomBar />
          </DetailSection>
        )}
      </DetailLayout>

      {sharing && <ShareDialog open onClose={() => setSharing(false)} agent={agent} />}
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          void deleteAgent(agent.id)
          navigate('/customize?tab=agents')
        }}
        title={t('{name} 삭제').replace('{name}', t(agent.name))}
        description={t('되돌릴 수 없습니다. 이 에이전트로 하던 대화는 그대로 남습니다.')}
      />
    </>
  )
}
