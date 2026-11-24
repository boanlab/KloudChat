import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { leaveAllBlanks, leaveBlank, sessionOfPath } from '@/lib/blankDocs'

/** Leaving the conversation of an untouched 「빈 문서로 시작」 document drops it (`lib/blankDocs.ts`). A query change on the same
 *  conversation (another panel, another tab of the document) is not leaving. */
export function BlankDocumentGuard() {
  const { pathname } = useLocation()
  const last = useRef(pathname)
  useEffect(() => {
    const before = sessionOfPath(last.current)
    last.current = pathname
    if (before && before !== sessionOfPath(pathname)) leaveBlank(before)
  }, [pathname])
  useEffect(() => {
    window.addEventListener('pagehide', leaveAllBlanks)
    return () => window.removeEventListener('pagehide', leaveAllBlanks)
  }, [])
  return null
}
