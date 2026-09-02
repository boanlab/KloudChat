import { Loader2 } from 'lucide-react'
import { Suspense, lazy, useEffect } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { RoleRoute } from '@/components/auth/RoleRoute'
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

/**
 * Re-reads the account when its allowance refills.
 *
 * The client keeps a running total — every turn adds its cost to `creditsUsed`
 * locally — and only re-reads the account at sign-in. That is right for
 * spending, which the client witnesses, and wrong for the refill, which
 * happens on the server at a moment no turn marks: the balance in the sidebar
 * went on counting down a month that had ended until somebody reloaded.
 *
 * The moment is not a guess. `cycleResetsAt` says when, so this waits for it
 * and asks once — and asking is itself what performs the refill, because every
 * authenticated request resolves the account through `refill_if_due`. The
 * answer carries the new cycle, which re-arms this for the month after.
 */
function useAllowanceRefresh() {
  const resetsAt = useStore((s) => s.user?.cycleResetsAt)
  const refreshMe = useStore((s) => s.refreshMe)

  useEffect(() => {
    const at = resetsAt ? Date.parse(resetsAt) : Number.NaN
    if (!Number.isFinite(at)) return
    const wait = at - Date.now()
    // Already past: the cycle rolled over while nothing was open.
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

function Authenticated() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const status = useStore((s) => s.user?.status)
  useAllowanceRefresh()

  if (authLoading) return <Spinner />

  if (!authenticated) return <LoginPage />
  // Signup lands in `pending`; nothing is reachable until an admin approves.
  if (status !== 'active') return <PendingApprovalPage />

  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />

          {/* /new/:kind 는 홈을 그 화면이 골라진 상태로 엽니다. 링크와 북마크가
              살아 있도록 경로는 남기고, 시작 화면은 하나로 둡니다. */}
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
          {/* One parent owns the role check so a new /admin route cannot be
              added beside an existing page and accidentally skip it. */}
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
