import { Download, Share, SquarePlus } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button, Modal } from '@/components/ui'
import { useInstall } from '@/lib/install'
import { useT } from '@/lib/useT'

function Step({ n, children }: { n: number; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
        {n}
      </span>
      <span className="flex items-center gap-1.5">{children}</span>
    </li>
  )
}

/** Safari has no prompt: the steps, as a dialog. */
export function InstallHowTo({ open, onClose, macSafari }: { open: boolean; onClose: () => void; macSafari: boolean }) {
  const t = useT()
  return macSafari ? (
    <Modal
      open={open}
      onClose={onClose}
      title={t('Dock에 추가하기')}
      description={t('Safari에서는 파일 메뉴로 설치합니다.')}
      width="max-w-sm"
    >
      <ol className="space-y-3 text-base text-fg">
        <Step n={1}>{t('메뉴 막대의 「파일」을 엽니다')}</Step>
        <Step n={2}>
          <SquarePlus size={16} className="text-accent" />
          {t('「Dock에 추가…」를 고릅니다')}
        </Step>
        <Step n={3}>{t('「추가」를 누르면 Dock에 KloudChat 아이콘이 생깁니다')}</Step>
      </ol>
    </Modal>
  ) : (
    <Modal
      open={open}
      onClose={onClose}
      title={t('홈 화면에 추가하기')}
      description={t('Safari에서는 공유 메뉴로 설치합니다.')}
      width="max-w-sm"
    >
      <ol className="space-y-3 text-base text-fg">
        <Step n={1}>
          {t('아래 도구 막대의')}
          <Share size={16} className="text-accent" />
          {t('공유 버튼을 누릅니다')}
        </Step>
        <Step n={2}>
          <SquarePlus size={16} className="text-accent" />
          {t('「홈 화면에 추가」를 고릅니다')}
        </Step>
        <Step n={3}>{t('오른쪽 위 「추가」를 누르면 홈 화면에 KloudChat 아이콘이 생깁니다')}</Step>
      </ol>
    </Modal>
  )
}

/** PWA install: Chromium deferred prompt; Safari Share/File-menu steps; nothing on Firefox. */
export function InstallButton() {
  const t = useT()
  const app = useInstall()
  if (!app.installable) return null
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => void app.install()}
        aria-label={t('앱 설치')}
        title={t('KloudChat을 앱으로 설치합니다')}
      >
        <Download size={16} />
        <span className="text-sm font-medium">{t('앱 설치')}</span>
      </Button>
      <InstallHowTo open={app.howTo} onClose={app.closeHowTo} macSafari={app.macSafari} />
    </>
  )
}
