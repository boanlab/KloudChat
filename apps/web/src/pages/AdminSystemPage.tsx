import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PageHeader } from '@/components/ui'
import { adminApi, errorMessage, type SystemSettings } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { AutoRoutingSection } from './settings/AutoRoutingSection'
import { BrandingSection } from './settings/BrandingSection'
import { ConnectorCatalogSection } from './settings/ConnectorCatalogSection'
import { FeaturesSection } from './settings/FeaturesSection'
import { MailSection } from './settings/MailSection'
import { ModelRolesSection } from './settings/ModelRolesSection'
import { OutlineModelSection } from './settings/OutlineModelSection'
import { ProxySection } from './settings/ProxySection'
import { SignupSection } from './settings/SignupSection'
import { ToolsSection } from './settings/ToolsSection'
import { OcrSection } from './settings/OcrSection'
import { SettingsFetchNotice, type SettingsFetch } from './settings/SettingsFetch'

const tabs = [
  { to: '/admin/system', label: '프록시', end: true },
  { to: '/admin/system/models', label: '모델', end: false },
  { to: '/admin/system/routing', label: '라우팅', end: false },
  { to: '/admin/system/features', label: '기능', end: false },
  { to: '/admin/system/branding', label: '브랜딩', end: false },
  { to: '/admin/system/mail', label: '메일', end: false },
  { to: '/admin/system/signup', label: '회원 가입', end: false },
  { to: '/admin/system/connectors', label: '커넥터 카탈로그', end: false },
]

function Group({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <div>
      <h2 className="mb-1 text-base font-medium">{title}</h2>
      <p className="mb-4 text-base text-muted">{description}</p>
      {children}
    </div>
  )
}

export function AdminSystemPage() {
  const t = useT()
  // Shared by tabs; a save in one refreshes the others.
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  // Our own API's answer, not LiteLLM's: a failed fetch is said, never left as 「확인하는 중」.
  const [status, setStatus] = useState<SettingsFetch>({ loading: true, error: null, checkedAt: null })
  const reload = useCallback(async () => {
    setStatus((f) => ({ ...f, loading: true }))
    try {
      const data = await adminApi.settings()
      setSettings(data)
      setStatus({ loading: false, error: null, checkedAt: new Date().toISOString() })
    } catch (err) {
      setStatus((f) => ({ ...f, loading: false, error: errorMessage(err, t('설정을 불러오지 못했습니다. 잠시 후 다시 시도하세요.')) }))
    }
  }, [t])

  useEffect(() => {
    void reload()
  }, [reload])

  const here = useLocation().pathname.replace(/\/$/, '')
  // Proxy says it in its status card; routing and the catalogue do not read these settings.
  const notice = status.error && !['/admin/system', '/admin/system/routing', '/admin/system/connectors'].includes(here)

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('시스템')}</span>} />
      <PageBody>
        <PageHeader
          title={t('시스템')}
          description={t('이 인스턴스 전체에 적용되는 설정입니다. 저장 즉시 적용되며 재시작이 필요하지 않습니다.')}
        />

        {/* Labels overflow a phone width, so a phone wraps them. Links to pages: a nav with aria-current, not tabs. */}
        <nav aria-label={t('시스템 메뉴')} className="mb-5 flex gap-1 border-b border-line max-sm:flex-wrap sm:overflow-x-auto [scrollbar-width:none]">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                cn(
                  '-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-base font-medium transition-colors',
                  isActive
                    ? 'border-accent text-fg'
                    : 'border-transparent text-muted hover:text-fg',
                )
              }
            >
              {t(tab.label)}
            </NavLink>
          ))}
        </nav>

        {notice && <SettingsFetchNotice status={status} settings={settings} onRetry={() => void reload()} />}

        <Routes>
          <Route index element={<ProxySection settings={settings} status={status} reload={reload} />} />
          <Route
            path="models"
            element={
              <Group
                title={t('모델')}
                description={t('어떤 모델이 어떤 일을 맡을지 정합니다. 서버 설정 파일(.env)의 값은 처음 설치할 때 한 번만 쓰이고, 여기서 저장한 값이 우선합니다.')}
              >
                <ModelRolesSection settings={settings} onSaved={reload} failed={!!status.error} />
              </Group>
            }
          />
          <Route
            path="routing"
            element={
              <div className="space-y-5">
                <Group
                  title={t('모델 자동 라우팅')}
                  description={t('질문 난이도에 맞는 모델을 사용해 불필요한 고비용 모델 호출을 줄입니다.')}
                >
                  <AutoRoutingSection />
                </Group>
                <OutlineModelSection />
              </div>
            }
          />
          <Route
            path="features"
            element={
              <div className="space-y-5">
                <Group
                  title={t('사용할 기능')}
                  description={t('사용자에게 어떤 화면을 열어 둘지 정합니다.')}
                >
                  <FeaturesSection settings={settings} onSaved={reload} />
                </Group>
                <Group
                  title={t('기능 연동')}
                  description={t('웹 검색, 문서 가져오기, 코드 실행, 심층 조사, 음성 전사, 글자 읽기를 연결합니다.')}
                >
                  <ToolsSection settings={settings} onSaved={reload} />
                </Group>
                <Group
                  title={t('글자 읽기(OCR)')}
                  description={t('스캔본과 문서 사진의 글자를 읽는 방식과 모델을 정합니다.')}
                >
                  <OcrSection settings={settings} onSaved={reload} />
                </Group>
              </div>
            }
          />
          <Route
            path="branding"
            element={
              <Group
                title={t('브랜딩')}
                description={t('사이드바와 로그인 화면에 보이는 이름과 로고입니다.')}
              >
                <BrandingSection settings={settings} onSaved={reload} />
              </Group>
            }
          />
          <Route path="mail" element={<MailSection settings={settings} reload={reload} />} />
          <Route
            path="signup"
            element={
              <Group
                title={t('회원 가입')}
                description={t('누가, 어떤 주소로 가입할 수 있는지, 주소를 메일로 확인할지 정합니다.')}
              >
                <SignupSection settings={settings} onSaved={reload} />
              </Group>
            }
          />
          <Route
            path="connectors"
            element={
              <Group
                title={t('커넥터 카탈로그')}
                description={t('사용자가 사용자 지정 › 커넥터에서 설치할 수 있는 MCP 서버입니다. 명령으로 실행하는 서버(stdio)는 여기서만 추가할 수 있습니다.')}
              >
                <ConnectorCatalogSection />
              </Group>
            }
          />
          <Route path="*" element={<Navigate to="/admin/system" replace />} />
        </Routes>
      </PageBody>
    </>
  )
}
