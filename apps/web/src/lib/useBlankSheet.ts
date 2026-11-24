import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { artifactsApi, errorMessage } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** 「빈 시트로 시작」 on the 시트 surface: a sheet conversation with an empty workbook open in its grid; a project's start keeps it in that project. */
export function useBlankSheet(projectId?: string | null) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const ensureSession = useStore((s) => s.ensureSession)
  const setNotice = useStore((s) => s.setNotice)
  const [opening, setOpening] = useState(false)
  const [stage, setStage] = useState('')
  /** Held as well as in state: a second click before the re-render does nothing. */
  const busy = useRef(false)
  const start = async () => {
    if (busy.current) return
    busy.current = true
    setOpening(true)
    try {
      setStage(t('대화를 만드는 중…'))
      const id = await ensureSession(await newSession('sheet', projectId ? { projectId } : undefined))
      setStage(t('빈 시트를 만드는 중…'))
      const row = await artifactsApi.createSheet({ sessionId: id, title: t('새 시트') })
      navigate(`/s/${id}?artifact=${row.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t('시트를 열지 못했습니다. 잠시 뒤 다시 시도하세요.')))
    } finally {
      busy.current = false
      setOpening(false)
      setStage('')
    }
  }
  return { start, opening, stage }
}
