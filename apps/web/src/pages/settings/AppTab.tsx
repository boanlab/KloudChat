import { CheckCircle2, Download } from 'lucide-react'
import { InstallHowTo } from '@/components/layout/InstallButton'
import { Button } from '@/components/ui'
import { useInstall } from '@/lib/install'
import { useT } from '@/lib/useT'

/** 설정 › 앱: install as an app (Chromium's prompt, Safari's steps), or that it already is. */
export function AppTab() {
  const t = useT()
  const app = useInstall()
  const line = app.standalone
    ? t('앱으로 설치해 쓰고 있습니다.')
    : app.canPrompt
      ? t('설치하면 브라우저 탭 없이 따로 열립니다.')
      : app.ios
        ? t('Safari 공유 메뉴의 「홈 화면에 추가」로 설치합니다.')
        : app.macSafari
          ? t('Safari 파일 메뉴의 「Dock에 추가」로 설치합니다.')
          : t('이 브라우저에서는 앱으로 설치할 수 없습니다.')
  return (
    <section className="space-y-3" aria-labelledby="app-install">
      <h2 id="app-install" className="text-base font-semibold">
        {t('앱으로 설치')}
      </h2>
      <p className="flex items-center gap-2 text-base text-muted" data-testid="app-install-state">
        {app.standalone && <CheckCircle2 size={16} className="shrink-0 text-success" />}
        {line}
      </p>
      {app.installable && (
        <Button variant="primary" onClick={() => void app.install()}>
          <Download size={15} />
          {app.canPrompt ? t('앱으로 설치') : t('설치 방법 보기')}
        </Button>
      )}
      <InstallHowTo open={app.howTo} onClose={app.closeHowTo} macSafari={app.macSafari} />
    </section>
  )
}
