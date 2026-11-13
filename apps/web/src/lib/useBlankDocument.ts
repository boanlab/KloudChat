import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { artifactsApi, errorMessage, type OfficeKind } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** A file picked with nothing typed opens blank in its editor beside a chat. */
const BLANK_START = {
  hwp: { title: '새 한글 문서', failed: '한글 문서를 열지 못했습니다.' },
  docx: { title: '새 Word 문서', failed: 'Word 문서를 열지 못했습니다.' },
  pptx: { title: '새 PowerPoint 문서', failed: 'PowerPoint 문서를 열지 못했습니다.' },
} as const satisfies Record<OfficeKind, { title: string; failed: string }>

/** 「빈 문서로 시작」 of the 형식 chip, the same on the home screen and in an empty session;
 *  a project's start keeps the new chat in that project. */
export function useBlankDocument(projectId?: string | null) {
  const t = useT()
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const ensureSession = useStore((s) => s.ensureSession)
  const setNotice = useStore((s) => s.setNotice)
  const [opening, setOpening] = useState<OfficeKind | null>(null)
  const start = async (kind: OfficeKind) => {
    if (opening) return
    setOpening(kind)
    try {
      const id = await ensureSession(await newSession('chat', projectId ? { projectId } : undefined))
      const row = await artifactsApi.createDocument(kind, { sessionId: id, title: t(BLANK_START[kind].title) })
      navigate(`/s/${id}?artifact=${row.id}`)
    } catch (err) {
      setNotice(errorMessage(err, t(BLANK_START[kind].failed)))
    } finally {
      setOpening(null)
    }
  }
  return { start, opening }
}
