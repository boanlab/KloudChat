import { LogOut } from 'lucide-react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, PageHeader } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { AccessTab } from './settings/AccessTab'
import { KeysTab } from './settings/KeysTab'
import { PreferencesTab } from './settings/PreferencesTab'
import { ProfileTab } from './settings/ProfileTab'
import { useT } from '@/lib/useT'

/**
 * Settings as routes rather than one long page.
 *
 * A single scroll held account details, model defaults, behaviour toggles, and
 * proxy credentials — four audiences in one column, and nothing linkable. Each
 * tab is a URL now, so "Settings → System" is something you can send someone.
 */
const tabs = [
  { to: '/settings', label: '프로필', end: true },
  { to: '/settings/preferences', label: '환경설정', end: false },
  { to: '/settings/keys', label: 'API 키', end: false },
  // Was 접속기록, when the tab held only the record. It now leads with the
  // live sign-ins and what to do about them.
  { to: '/settings/access', label: '보안', end: false },
]

export function SettingsPage() {
  const t = useT()
  const { logout } = useStore()
  const navigate = useNavigate()
  // Every tab here is about the person looking at it, so there is nothing to
  // filter any more — System moved to the admin screens, where it belongs.
  const visible = tabs

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('설정')}</span>} />
      <PageBody>
        <PageHeader
          title={t('설정')}
          action={
            <Button
              variant="danger"
              onClick={async () => {
                await logout()
                navigate('/')
              }}
            >
              <LogOut size={15} />
              {t('로그아웃')}
            </Button>
          }
        />

        <div role="tablist" className="mb-5 flex gap-1 border-b border-line">
          {visible.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              role="tab"
              className={({ isActive }) =>
                cn(
                  '-mb-px border-b-2 px-3 py-2 text-base font-medium transition-colors',
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
          <Route index element={<ProfileTab />} />
          <Route path="preferences" element={<PreferencesTab />} />
          <Route path="keys" element={<KeysTab />} />
          <Route path="access" element={<AccessTab />} />
          <Route path="*" element={<Navigate to="/settings" replace />} />
        </Routes>
      </PageBody>
    </>
  )
}
