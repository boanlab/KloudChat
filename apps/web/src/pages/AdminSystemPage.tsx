import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PageHeader } from '@/components/ui'
import { adminApi, type SystemSettings } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { AutoRoutingSection } from './settings/AutoRoutingSection'
import { BrandingSection } from './settings/BrandingSection'
import { FeaturesSection } from './settings/FeaturesSection'
import { MailSection } from './settings/MailSection'
import { OutlineModelSection } from './settings/OutlineModelSection'
import { ProxySection } from './settings/ProxySection'
import { SignupSection } from './settings/SignupSection'
import { TemplatesSection } from './settings/TemplatesSection'
import { ToolsSection } from './settings/ToolsSection'

const tabs = [
  { to: '/admin/system', label: '프록시', end: true },
  { to: '/admin/system/routing', label: '라우팅', end: false },
  { to: '/admin/system/features', label: '기능', end: false },
  { to: '/admin/system/templates', label: '공용 템플릿', end: false },
  { to: '/admin/system/branding', label: '브랜딩', end: false },
  { to: '/admin/system/mail', label: '메일', end: false },
  { to: '/admin/system/signup', label: '회원 가입', end: false },
]

/** Titled section with a one-line description. */
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
  // Shared by several tabs; a save in one refreshes the others.
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const reload = useCallback(async () => {
    const data = await adminApi.settings().catch(() => null)
    if (data) setSettings(data)
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('시스템')}</span>} />
      <PageBody>
        <PageHeader
          title={t('시스템')}
          description={t('이 인스턴스 전체에 적용되는 설정입니다. 저장 즉시 적용되며 재시작이 필요하지 않습니다.')}
        />

        {/* Scrolls horizontally: the labels overflow a phone width. */}
        <div role="tablist" className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              role="tab"
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
        </div>

        <Routes>
          <Route index element={<ProxySection settings={settings} reload={reload} />} />
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
                  description={t('웹 검색, 문서 가져오기, 코드 실행, 심층 조사, 음성 전사를 연결합니다.')}
                >
                  <ToolsSection settings={settings} onSaved={reload} />
                </Group>
              </div>
            }
          />
          <Route
            path="templates"
            element={
              <Group
                title={t('공용 템플릿')}
                description={t('기관 양식처럼 모두가 같은 형식으로 시작해야 하는 것을 한 번만 등록합니다.')}
              >
                <TemplatesSection />
              </Group>
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
          <Route path="*" element={<Navigate to="/admin/system" replace />} />
        </Routes>
      </PageBody>
    </>
  )
}
