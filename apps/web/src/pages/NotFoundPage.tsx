import { House, Search, SearchX } from 'lucide-react'
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui'
import { openSearch } from '@/components/layout/SearchDialog'
import { claimPageTitle } from '@/lib/pageTitle'
import { useT } from '@/lib/useT'

/** An address no screen owns (a mistyped or retired link): say so, and offer a way on. */
export function NotFoundPage() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const t = useT()
  useEffect(() => {
    claimPageTitle(t('페이지를 찾을 수 없음'))
    return () => claimPageTitle(null)
  }, [t])

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('페이지를 찾을 수 없음')}</span>} />
      <PageBody>
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center" data-testid="not-found">
          <div className="grid size-11 place-items-center rounded-card border border-line bg-elevated text-muted">
            <SearchX size={19} />
          </div>
          <div className="space-y-1">
            <h1 className="text-xl font-semibold">{t('이 주소에는 화면이 없습니다.')}</h1>
            <p className="max-w-sm text-base text-muted">
              {t('주소가 바뀌었거나 잘못 입력된 링크입니다. 찾던 대화나 파일은 검색으로 찾을 수 있습니다.')}
            </p>
            <p className="font-mono text-xs break-all text-faint">{pathname}</p>
          </div>
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="primary" onClick={() => navigate('/')}>
              <House size={15} />
              {t('홈으로')}
            </Button>
            <Button onClick={openSearch}>
              <Search size={15} />
              {t('검색')}
            </Button>
          </div>
        </div>
      </PageBody>
    </>
  )
}
