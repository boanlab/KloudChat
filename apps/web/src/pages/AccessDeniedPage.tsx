import { House, ShieldX } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'

/** An authenticated account reached a screen its role does not own. */
export function AccessDeniedPage() {
  const navigate = useNavigate()
  const t = useT()

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('접근 제한')}</span>} />
      <PageBody>
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <div className="grid size-11 place-items-center rounded-card border border-line bg-elevated text-muted">
            <ShieldX size={19} />
          </div>
          <div className="space-y-1">
            <h1 className="text-xl font-semibold">{t('이 페이지에 접근할 수 없습니다.')}</h1>
            <p className="max-w-sm text-base text-muted">
              {t('관리자 권한이 필요한 화면입니다. 다른 작업은 계속 사용할 수 있습니다.')}
            </p>
          </div>
          <Button variant="primary" onClick={() => navigate('/')}>
            <House size={15} />
            {t('홈으로')}
          </Button>
        </div>
      </PageBody>
    </>
  )
}
