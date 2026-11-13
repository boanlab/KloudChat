import { useEffect, useRef, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { Button, Modal } from '@/components/ui'
import { leaveOffice, officeDirty } from '@/lib/officeGuard'
import { dropFormMarks, formsUnsaved } from '@/lib/unsaved'
import { useT } from '@/lib/useT'

const trim = (path: string) => path.replace(/\/+$/, '') || '/'

/**
 * Leaving a screen (a link, the router, or browser Back) while something is unsaved: an open
 * office document asks 저장 / 버리기 / 취소 in its own panel first, then any other unsaved
 * form asks 머물기 / 버리고 옮기기. One blocker for both, so a navigation asks each once.
 * Tab close and reload are `beforeunload` (`useMarkUnsaved`).
 */
export function UnsavedLinkGuard() {
  const t = useT()
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      // Same screen (a query or hash change): nothing is dropped.
      trim(currentLocation.pathname) !== trim(nextLocation.pathname) &&
      (officeDirty() || formsUnsaved()),
  )
  const [askForms, setAskForms] = useState(false)
  const asking = useRef(false)
  const latest = useRef(blocker)
  useEffect(() => {
    latest.current = blocker
  })

  useEffect(() => {
    if (blocker.state !== 'blocked' || asking.current) return
    asking.current = true
    void (async () => {
      const go = officeDirty() ? await leaveOffice() : true
      const now = latest.current
      if (now.state !== 'blocked') {
        asking.current = false
        return
      }
      if (!go) {
        asking.current = false
        now.reset()
      } else if (formsUnsaved()) {
        setAskForms(true)
      } else {
        asking.current = false
        now.proceed()
      }
    })()
  }, [blocker.state])

  const settle = (leave: boolean) => {
    setAskForms(false)
    asking.current = false
    const now = latest.current
    if (now.state !== 'blocked') return
    if (leave) {
      dropFormMarks()
      now.proceed()
    } else {
      now.reset()
    }
  }

  return (
    <Modal
      open={askForms}
      onClose={() => settle(false)}
      title={t('저장하지 않은 변경 내용이 있습니다')}
      description={t('이 화면을 떠나면 바꾼 내용이 사라집니다.')}
      footer={
        <>
          <Button onClick={() => settle(false)}>{t('머물기')}</Button>
          <Button variant="danger" onClick={() => settle(true)}>
            {t('버리고 옮기기')}
          </Button>
        </>
      }
    />
  )
}
