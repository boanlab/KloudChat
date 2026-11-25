import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { artifactsApi } from '@/lib/api'
import { leaveBlank, sessionOfPath, TAB_ID } from '@/lib/blankDocs'
import { useStore } from '@/store/useStore'

const OFFICE = new Set(['pptx', 'docx', 'hwp', 'xlsx'])
const PING_MS = 60_000

/** Leaving the conversation of an untouched 「빈 문서로 시작」 document drops it (`lib/blankDocs.ts`). A query change on the same
 *  conversation (another panel, another tab of the document) is not leaving. While a conversation's office document is open
 *  here, the server is told every minute, so another tab leaving does not drop what this one has open (the server ignores
 *  anything that is not an untouched blank). */
export function BlankDocumentGuard() {
  const { pathname } = useLocation()
  const last = useRef(pathname)
  useEffect(() => {
    const before = sessionOfPath(last.current)
    last.current = pathname
    if (before && before !== sessionOfPath(pathname)) leaveBlank(before)
  }, [pathname])

  const sessionId = sessionOfPath(pathname)
  const open = useStore((s) =>
    sessionId ? s.artifacts.filter((a) => a.sessionId === sessionId && OFFICE.has(a.kind)).map((a) => a.id).join(',') : '',
  )
  useEffect(() => {
    if (!open) return
    const ids = open.split(',')
    const ping = () => ids.forEach((id) => void artifactsApi.pingBlank(id, TAB_ID).catch(() => undefined))
    ping()
    const timer = setInterval(ping, PING_MS)
    return () => clearInterval(timer)
  }, [open])
  return null
}
