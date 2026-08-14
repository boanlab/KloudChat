import { Loader2 } from 'lucide-react'
import { useEffect } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { kindOrder } from '@/lib/kinds'
import { AdminGovernancePage } from '@/pages/AdminGovernancePage'
import { AdminSystemPage } from '@/pages/AdminSystemPage'
import { AdminUsagePage } from '@/pages/AdminUsagePage'
import { AdminUsersPage } from '@/pages/AdminUsersPage'
import { AgentSetupPage } from '@/pages/AgentSetupPage'
import { AgentsPage } from '@/pages/AgentsPage'
import { ArtifactsPage } from '@/pages/ArtifactsPage'
import { ConnectorsPage } from '@/pages/ConnectorsPage'
import { HistoryPage } from '@/pages/HistoryPage'
import { MyUsagePage } from '@/pages/MyUsagePage'
import { SharedPage } from '@/pages/SharedPage'
import { HomePage } from '@/pages/HomePage'
import { LoginPage } from '@/pages/LoginPage'
import { MemoryPage } from '@/pages/MemoryPage'
import { PendingApprovalPage } from '@/pages/PendingApprovalPage'
import { ProjectDetailPage } from '@/pages/ProjectDetailPage'
import { ProjectsPage } from '@/pages/ProjectsPage'
import { SessionPage } from '@/pages/SessionPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { SkillsPage } from '@/pages/SkillsPage'
import { useStore } from '@/store/useStore'

/**
 * Every screen that requires an account.
 *
 * The session check sits here rather than above the router because exactly one
 * route has to pass through it: a share link. That URL *is* the permission,
 * and whoever holds it may well have no account on this instance.
 */
function Authenticated() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const status = useStore((s) => s.user?.status)

  if (authLoading) {
    return (
      <div className="grid h-full place-items-center bg-bg text-faint">
        <Loader2 size={20} className="animate-spin" />
      </div>
    )
  }

  if (!authenticated) return <LoginPage />
  // Signup lands in `pending`; nothing is reachable until an admin approves.
  if (status !== 'active') return <PendingApprovalPage />

  return (
    <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />

          {/* 다섯 개 생성 화면. /new/:kind 는 빈 세션, /s/:id 는 기존 세션. */}
          {kindOrder.map((kind) => (
            <Route key={kind} path={`new/${kind}`} element={<SessionPage newKind={kind} />} />
          ))}
          <Route path="s/:sessionId" element={<SessionPage />} />

          <Route path="projects" element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<ProjectDetailPage />} />
          <Route path="artifacts" element={<ArtifactsPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="usage" element={<MyUsagePage />} />
          <Route path="connectors" element={<ConnectorsPage />} />
          <Route path="agent-setup" element={<AgentSetupPage />} />
          {/* Nested routes live inside the page, so the parent needs the splat. */}
          <Route path="settings/*" element={<SettingsPage />} />
          <Route path="admin/users" element={<AdminUsersPage />} />
          <Route path="admin/usage" element={<AdminUsagePage />} />
          <Route path="admin/system" element={<AdminSystemPage />} />
          <Route path="admin/governance" element={<AdminGovernancePage />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
  )
}

export default function App() {
  const bootstrap = useStore((s) => s.bootstrap)

  // The access token is memory-only, so a reload has to ask the refresh cookie
  // whether a session survived. Runs for the share route too and simply finds
  // nothing — that page never reads the result.
  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  return (
    <Router>
      <Routes>
        {/* Before the gate on purpose. See `Authenticated`. */}
        <Route path="/share/:token" element={<SharedPage />} />
        <Route path="*" element={<Authenticated />} />
      </Routes>
    </Router>
  )
}

