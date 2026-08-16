import { Loader2 } from 'lucide-react'
import { Suspense, lazy, useEffect } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { kindOrder } from '@/lib/kinds'
import { HomePage } from '@/pages/HomePage'
import { LoginPage } from '@/pages/LoginPage'
import { PendingApprovalPage } from '@/pages/PendingApprovalPage'
import { SessionPage } from '@/pages/SessionPage'
import { useStore } from '@/store/useStore'


/**
 * Screens fetched when somebody goes there.
 *
 * Chat, the session view and the login form are what a visit starts with;
 * everything below is a place you navigate *to*, and bundling it all into the
 * first request made signing in pay for the admin console. Kept as one list so
 * it is obvious what is deferred and what is not.
 */
const AdminGovernancePage = lazy(() => import('@/pages/AdminGovernancePage').then((m) => ({ default: m.AdminGovernancePage })))
const AdminSystemPage = lazy(() => import('@/pages/AdminSystemPage').then((m) => ({ default: m.AdminSystemPage })))
const AdminUsagePage = lazy(() => import('@/pages/AdminUsagePage').then((m) => ({ default: m.AdminUsagePage })))
const AdminUsersPage = lazy(() => import('@/pages/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })))
const AgentSetupPage = lazy(() => import('@/pages/AgentSetupPage').then((m) => ({ default: m.AgentSetupPage })))
const AgentsPage = lazy(() => import('@/pages/AgentsPage').then((m) => ({ default: m.AgentsPage })))
const ArtifactsPage = lazy(() => import('@/pages/ArtifactsPage').then((m) => ({ default: m.ArtifactsPage })))
const ConnectorsPage = lazy(() => import('@/pages/ConnectorsPage').then((m) => ({ default: m.ConnectorsPage })))
const HistoryPage = lazy(() => import('@/pages/HistoryPage').then((m) => ({ default: m.HistoryPage })))
const MemoryPage = lazy(() => import('@/pages/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const MyUsagePage = lazy(() => import('@/pages/MyUsagePage').then((m) => ({ default: m.MyUsagePage })))
const ProjectDetailPage = lazy(() => import('@/pages/ProjectDetailPage').then((m) => ({ default: m.ProjectDetailPage })))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const SharedPage = lazy(() => import('@/pages/SharedPage').then((m) => ({ default: m.SharedPage })))
const SkillsPage = lazy(() => import('@/pages/SkillsPage').then((m) => ({ default: m.SkillsPage })))

/**
 * Every screen that requires an account.
 *
 * The session check sits here rather than above the router because exactly one
 * route has to pass through it: a share link. That URL *is* the permission,
 * and whoever holds it may well have no account on this instance.
 */
/** The one waiting state: session check, and any screen still arriving. */
function Spinner() {
  return (
    <div className="grid h-full place-items-center bg-bg text-faint">
      <Loader2 size={20} className="animate-spin" />
    </div>
  )
}

function Authenticated() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const status = useStore((s) => s.user?.status)

  if (authLoading) return <Spinner />

  if (!authenticated) return <LoginPage />
  // Signup lands in `pending`; nothing is reachable until an admin approves.
  if (status !== 'active') return <PendingApprovalPage />

  return (
    <Suspense fallback={<Spinner />}>
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
    </Suspense>
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
      <Suspense fallback={<Spinner />}>
        <Routes>
          {/* Before the gate on purpose. See `Authenticated`. */}
          <Route path="/share/:token" element={<SharedPage />} />
          <Route path="*" element={<Authenticated />} />
        </Routes>
      </Suspense>
    </Router>
  )
}

