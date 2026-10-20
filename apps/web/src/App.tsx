import { Loader2 } from 'lucide-react'
import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes, useLocation, useNavigate } from 'react-router-dom'
import { RoleRoute } from '@/components/auth/RoleRoute'
import { AppShell } from '@/components/layout/AppShell'
import { setBotEmbedded } from '@/lib/api'
import { SHARED_AGENTS_PATH } from '@/lib/publishing'
import { kindOrder } from '@/lib/kinds'
import { HomePage } from '@/pages/HomePage'
import { LoginPage } from '@/pages/LoginPage'
import { PendingApprovalPage } from '@/pages/PendingApprovalPage'
import { SessionPage } from '@/pages/SessionPage'
import { useStore } from '@/store/useStore'

// Lazy screens: only home, session and login ship in the first bundle.
const AdminPublicationsPage = lazy(() => import('@/pages/AdminPublicationsPage').then((m) => ({ default: m.AdminPublicationsPage })))
const AdminIssuesPage = lazy(() => import('@/pages/AdminIssuesPage').then((m) => ({ default: m.AdminIssuesPage })))
const AdminGovernancePage = lazy(() => import('@/pages/AdminGovernancePage').then((m) => ({ default: m.AdminGovernancePage })))
const AdminSystemPage = lazy(() => import('@/pages/AdminSystemPage').then((m) => ({ default: m.AdminSystemPage })))
const AdminPerformancePage = lazy(() =>
  import('@/pages/AdminPerformancePage').then((m) => ({ default: m.AdminPerformancePage })),
)
const PerfHostPage = lazy(() => import('@/pages/perf/PerfHostPage').then((m) => ({ default: m.PerfHostPage })))
const PerfModelsPage = lazy(() => import('@/pages/perf/PerfModelsPage').then((m) => ({ default: m.PerfModelsPage })))
const PerfGpuPage = lazy(() => import('@/pages/perf/PerfGpuPage').then((m) => ({ default: m.PerfGpuPage })))
const PerfServerPage = lazy(() => import('@/pages/perf/PerfServerPage').then((m) => ({ default: m.PerfServerPage })))
const AdminUsagePage = lazy(() => import('@/pages/AdminUsagePage').then((m) => ({ default: m.AdminUsagePage })))
const AdminUsersPage = lazy(() => import('@/pages/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })))
const AgentSetupPage = lazy(() => import('@/pages/AgentSetupPage').then((m) => ({ default: m.AgentSetupPage })))
const ApiSetupPage = lazy(() => import('@/pages/ApiSetupPage').then((m) => ({ default: m.ApiSetupPage })))
const CustomizePage = lazy(() => import('@/pages/CustomizePage').then((m) => ({ default: m.CustomizePage })))
const AgentDetailPage = lazy(() =>
  import('@/pages/customize/AgentDetailPage').then((m) => ({ default: m.AgentDetailPage })),
)
const AgentCreatePage = lazy(() => import('@/pages/customize/CreatePages').then((m) => ({ default: m.AgentCreatePage })))
const TemplateEditPage = lazy(() => import('@/pages/customize/TemplateEditPage').then((m) => ({ default: m.TemplateEditPage })))
const TemplatesPage = lazy(() => import('@/pages/customize/TemplateEditPage').then((m) => ({ default: m.TemplatesPage })))
const SkillDetailPage = lazy(() =>
  import('@/pages/customize/SkillDetailPage').then((m) => ({ default: m.SkillDetailPage })),
)
const SkillCreatePage = lazy(() => import('@/pages/customize/CreatePages').then((m) => ({ default: m.SkillCreatePage })))
const SkillUploadPage = lazy(() => import('@/pages/customize/SkillUploadPage').then((m) => ({ default: m.SkillUploadPage })))
const ChainPage = lazy(() => import('@/pages/customize/ChainPage').then((m) => ({ default: m.ChainPage })))
const BundlesPage = lazy(() => import('@/pages/customize/BundlesPage').then((m) => ({ default: m.BundlesPage })))
const ArtifactsPage = lazy(() => import('@/pages/ArtifactsPage').then((m) => ({ default: m.ArtifactsPage })))
const FilesPage = lazy(() => import('@/pages/FilesPage').then((m) => ({ default: m.FilesPage })))
const HistoryPage = lazy(() => import('@/pages/HistoryPage').then((m) => ({ default: m.HistoryPage })))
const IssuesPage = lazy(() => import('@/pages/IssuesPage').then((m) => ({ default: m.IssuesPage })))
const MyUsagePage = lazy(() => import('@/pages/MyUsagePage').then((m) => ({ default: m.MyUsagePage })))
const ProjectDetailPage = lazy(() => import('@/pages/ProjectDetailPage').then((m) => ({ default: m.ProjectDetailPage })))
const ProjectsPage = lazy(() => import('@/pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const PublicBotsPage = lazy(() => import('@/pages/PublicBotsPage').then((m) => ({ default: m.PublicBotsPage })))
const SharedPage = lazy(() => import('@/pages/SharedPage').then((m) => ({ default: m.SharedPage })))
const BotPage = lazy(() => import('@/pages/BotPage').then((m) => ({ default: m.BotPage })))
const PublicationPage = lazy(() =>
  import('@/pages/customize/PublicationPage').then((m) => ({ default: m.PublicationPage })),
)

/** Old hub addresses → their tab, keeping the query. */
function ToHub({ tab }: { tab: string }) {
  const { search } = useLocation()
  const from = new URLSearchParams(search)
  from.delete('tab')
  const to = new URLSearchParams({ tab })
  if (tab === 'agents') {
    for (const key of ['agent', 'focus']) from.delete(key)
  }
  for (const [key, value] of from) to.set(key, value)
  return <Navigate to={`/customize?${to.toString()}`} replace />
}

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

/** Sign-in lands where this account may go, not on the previous user's address. */
function useLandingAfterSignIn(authenticated: boolean, role: string | undefined) {
  const navigate = useNavigate()
  const was = useRef(authenticated)
  useEffect(() => {
    const signedIn = !was.current && authenticated
    was.current = authenticated
    if (!signedIn || !role) return
    if (window.location.pathname.startsWith('/admin') && role !== 'admin') navigate('/', { replace: true })
  }, [authenticated, role, navigate])
}

/** Share link; a members-only link offers 로그인 in place, same address. */
function ShareRoute() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const [signingIn, setSigningIn] = useState(false)
  if (signingIn && !authenticated && !authLoading) return <LoginPage />
  return <SharedPage onSignIn={() => setSigningIn(true)} />
}

/** Published bot page without the app shell; sign-in offered when required. */
function BotRoute() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const [signingIn, setSigningIn] = useState(false)
  if (authLoading) return <Spinner />
  if (signingIn && !authenticated) return <LoginPage />
  return (
    <Suspense fallback={<Spinner />}>
      <BotPage onSignIn={() => setSigningIn(true)} />
    </Suspense>
  )
}

/** Bot framed on another site: always accountless, calls marked to keep the frame's cookie. */
function EmbedRoute() {
  useState(() => {
    setBotEmbedded(true)
    return true
  })
  // Tells the host loader (`/embed/widget.js`) the bot drew, and forwards Esc unless a bot dialog is open.
  useEffect(() => {
    if (window.parent === window) return
    window.parent.postMessage({ type: 'kloudchat:ready' }, '*')
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('[role="dialog"]')) {
        window.parent.postMessage({ type: 'kloudchat:close' }, '*')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <Suspense fallback={<Spinner />}>
      <BotPage embedded />
    </Suspense>
  )
}

function Authenticated() {
  const authenticated = useStore((s) => s.authenticated)
  const authLoading = useStore((s) => s.authLoading)
  const status = useStore((s) => s.user?.status)
  const role = useStore((s) => s.user?.role)
  useAllowanceRefresh()
  useHomeAfterApproval(status)
  useLandingAfterSignIn(authenticated, role)

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
          <Route path="files" element={<FilesPage />} />
          <Route path="customize" element={<CustomizePage />} />
          <Route path="customize/agents/new" element={<AgentCreatePage />} />
          <Route path="customize/templates" element={<TemplatesPage />} />
          <Route path="customize/templates/new" element={<TemplateEditPage />} />
          <Route path="customize/templates/:templateId" element={<TemplateEditPage />} />
          <Route path="customize/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="customize/agents/:agentId/chain" element={<ChainPage />} />
          <Route path="customize/skills/new" element={<SkillCreatePage />} />
          <Route path="customize/skills/upload" element={<SkillUploadPage />} />
          <Route path="customize/skills/:skillId" element={<SkillDetailPage />} />
          <Route path="customize/bundles" element={<BundlesPage />} />
          <Route path="customize/publications/:publicationId" element={<PublicationPage />} />
          {/* Members' bots are agents in 사용자 지정; old links land on the shared group. */}
          <Route path="bots" element={<Navigate to={SHARED_AGENTS_PATH} replace />} />
          <Route path="customize/workflows/:pipelineId" element={<ToHub tab="agents" />} />
          <Route path="designs" element={<Navigate to="/settings/themes" replace />} />
          <Route path="agents" element={<ToHub tab="agents" />} />
          <Route path="skills" element={<ToHub tab="skills" />} />
          <Route path="pipelines" element={<ToHub tab="agents" />} />
          <Route path="pipelines/:pipelineId" element={<ToHub tab="agents" />} />
          <Route path="memory" element={<Navigate to="/settings/memory" replace />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="usage" element={<MyUsagePage />} />
          <Route path="issues" element={<IssuesPage />} />
          <Route path="connectors" element={<ToHub tab="connectors" />} />
          <Route path="agent-setup" element={<AgentSetupPage />} />
          <Route path="api-setup" element={<ApiSetupPage />} />
          {/* Tabs live inside these pages, so the parent needs the splat. */}
          <Route path="settings/*" element={<SettingsPage />} />
          {/* One parent owns the role check for every /admin route. */}
          <Route path="admin" element={<RoleRoute roles={['admin']} />}>
            <Route index element={<Navigate to="/admin/users" replace />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="usage" element={<AdminUsagePage />} />
            <Route path="performance" element={<AdminPerformancePage />} />
            <Route path="performance/host" element={<PerfHostPage />} />
            <Route path="performance/models" element={<PerfModelsPage />} />
            <Route path="performance/gpu" element={<PerfGpuPage />} />
            <Route path="performance/vllm" element={<PerfServerPage />} />
            <Route path="system/*" element={<AdminSystemPage />} />
            <Route path="governance" element={<AdminGovernancePage />} />
            <Route path="issues" element={<AdminIssuesPage />} />
            <Route path="issues/:issueId" element={<AdminIssuesPage />} />
            <Route path="publications" element={<AdminPublicationsPage />} />
            <Route path="publications/:publicationId" element={<AdminPublicationsPage />} />
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

  // Access token is memory-only: a reload asks the refresh cookie (not in an embed frame).
  useEffect(() => {
    if (!window.location.pathname.startsWith('/embed/')) void bootstrap()
  }, [bootstrap])

  return (
    <Router>
      <Suspense fallback={<Spinner />}>
        <Routes>
          {/* Share links and bots opened outside need no account, so these sit outside the auth gate. */}
          <Route path="/share/:token" element={<ShareRoute />} />
          <Route path="/b" element={<PublicBotsPage />} />
          <Route path="/b/:slug" element={<BotRoute />} />
          <Route path="/embed/b/:slug" element={<EmbedRoute />} />
          <Route path="*" element={<Authenticated />} />
        </Routes>
      </Suspense>
    </Router>
  )
}
