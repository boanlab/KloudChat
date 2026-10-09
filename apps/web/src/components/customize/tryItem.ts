import { useNavigate } from 'react-router-dom'
import { errorMessage, userPipelinesApi } from '@/lib/api'
import { startFailure } from '@/lib/failures'
import { agentStartKind } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { HubItem } from './hubItems'

/**
 * 「체험」 and 「+」 for every kind of hub item: 체험 opens a conversation with the item in it,
 * 「+」 takes a copy into 내 항목. Both report a failure through the shell's notice.
 */
export function useItemActions(onChanged?: () => void) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const setSessionSkills = useStore((s) => s.setSessionSkills)
  const installSkill = useStore((s) => s.installSkill)
  const installAgent = useStore((s) => s.installAgent)
  const deleteSkill = useStore((s) => s.deleteSkill)
  const loadSessions = useStore((s) => s.loadSessions)
  const setNotice = useStore((s) => s.setNotice)
  const setToast = useStore((s) => s.setToast)

  const startPipeline = async (id: string) => {
    const made = await userPipelinesApi.start(id)
    await loadSessions()
    navigate(`/s/${made.sessionId}`)
  }

  const chatWithSkill = async (skillId: string) => {
    const id = await newSession('chat')
    await setSessionSkills(id, [skillId])
    navigate(`/s/${id}`)
  }

  const tryItem = async (item: HubItem) => {
    try {
      if (item.agent) {
        const id = await newSession(agentStartKind(item.agent), { agentId: item.agent.id })
        navigate(`/s/${id}`)
        return
      }
      if (item.shippedAgent) {
        const id = await newSession(agentStartKind(item.shippedAgent), { agentId: item.shippedAgent.id })
        navigate(`/s/${id}`)
        return
      }
      if (item.pipeline) {
        await startPipeline(item.pipeline.id)
        return
      }
      if (item.skill) {
        if (item.mine) {
          await chatWithSkill(item.skill.id)
          return
        }
        // A store skill runs from a copy: take one, say so, and offer to give it back.
        const before = new Set(useStore.getState().skills.map((s) => s.id))
        await installSkill(item.skill.id)
        const held = useStore.getState().skills
        const copy = held.find((s) => s.originId === item.skill?.id) ?? held.find((s) => !before.has(s.id))
        if (!copy) return
        setToast({
          text: t('체험하려고 내 스킬에 추가했어요'),
          action: before.has(copy.id)
            ? undefined
            : { label: t('빼기'), run: () => void deleteSkill(copy.id) },
        })
        onChanged?.()
        await chatWithSkill(copy.id)
      }
    } catch (err) {
      setNotice(item.agent || item.shippedAgent ? startFailure(err, t) : errorMessage(err, t('시작하지 못했습니다.')))
    }
  }

  /** Takes a copy; resolves to where the copy can be opened, when it has a screen. */
  const addItem = async (item: HubItem): Promise<string | null> => {
    try {
      if (item.agent) {
        await installAgent(item.agent)
        const copy = useStore.getState().agents.find((a) => a.originId === item.agent?.id)
        onChanged?.()
        return copy ? `/customize/agents/${copy.id}` : null
      }
      if (item.skill) {
        await installSkill(item.skill.id)
        const copy = useStore.getState().skills.find((s) => s.originId === item.skill?.id)
        onChanged?.()
        return copy ? `/customize/skills/${copy.id}` : null
      }
      if (item.shippedAgent) {
        const made = await userPipelinesApi.create({ fromAgentId: item.shippedAgent.id })
        onChanged?.()
        return `/customize/workflows/${made.id}`
      }
      if (item.pipeline) {
        const made = await userPipelinesApi.create({ fromId: item.pipeline.id })
        setToast({ text: t('내 워크플로로 가져왔습니다.') })
        onChanged?.()
        return `/customize/workflows/${made.id}`
      }
    } catch (err) {
      setNotice(errorMessage(err, t('가져오지 못했습니다.')))
    }
    return null
  }

  return { tryItem, addItem }
}
