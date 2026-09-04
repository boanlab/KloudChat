import { Download, Share, SquarePlus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Modal } from '@/components/ui'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'

/** Chrome's deferred install prompt; not in lib.dom. */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

// The event fires once, early, usually before this component mounts.
let deferred: BeforeInstallPromptEvent | null = null
const listeners = new Set<() => void>()
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferred = e as BeforeInstallPromptEvent
    listeners.forEach((fn) => fn())
  })
  window.addEventListener('appinstalled', () => {
    deferred = null
    listeners.forEach((fn) => fn())
  })
}

const isApple = () =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  // iPadOS reports itself as a Mac; the touch points give it away.
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)

/** PWA install button for touch devices; Safari has no prompt, so it shows instructions. */
export function InstallButton() {
  const t = useT()
  const touch = useMediaQuery('(hover: none) and (pointer: coarse)')
  const standalone = useMediaQuery('(display-mode: standalone)')
  const [, bump] = useState(0)
  const [howTo, setHowTo] = useState(false)
  useEffect(() => {
    const fn = () => bump((n) => n + 1)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])

  const apple = isApple()
  if (!touch || standalone || (!deferred && !apple)) return null
  if (
    'standalone' in navigator &&
    (navigator as Navigator & { standalone?: boolean }).standalone
  )
    return null

  const install = async () => {
    if (!deferred) {
      setHowTo(true)
      return
    }
    const prompt = deferred
    await prompt.prompt()
    const { outcome } = await prompt.userChoice
    if (outcome === 'accepted') deferred = null
    bump((n) => n + 1)
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => void install()}
        aria-label={t('앱 설치')}
        title={t('홈 화면에 앱으로 설치합니다')}
      >
        <Download size={16} />
        <span className="text-sm font-medium">{t('앱 설치')}</span>
      </Button>
      <Modal
        open={howTo}
        onClose={() => setHowTo(false)}
        title={t('홈 화면에 추가하기')}
        description={t('Safari 에서는 공유 메뉴로 설치합니다.')}
        width="max-w-sm"
      >
        <ol className="space-y-3 text-base text-fg">
          <li className="flex items-start gap-3">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
              1
            </span>
            <span className="flex items-center gap-1.5">
              {t('아래 도구 막대의')}
              <Share size={16} className="text-accent" />
              {t('공유 버튼을 누릅니다')}
            </span>
          </li>
          <li className="flex items-start gap-3">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
              2
            </span>
            <span className="flex items-center gap-1.5">
              <SquarePlus size={16} className="text-accent" />
              {t('「홈 화면에 추가」를 고릅니다')}
            </span>
          </li>
          <li className="flex items-start gap-3">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
              3
            </span>
            <span>{t('오른쪽 위 「추가」를 누르면 홈 화면에 KloudChat 아이콘이 생깁니다')}</span>
          </li>
        </ol>
      </Modal>
    </>
  )
}
