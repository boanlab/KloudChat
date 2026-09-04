import { Loader2 } from 'lucide-react'
import { Suspense, lazy, useEffect, useRef } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes, useNavigate } from 'react-router-dom'
import { RoleRoute } from '@/components/auth/RoleRoute'
import { AppShell } from '@/components/layout/AppShell'
import { kindOrder } from '@/lib/kinds'
import { HomePage } from '@/pages/HomePage'
import { LoginPage } from '@/pages/LoginPage'
import { PendingApprovalPage } from '@/pages/PendingApprovalPage'
import { SessionPage } from '@/pages/SessionPage'
import { useStore } from '@/store/useStore'

// Lazy screens: only home, session and login ship in the first bundle.
const AdminGovernancePage = lazy(() => import('@/pages/AdminGovernancePage').then((m) => ({ default: m.AdminGovernancePage })))
const AdminSystemPage = lazy(() => import('@/pages/AdminSystemPage').then((m) => ({ default: m.AdminSystemPage })))
const AdminUsagePage = lazy(() => import('@/pages/AdminUsagePage').then((m) => ({ default: m.AdminUsagePage })))
const AdminUsersPage = lazy(() => import('@/pages/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })))
const AgentSetupPage = lazy(() => import('@/pages/AgentSetupPage').then((m) => ({ default: m.AgentSetupPage })))
const ApiSetupPage = lazy(() => import('@/pages/ApiSetupPage').then((m) => ({ default: m.ApiSetupPage })))
const AgentsPage = lazy(() => import('@/pages/AgentsPage').then((m) => ({ default: m.AgentsPage })))
const ArtifactsPage = lazy(() => import('@/pages/ArtifactsPage').then((m) => ({ default: m.ArtifactsPage })))
const DesignsPage = lazy(() => import('@/pages/DesignsPage').then((m) => ({ default: m.DesignsPage })))
const ConnectorsPage = lazy(() => import('@/pages/ConnectorsPage').then((m) => ({ default: m.ConnectorsPage })))
const HistoryPage = lazy(() => import('@/pages/HistoryPage').then((m) => ({ default: m.HistoryPage })))
const MemoryPage = lazy(() => import('@/pages/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const MyUsagePage = lazy(() => import('@/pages/MyUsagePage').then((m) => ({ default: m.MyUsagePage })))
const ProjectDetailPage = lazy(() => import('@/pages/ProjectDetailPage').then((m) => ({ default: m.ProjectDetailPage })))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const SharedPage = lazy(() => import('@/pages/SharedPage').then((m) => ({ default: m.SharedPage })))
const SkillsPage = lazy(() => import('@/pages/SkillsPage').then((m) => ({ default: m.SkillsPage })))

function Spinner() {
  return (
    <div className="grid h-full place-items-center bg-bg text-faint">
      <Loader2 size={20} className="animate-spin" />
    </div>
  )
}

/** Re-reads the account at `cycleResetsAt`; the request itself performs the server-side refill. */
function useAllowanceRefresh() {
  const resetsAt = useStore((s) => s.user?.cycleResetsAt)
  const refreshMe = useStore((s) => s.refreshMe)

  useEffect(() => {
    const at = resetsAt ? Date.parse(resetsAt) : Number.NaN
    if (!Number.isFinite(at)) return
    const wait = at - Date.now()
    if (wait <= 0) {
      void refreshMe()
      return
    }
    // `setTimeout` treats anything over ~24.8 days as zero, which would spin.
    if (wait > 2_000_000_000) return
    const timer = setTimeout(() => void refreshMe(), wait + 1_000)
    return () => clearTimeout(timer)
  }, [resetsAt, refreshMe])
}

/** Pending → active lands at home rather than on whatever URL the tab was covering. */
function useHomeAfterApproval(status: string | undefined) {
  const navigate = useNavigate()
  const was = useRef(status)
  useEffect(() => {
    if (was.current === 'pending' && status === 'active') navigate('/', { replace: true })
    was.current = status
  }, [status, navigate])
}

function Authenticated() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const status = useStore((s) => s.user?.status)
  useAllowanceRefresh()
  useHomeAfterApproval(status)

  if (authLoading) return <Spinner />

  if (!authenticated) return <LoginPage />
  // Signup lands in `pending`; nothing is reachable until an admin approves.
  if (status !== 'active') return <PendingApprovalPage />

  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />

          {/* /new/:kind opens home with that kind preselected. */}
          {kindOrder.map((kind) => (
            <Route key={kind} path={`new/${kind}`} element={<HomePage initialKind={kind} />} />
          ))}
          <Route path="s/:sessionId" element={<SessionPage />} />

          <Route path="projects" element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<ProjectDetailPage />} />
          <Route path="artifacts" element={<ArtifactsPage />} />
          <Route path="designs" element={<DesignsPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="usage" element={<MyUsagePage />} />
          <Route path="connectors" element={<ConnectorsPage />} />
          <Route path="agent-setup" element={<AgentSetupPage />} />
          <Route path="api-setup" element={<ApiSetupPage />} />
          {/* Tabs live inside these pages, so the parent needs the splat. */}
          <Route path="settings/*" element={<SettingsPage />} />
          {/* One parent owns the role check for every /admin route. */}
          <Route path="admin" element={<RoleRoute roles={['admin']} />}>
            <Route index element={<Navigate to="/admin/users" replace />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="usage" element={<AdminUsagePage />} />
            <Route path="system/*" element={<AdminSystemPage />} />
            <Route path="governance" element={<AdminGovernancePage />} />
            <Route path="*" element={<Navigate to="/admin/users" replace />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

export default function App() {
  const bootstrap = useStore((s) => s.bootstrap)

  // Access token is memory-only; a reload asks the refresh cookie.
  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  return (
    <Router>
      <Suspense fallback={<Spinner />}>
        <Routes>
          {/* Share links need no account, so this sits outside the auth gate. */}
          <Route path="/share/:token" element={<SharedPage />} />
          <Route path="*" element={<Authenticated />} />
        </Routes>
      </Suspense>
    </Router>
  )
}
