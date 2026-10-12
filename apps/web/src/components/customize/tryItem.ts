import { useNavigate } from 'react-router-dom'
import { errorCode, errorMessage } from '@/lib/api'
import { startFailure } from '@/lib/failures'
import { agentStartKind } from '@/lib/kinds'
import { copyBot } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { HubItem } from './hubItems'

/** 「체험」 (conversation with the item) and 「+」 (copy into 내 항목); failures via the shell notice. */
export function useItemActions(onChanged?: () => void) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const setSessionSkills = useStore((s) => s.setSessionSkills)
  const installSkill = useStore((s) => s.installSkill)
  const installAgent = useStore((s) => s.installAgent)
  const deleteSkill = useStore((s) => s.deleteSkill)
  const setNotice = useStore((s) => s.setNotice)
  const setToast = useStore((s) => s.setToast)

  const chatWithSkill = async (skillId: string) => {
    const id = await newSession('chat')
    await setSessionSkills(id, [skillId])
    navigate(`/s/${id}`)
  }

  const tryItem = async (item: HubItem) => {
    try {
      // A published agent answers on its own page, on its publisher's credits.
      if (item.bot) {
        navigate(`/b/${item.bot.slug}`)
        return
      }
      if (item.agent) {
        const id = await newSession(agentStartKind(item.agent), { agentId: item.agent.id })
        navigate(`/s/${id}`)
        return
      }
      if (item.skill) {
        if (item.mine) {
          await chatWithSkill(item.skill.id)
          return
        }
        // Store skill runs from a copy: take one, say so, offer removal.
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
      setNotice(item.agent ? startFailure(err, t) : errorMessage(err, t('시작하지 못했습니다.')))
    }
  }

  /** Takes a copy; resolves to its screen, when it has one. */
  const addItem = async (item: HubItem): Promise<string | null> => {
    try {
      if (item.bot) {
        const id = await copyBot(item.bot.slug)
        onChanged?.()
        return `/customize/agents/${id}`
      }
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
    } catch (err) {
      setNotice(
        errorCode(err) === 'copy_closed'
          ? t('만든 사람이 프롬프트를 비공개로 두어 내 에이전트로 복사할 수 없습니다.')
          : errorMessage(err, t('가져오지 못했습니다.')),
      )
    }
    return null
  }

  return { tryItem, addItem }
}
