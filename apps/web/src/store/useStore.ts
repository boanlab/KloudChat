import { create } from 'zustand'
import { applyBrand } from '@/lib/brand'
import { errorMessage } from '@/lib/api'
import {
  ApiError,
  PrivacyDecisionError,
  UnauthorizedError,
  adminApi,
  agentsApi,
  artifactsApi,
  jobsApi,
  authConfig,
  auth,
  connectorsApi,
  designsApi,
  designTemplatesApi,
  promptTemplatesApi,
  keysApi,
  filesApi,
  memoryApi,
  modelsApi,
  projectsApi,
  sessionsApi,
  setAccessToken,
  skillsApi,
  streamComparison,
  streamSession,
  toolsApi,
  usageApi,
} from '@/lib/api'
import type {
  AgentRow,
  ApiKeyRow,
  ArtifactRow,
  AuditRow,
  GovernancePolicy,
  CatalogEntry,
  ConnectorRow,
  DesignRow,
  DesignTemplateRow,
  JobRow,
  FileRow,
  MemoryRow,
  MessageRow,
  ModelCatalogue,
  ProjectRow,
  PromptTemplateRow,
  SessionRow,
  SkillRow,
  UsageReport,
} from '@/lib/api'
import type {
  Agent,
  Artifact,
  Connector,
  Job,
  MemoryEntry,
  Message,
  ModelInfo,
  Project,
  ProjectFile,
  Session,
  Preferences,
  PrivacyAction,
  PrivacyRouting,
  CodeArtifact,
  DeckArtifact,
  ReportArtifact,
  ReportSection,
  SessionKind,
  StartingPoint,
  Variant,
  Skill,
  Step,
  ToolCatalogEntry,
  User,
} from '@/types'
import { uid } from '@/lib/utils'
import { currentLang, translate, type Lang } from '@/lib/i18n'

/** The store is not a component and cannot use hooks, so only strings that
 *  reach the screen are translated here. */
const tr = (text: string) => translate(currentLang(), text)

type Theme = 'light' | 'dark' | 'system'

/** A client refusal is returned before send/compare writes its first Message. */
const isClientRefusal = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status >= 400 && error.status < 500

/**
 * Model and routing-mode changes are persisted separately from a message send.
 * Keep them ordered per conversation and make sends wait for the latest PATCH,
 * otherwise a quick Enter after choosing Auto can reach the server while the
 * conversation is still manual.
 */
const sessionPersistence = new Map<string, Promise<void>>()

function queueSessionPersistence(sessionId: string, persist: () => Promise<void>): Promise<void> {
  const previous = sessionPersistence.get(sessionId) ?? Promise.resolve()
  const queued = previous.catch(() => undefined).then(persist)
  sessionPersistence.set(sessionId, queued)
  const cleanup = () => {
    if (sessionPersistence.get(sessionId) === queued) sessionPersistence.delete(sessionId)
  }
  void queued.then(cleanup, cleanup)
  return queued
}

async function waitForSessionPersistence(sessionId: string): Promise<void> {
  // Another picker action can be queued while the previous PATCH is in flight.
  // Continue until the promise observed is still the last one for this session.
  while (true) {
    const pending = sessionPersistence.get(sessionId)
    if (!pending) return
    await pending
    if (sessionPersistence.get(sessionId) === pending) return
  }
}

interface State {
  // ── auth (KloudChat's own, not LiteLLM's) — live against /api/auth ─────────
  user: User | null
  authenticated: boolean
  /** True until the boot-time session check finishes. The sign-in screen is
   *  not drawn while it is. */
  authLoading: boolean
  /** Backend `detail` code from the last failed auth call, for the form to render. */
  authError: string | null
  bootstrap: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  signup: (email: string, password: string, name: string) => Promise<void>
  logout: () => Promise<void>
  /** Re-reads the caller's own row. The approval-waiting screen polls this. */
  refreshMe: () => Promise<void>
  updateProfile: (patch: {
    name?: string
    avatarColor?: string
    preferences?: Partial<Preferences>
  }) => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>

  // ── chrome ────────────────────────────────────────────────────────────
  theme: Theme
  toggleTheme: () => void
  lang: Lang
  toggleLang: () => void
  sidebarOpen: boolean
  toggleSidebar: () => void

  // ── data ──────────────────────────────────────────────────────────────
  users: User[]
  sessions: Session[]
  jobs: Job[]
  projects: Project[]
  artifacts: Artifact[]
  skills: Skill[]
  /** Looks this account can attach to a project: its own, plus shared ones. */
  designs: DesignRow[]
  /** Shapes the answer can come out in. Ships with the server; read-only. */
  designTemplates: DesignTemplateRow[]
  /** The built-in 시작점, loaded with the workspace so the gallery opens full
   *  rather than filling in a moment after it is read. */
  promptTemplates: PromptTemplateRow[]
  availableTools: ToolCatalogEntry[]
  memories: MemoryEntry[]
  agents: Agent[]

  // ── models — live against /api/models ─────────────────────────────────
  models: ModelInfo[]
  modelsLoading: boolean
  /**
   * Whether each collection has come back yet. `length === 0` means both an
   * empty workspace and a request in flight; screens need to tell them apart.
   */
  workspaceLoading: boolean
  artifactsLoading: boolean
  sessionsLoading: boolean
  /**
   * Whether the last refresh failed. Distinct from loading: the screen keeps
   * what it had, and has to say the list may be stale.
   */
  workspaceFailed: boolean
  artifactsFailed: boolean
  sessionsFailed: boolean
  /** False when the proxy did not answer; only adapter models are listed. */
  litellmAvailable: boolean
  autoRouting: ModelCatalogue['autoRouting']
  loadModels: () => Promise<void>

  // ── session / generation ──────────────────────────────────────────────
  activeSessionId: string | null
  streaming: boolean
  abortStream: (() => void) | null
  modelByKind: Record<SessionKind, string>
  setModel: (kind: SessionKind, id: string) => void
  /** Changes one conversation's model. The surface default is left alone. */
  setSessionModel: (sessionId: string, modelId: string) => Promise<void>
  /** Auto is stored as a session mode, never as a synthetic model id. */
  setSessionRoutingMode: (sessionId: string, mode: Session['routingMode']) => Promise<void>
  setActiveSession: (id: string | null) => void
  /** Sidebar list. Titles only — transcripts arrive with `openSession`. */
  loadSessions: () => Promise<void>
  openSession: (id: string) => Promise<void>
  newSession: (
    kind: SessionKind,
    opts?: {
      projectId?: string | null
      agentId?: string | null
      routingMode?: Session['routingMode']
    },
  ) => Promise<string>
  send: (
    sessionId: string | null,
    kind: SessionKind,
    text: string,
    opts?: {
      projectId?: string | null
      /** The composer's toggle — the user asking for the web to be consulted. */
      webSearch?: boolean
      /** Ids of already-uploaded files; the server reads their extracted text. */
      attachments?: string[]
      /** Their names, so the optimistic bubble does not show raw ids. */
      attachmentNames?: string[]
      /** Installed skills selected for this turn only. */
      activatedSkillIds?: string[]
      /**
       * A rendering template picked in the gallery. Sent with the turn rather
       * than saved first: the session may not exist yet when it is chosen, and
       * the server makes it sticky from there.
       */
      renderTemplateId?: string
      /**
       * A 시작점 the turn carries. The id is what goes on the wire; the title
       * rides along so the bubble can name it before the server's copy of the
       * turn comes back.
       */
      startingTemplate?: StartingPoint
      privacyAction?: PrivacyAction
      privacyDecisionToken?: string
            /**
             * Called as soon as a session id exists. Waiting for the stream to
             * finish would lose the conversation on a refresh mid-answer.
             */
      onSession?: (id: string) => void
    },
  ) => Promise<string>
  stopStreaming: () => void
  renameSession: (id: string, title: string) => Promise<void>
  /**
   * Puts a rendering template on a session, or takes it off.
   *
   * The turn makes it sticky server-side, so clearing the chip has to reach
   * the row as well — otherwise the next turn keeps writing into a shape the
   * composer no longer shows.
   */
  setSessionTemplate: (id: string, templateId: string | null) => Promise<void>
  deleteSession: (id: string) => Promise<void>
  /** Bulk removal from the history screen. Returns how many the server removed. */
  deleteSessions: (payload: { ids?: string[]; all?: boolean }) => Promise<number>
  /** Polls one job until it settles. */
  followJob: (sessionId: string, jobId: string) => Promise<void>
  /** Starts a video and follows it to completion. */
  generateVideo: (
    sessionId: string | null,
    prompt: string,
    opts?: { projectId?: string | null; onSession?: (id: string) => void },
  ) => Promise<void>
  /** Runs a failed job's request again. Nothing was charged for the failure. */
  retryJob: (job: Job) => Promise<void>
  /** Generates one sound clip on the a/v surface. */
  generateAudio: (
    sessionId: string | null,
    prompt: string,
    opts?: { projectId?: string | null; onSession?: (id: string) => void },
  ) => Promise<void>
  /** Generates pictures on the image surface and opens the panel on the last. */
  generateImages: (
    sessionId: string | null,
    prompt: string,
    opts?: { projectId?: string | null; onSession?: (id: string) => void },
  ) => Promise<void>
  togglePinSession: (id: string) => Promise<void>
  rateMessage: (
    sessionId: string,
    messageId: string,
    rating: 'up' | 'down' | null,
  ) => Promise<void>

  // ── model comparison ──────────────────────────────────────────────────
  compareMode: boolean
  compareModels: string[]
  toggleCompareMode: () => void
  toggleCompareModel: (id: string) => void
  /** Keeps one variant's answer and continues the conversation from it. */
  chooseVariant: (sessionId: string, messageId: string, model: string) => Promise<void>

  // ── governance (admin) ────────────────────────────────────────────────
  /** Both null until fetched. Null means "not loaded", not "zero". */
  usage: UsageReport | null
  audit: AuditRow[] | null
  loadUsage: (days?: number) => Promise<void>
  loadAudit: () => Promise<void>
  /** Instance policy. Null until fetched. */
  /** The user's own API keys. Null until fetched. */
  apiKeys: ApiKeyRow[] | null
  loadApiKeys: () => Promise<void>
  createApiKey: (name: string) => Promise<string | null>
  revokeApiKey: (id: string) => Promise<void>
  governance: GovernancePolicy | null
  loadGovernance: () => Promise<void>
  setGovernance: (patch: Partial<GovernancePolicy>) => Promise<number>
  /** Confirmed save for forms that must distinguish failure from success. */
  saveGovernance: (patch: Partial<GovernancePolicy>) => Promise<number>

  // ── image / audio-video ───────────────────────────────────────────────
  /**
   * The 서식 whose defaults the option chips are still showing, if any.
   *
   * A media 서식 leaves no chip on the composer — it is spent on the sentence
   * and on these values the moment it is picked — and the values are one
   * workspace-wide preference, not a property of the session it was picked in.
   * So a clip started a week later still comes out in that shape, and nothing
   * on the screen said why until this. Any hand-made change to an option drops
   * the name: from then on the values are the person's own.
   */
  optionTemplate: DesignTemplateRow | null
  setOptionTemplate: (template: DesignTemplateRow | null) => void
  imageOptions: { aspect: string; style: string; count: number }
  setImageOptions: (patch: Partial<State['imageOptions']>) => void
  /** `mode` picks which artifact the av surface produces; the rest is per-mode. */
  /** Text to drop into the composer. A template fills it in; it is never sent
   *  on the user's behalf. */
  draft: string
  setDraft: (text: string) => void
  /**
   * Form file a picked template carries, handed to the composer. The gallery
   * and the composer meet only here. Consumed once, or it re-attaches to every
   * later turn.
   */
  pendingAttachment: FileRow | null
  setPendingAttachment: (file: FileRow | null) => void
  /**
   * Rendering template picked in the gallery, waiting for the turn that will
   * use it. Held here rather than on the session because the session may not
   * exist yet — the server makes it sticky once the first turn arrives.
   */
  pendingTemplate: DesignTemplateRow | null
  setPendingTemplate: (template: DesignTemplateRow | null) => void
  /**
   * 시작점 picked in the gallery, handed to the composer the way a form file
   * is. Consumed once and put down: it attaches to the turn it was chosen
   * for, and the composer owns it from there.
   */
  pendingStartingTemplate: StartingPoint | null
  setPendingStartingTemplate: (template: StartingPoint | null) => void
  /** Whether this instance has a Whisper backend. Drives the composer's mic. */
  dictationEnabled: boolean
  /** Service name and logo to render. An empty logo draws the default mark. */
  brand: { name: string; logo: string }
  /** Re-read after an administrator saves branding, so it applies without a
   *  page reload. */
  refreshBrand: () => Promise<void>
  /** Surfaces the administrator has enabled. Chat is always among them. */
  enabledKinds: SessionKind[]
  avOptions: {
    mode: 'video' | 'audio'
    aspect: string
    durationSec: number
    audioKind: 'narration' | 'music'
    /** Narration only: which of the gateway's six voices reads it. */
    voice: string
    /** Video only. Both are priced separately — see videogen's rate table. */
    resolution: '720p' | '1080p'
    withAudio: boolean
  }
  setAvOptions: (patch: Partial<State['avOptions']>) => void
  /** Stops watching a clip and stops the charge that lands on delivery. */
  cancelJob: (id: string) => Promise<void>

  // ── artifact panel ────────────────────────────────────────────────────
  openArtifactId: string | null
  openArtifact: (id: string | null) => void
  /** A delete waiting out its undo window, if any. */
  pendingDelete: { label: string; undo: () => void } | null
  /** Why a media surface refused to start, for the surface to show. */
  mediaError: string | null
  clearMediaError: () => void
  /** Replaces the store's copy with the server's, and hands it back. */
  refreshArtifact: (id: string) => Promise<Artifact | null>

  // ── workspace — all live against /api ─────────────────────────────────
  /** One call after sign-in; each screen also refreshes its own slice. */
  loadWorkspace: () => Promise<void>
  createProject: (
    p: Pick<Project, 'name' | 'description' | 'emoji' | 'instructions'>,
  ) => Promise<string>
  updateProject: (id: string, patch: Partial<Project>) => Promise<void>
  deleteProject: (id: string) => Promise<void>
  uploadFile: (file: File, opts?: { projectId?: string; sessionId?: string }) => Promise<FileRow>
  deleteFile: (id: string) => Promise<void>
  /** Page one, for the current filter or the one passed in. */
  loadArtifacts: (filter?: ArtifactFilter) => Promise<void>
  /** The page after the oldest row on screen. */
  loadMoreArtifacts: () => Promise<void>
  /** What the gallery is currently showing: a kind, a title search, or both. */
  artifactFilter: ArtifactFilter
  artifactsHasMore: boolean
  artifactsLoadingMore: boolean
  /** How many of each kind exist for this filter — a page cannot say. */
  artifactCounts: Record<string, number> | null
  deleteArtifact: (id: string) => Promise<void>

  // ── MCP connectors ────────────────────────────────────────────────────
  connectors: Connector[]
  /** Servers available to install, with the ones already installed marked. */
  connectorCatalog: CatalogEntry[]
  toggleConnector: (id: string) => Promise<void>
  /** Re-supply a connector's credentials. Values are write-only server-side. */
  updateConnectorEnv: (id: string, env: Record<string, string>) => Promise<void>
  toggleConnectorTool: (id: string, tool: string) => Promise<void>
  installConnector: (slug: string, env?: Record<string, string>) => Promise<void>
  uninstallConnector: (id: string) => Promise<void>
  /** Re-asks the server what tools it exposes. */
  syncConnector: (id: string) => Promise<void>
  addCustomConnector: (
    c: Pick<Connector, 'name' | 'transport' | 'endpoint' | 'auth'> & {
      /** `KEY: value` credentials for the server process. Write-only. */
      env?: Record<string, string>
    },
  ) => Promise<void>

  // ── workspace ─────────────────────────────────────────────────────────
  toggleSkill: (id: string) => Promise<void>
  upsertSkill: (s: Skill) => Promise<void>
  deleteSkill: (id: string) => Promise<void>
  upsertMemory: (m: MemoryEntry) => Promise<void>
  deleteMemory: (id: string) => Promise<void>
  togglePinMemory: (id: string) => Promise<void>
  upsertAgent: (a: Agent) => Promise<void>
  /** Copies someone else's shared agent into your own workspace. */
  forkAgent: (a: Agent) => Promise<void>
  deleteAgent: (id: string) => Promise<void>

  // ── keys (KloudChat issues these against LiteLLM, server-side) ────────────

  // ── admin — live against /api/admin ───────────────────────────────────
  usersLoading: boolean
  loadUsers: () => Promise<void>
  approveUser: (id: string, monthlyCredits?: number) => Promise<void>
  rejectUser: (id: string) => Promise<void>
  suspendUser: (id: string) => Promise<void>
  reinstateUser: (id: string) => Promise<void>
  rotateLitellmKey: (id: string) => Promise<void>
  removeUser: (id: string) => Promise<void>
  /** Restricts an account to a set of models. Empty means the whole catalogue. */
  setUserModels: (id: string, models: string[]) => Promise<void>
  setUserCredits: (id: string, monthlyCredits: number) => Promise<void>
}

/** Bumped on every workspace write, so a stale fetch cannot overwrite newer
 *  state. */
let workspaceEpoch = 0
const touchWorkspace = () => ++workspaceEpoch

/**
 * Which artifact fetch is current. Two loaders fill the same list —
 * `loadArtifacts` and `loadWorkspace` — and without this the later *reply*
 * wins over the later *request*, leaving a stale snapshot that only surfaces as
 * a phantom edit conflict.
 */
/** One screenful and then some, matching the server's own page size. */
const ARTIFACT_PAGE = 60

/** What the gallery is showing. Both optional: no filter is the whole list. */
export type ArtifactFilter = { kind?: string; q?: string }

let artifactsEpoch = 0
//: The filter currently being fetched. Sign-in and the gallery ask for the
//: same first page within the same tick, and asking twice is the whole cost
//: this page was trying to shed.
let artifactsInFlight: string | null = null

/**
 * The same filter written the same way.
 *
 * `{}` and `{ kind: undefined, q: '' }` mean one thing and hash to two, which
 * is how sign-in and the gallery ended up asking for the same page twice.
 */
function sameFilter(filter: ArtifactFilter): ArtifactFilter {
  const kind = filter.kind || undefined
  const q = filter.q?.trim() || undefined
  return { ...(kind ? { kind } : {}), ...(q ? { q } : {}) }
}

const MODEL_STORAGE_KEY = 'kchat-models'

/** Remembered model choice per surface. Only ever holds ids picked from the
 *  real catalogue. */
const initialModelByKind: Record<SessionKind, string> = (() => {
  const blank = { chat: '', report: '', slides: '', image: '', av: '' }
  try {
    return { ...blank, ...JSON.parse(localStorage.getItem(MODEL_STORAGE_KEY) || '{}') }
  } catch {
    return blank
  }
})()

/**
 * `system` is the default and the state a reader can get back to. Storing the
 * resolved colour instead — which is what happened before — silently opted the
 * app out of the OS setting the first time the toggle was pressed, with no way
 * back short of clearing site data.
 */
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')

const initialTheme: Theme = (localStorage.getItem('kchat-theme') as Theme | null) ?? 'system'

let currentTheme: Theme = initialTheme

function applyTheme(theme: Theme) {
  currentTheme = theme
  const dark = theme === 'system' ? darkQuery.matches : theme === 'dark'
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem('kchat-theme', theme)
}
applyTheme(initialTheme)

// Following the system means following it after load too, not only at it.
darkQuery.addEventListener('change', () => {
  if (currentTheme === 'system') applyTheme('system')
})

// Start in Korean when the browser says Korean; English otherwise.
const initialLang: Lang =
  (localStorage.getItem('kchat-lang') as Lang | null) ??
  (navigator.language?.toLowerCase().startsWith('ko') ? 'ko' : 'en')

function applyLang(lang: Lang) {
  document.documentElement.lang = lang
  localStorage.setItem('kchat-lang', lang)
}
applyLang(initialLang)

/**
 * Re-issues the token shortly before it expires. The access token lives in
 * memory and lasts 15 minutes; the refresh cookie is httpOnly, so this is the
 * only way to learn whether the session is still alive.
 */
let refreshTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Collapses concurrent refreshes into one. Presenting the same refresh cookie
 * twice is what the server reads as token reuse.
 */
let inFlight: Promise<void> | null = null

function scheduleRefresh(expiresIn: number, run: () => void) {
  if (refreshTimer) clearTimeout(refreshTimer)
  // 60s of headroom, and never busier than once a minute.
  const delayMs = Math.max(30, expiresIn - 60) * 1000
  refreshTimer = setTimeout(run, delayMs)
}

function cancelRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
}

/**
 * What to say when a conversational turn arrives on a job surface. Duration and
 * voice come from the composer's controls, so this points at them rather than
 * imitating an answer.
 */
function handToTheComposer(set: Set, text: string) {
  // A clip and a piece of audio are started from the composer, where their
  // length, resolution and voice are chosen — so a prompt that arrives here
  // is handed back to it rather than answered.
  //
  // It used to append an assistant message saying so. That message was never
  // sent to a model and never stored, so the conversation showed a turn that
  // had not happened and lost it on the next reload: a made-up reply is a
  // worse way to say "not here" than simply putting the sentence where it
  // belongs.
  set({ draft: text })
}

/**
 * Falls back to the instance default when the remembered model is not in the
 * catalogue, and to the cheapest one when that model does not serve this
 * surface.
 */
function reconcileDefaults(
  current: Record<SessionKind, string>,
  available: ModelInfo[],
  instanceDefault = '',
): Record<SessionKind, string> {
  const next = { ...current }
  for (const kind of Object.keys(next) as SessionKind[]) {
    if (available.some((m) => m.id === next[kind])) continue
    const usable = available
      .filter((m) => m.kinds.includes(kind))
      .sort((a, b) => a.creditCost - b.creditCost)
    const preferred = usable.find((m) => m.id === instanceDefault)
    if (preferred) next[kind] = preferred.id
    else if (usable.length) next[kind] = usable[0].id
  }
  return next
}

/**
 * The model a turn on this conversation will actually run on.
 *
 * The precedence the API states — turn override, then the conversation, then
 * the agent — minus the turn override, which no screen can know in advance. A
 * conversation opened against an agent carries no model of its own until
 * somebody picks one, so without the middle step every surface here would name
 * the screen default while the server ran the agent's choice.
 */
export function effectiveModelId(
  session: Pick<Session, 'model' | 'agentId'> | undefined,
  kind: SessionKind,
  agents: Agent[],
  modelByKind: Record<SessionKind, string>,
): string {
  if (session?.model) return session.model
  const agent = session?.agentId ? agents.find((a) => a.id === session.agentId) : undefined
  return agent?.model || modelByKind[kind]
}

function reconcileCompareModels(current: string[], available: ModelInfo[]): string[] {
  const chatIds = available.filter((model) => model.kinds.includes('chat')).map((model) => model.id)
  const valid = current.filter((id, index) => chatIds.includes(id) && current.indexOf(id) === index)
  for (const id of chatIds) {
    if (valid.length >= 2) break
    if (!valid.includes(id)) valid.push(id)
  }
  return valid.slice(0, 3)
}


export const useStore = create<State>((set, get) => ({
  user: null,
  pendingDelete: null,
  mediaError: null,
  authenticated: false,
  authLoading: true,
  authError: null,

  /** Adopts a fresh session and arms the next silent refresh. */
  bootstrap: async () => {
    if (inFlight) return inFlight
    inFlight = (async () => {
      try {
        const session = await auth.refresh()
        setAccessToken(session.accessToken)
        set({ authenticated: true, user: session.user, authLoading: false, authError: null })
        // What this deployment can offer: no Whisper backend means the
        // composer hides its microphone rather than failing on click.
        void authConfig
          .get()
          .then((c) => {
            applyBrand(c.brand)
            set({
              dictationEnabled: c.dictationEnabled,
              brand: c.brand,
              enabledKinds: (c.enabledKinds ?? ['chat']) as SessionKind[],
            })
          })
          .catch(() => {})
        scheduleRefresh(session.expiresIn, () => void get().bootstrap())
        void get().loadModels()
        void get().loadSessions()
        void get().loadWorkspace()
      } catch {
        // No cookie, expired, or the account was suspended — all mean "log in".
        cancelRefresh()
        setAccessToken(null)
        set({ authenticated: false, user: null, authLoading: false })
      } finally {
        inFlight = null
      }
    })()
    return inFlight
  },

  login: async (email, password) => {
    set({ authError: null })
    try {
      const session = await auth.login(email, password)
      setAccessToken(session.accessToken)
      set({ authenticated: true, user: session.user, authLoading: false })
      scheduleRefresh(session.expiresIn, () => void get().bootstrap())
      void get().loadModels()
      void get().loadSessions()
      void get().loadWorkspace()
    } catch (err) {
      set({ authError: err instanceof ApiError ? err.detail : 'network_error' })
      throw err
    }
  },

  /** New accounts land in `pending` and cannot use the app until an admin approves. */
  signup: async (email, password, name) => {
    set({ authError: null })
    try {
      const { user, session } = await auth.signup(email, password, name)
      if (session) {
        // `open` signup mode, or the bootstrap admin — straight in.
        setAccessToken(session.accessToken)
        set({ authenticated: true, user: session.user, authLoading: false })
        scheduleRefresh(session.expiresIn, () => void get().bootstrap())
        void get().loadModels()
      } else {
        // Pending: log in so the waiting screen has an identity to show.
        await get().login(email, password)
        set({ user })
      }
    } catch (err) {
      set({ authError: err instanceof ApiError ? err.detail : 'network_error' })
      throw err
    }
  },

  logout: async () => {
    try {
      await auth.logout()
    } catch {
      // Already gone server-side; the local teardown below is what matters.
    }
    cancelRefresh()
    setAccessToken(null)
    // Invalidates any workspace load still in the air: otherwise a response
    // requested by the previous account repopulates the screen after logout.
    touchWorkspace()
    set({
      authenticated: false,
      user: null,
      activeSessionId: null,
      authError: null,
      // Never leave one account's work on screen for the next.
      sessions: [],
      users: [],
      projects: [],
      artifacts: [],
      skills: [],
      designs: [],
      designTemplates: [],
      promptTemplates: [],
      availableTools: [],
      memories: [],
      agents: [],
      connectors: [],
      connectorCatalog: [],
    })
  },

  refreshMe: async () => {
    try {
      set({ user: await auth.me() })
    } catch (err) {
      if (err instanceof UnauthorizedError) await get().bootstrap()
    }
  },

  updateProfile: async (patch) => {
    set({ user: await auth.updateMe(patch) })
  },
  changePassword: async (currentPassword, newPassword) => {
    await auth.changePassword(currentPassword, newPassword)
  },

  theme: initialTheme,
  toggleTheme: () => {
    const order: Theme[] = ['system', 'light', 'dark']
    const next = order[(order.indexOf(get().theme) + 1) % order.length]
    applyTheme(next)
    set({ theme: next })
  },
  lang: initialLang,
  toggleLang: () => {
    const next: Lang = get().lang === 'ko' ? 'en' : 'ko'
    applyLang(next)
    set({ lang: next })
  },
  // Three columns do not fit under ~1024px; start collapsed there.
  sidebarOpen: window.matchMedia('(min-width: 1024px)').matches,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  // Every slice below starts empty and is filled from the API. Nothing is
  // seeded: a fresh install shows nothing.
  users: [],
  usersLoading: false,
  models: [],
  modelsLoading: false,
  workspaceLoading: true,
  artifactsLoading: true,
  artifactsLoadingMore: false,
  artifactsHasMore: false,
  artifactFilter: {},
  artifactCounts: null,
  sessionsLoading: true,
  workspaceFailed: false,
  artifactsFailed: false,
  sessionsFailed: false,
  litellmAvailable: true,
  autoRouting: {
    enabled: false,
    available: false,
    reason: 'disabled',
    classifierModelId: null,
    economyModelIds: [],
  },

  sessions: [],
  jobs: [],
  projects: [],
  artifacts: [],
  skills: [],
  designs: [],
  designTemplates: [],
  promptTemplates: [],
  availableTools: [],
  memories: [],
  agents: [],
  connectorCatalog: [],

  /**
   * Loads the workspace in one go, straight after sign-in.
   *
   * A partial failure still renders the rest; each screen re-reads its own
   * slice when it mounts.
   */
  loadWorkspace: async () => {
    // Screens refetch on mount, and a mutation can land mid-flight. Applying a
    // snapshot taken before it would drop the row just created.
    const epoch = ++workspaceEpoch
    // Artifacts load through their own action: the gallery asks for the same
    // page as it mounts, and one door means one request rather than two.
    void get().loadArtifacts({})
    const results = await Promise.allSettled([
      projectsApi.list(),
      skillsApi.list(),
      designsApi.list(),
      designTemplatesApi.list(),
      promptTemplatesApi.list(),
      memoryApi.list(),
      agentsApi.list(),
      connectorsApi.list(),
      connectorsApi.catalog(),
      toolsApi.list(),
    ])
    const [
      projects,
      skills,
      designs,
      designTemplates,
      promptTemplates,
      memories,
      agents,
      connectors,
      catalog,
      tools,
    ] = results
    // Something changed under us; that write already holds the truth.
    if (epoch !== workspaceEpoch) return
    set((s) => ({
      workspaceLoading: false,
      // Any endpoint that did not come back leaves part of this screen stale.
      workspaceFailed: results.some((r) => r.status === 'rejected'),
      projects: projects.status === 'fulfilled' ? projects.value.map(toProject) : s.projects,
      skills: skills.status === 'fulfilled' ? skills.value.map(toSkill) : s.skills,
      designs: designs.status === 'fulfilled' ? designs.value : s.designs,
      designTemplates:
        designTemplates.status === 'fulfilled' ? designTemplates.value : s.designTemplates,
      promptTemplates:
        promptTemplates.status === 'fulfilled' ? promptTemplates.value : s.promptTemplates,
      availableTools: tools.status === 'fulfilled' ? tools.value : s.availableTools,
      memories: memories.status === 'fulfilled' ? memories.value.map(toMemory) : s.memories,
      agents: agents.status === 'fulfilled' ? agents.value.map(toAgent) : s.agents,
      connectors:
        connectors.status === 'fulfilled' ? connectors.value.map(toConnector) : s.connectors,
      connectorCatalog: catalog.status === 'fulfilled' ? catalog.value : s.connectorCatalog,
    }))
  },

  usage: null,
  audit: null,
  loadUsage: async (days = 7) => {
    const report = await usageApi.report(days).catch(() => null)
    if (report) set({ usage: report })
  },
  apiKeys: null,
  loadApiKeys: async () => {
    const rows = await keysApi.list().catch(() => null)
    if (rows) set({ apiKeys: rows })
  },
  createApiKey: async (name) => {
    const row = await keysApi.create(name)
    set((s) => ({ apiKeys: [{ ...row, secret: null }, ...(s.apiKeys ?? [])] }))
    // Returned, not stored: the only moment it exists outside the database.
    return row.secret ?? null
  },
  revokeApiKey: async (id) => {
    await keysApi.revoke(id)
    set((s) => ({ apiKeys: (s.apiKeys ?? []).filter((k) => k.id !== id) }))
  },
  governance: null,
  loadGovernance: async () => {
    const policy = await usageApi.governance().catch(() => null)
    if (policy) set({ governance: policy })
  },
  setGovernance: async (patch) => {
    // Optimistic — a switch that lags a round trip feels broken. The reload
    // below is what the screen trusts.
    set((s) => (s.governance ? { governance: { ...s.governance, ...patch } } : s))
    const result = await usageApi.setGovernance(patch).catch(() => null)
    await get().loadGovernance()
    return result?.clearedMessages ?? 0
  },
  saveGovernance: async (patch) => {
    const result = await usageApi.setGovernance(patch)
    await get().loadGovernance()
    return result.clearedMessages
  },
  loadAudit: async () => {
    const rows = await usageApi.audit(200).catch(() => null)
    if (rows) set({ audit: rows })
  },

  loadModels: async () => {
    set({ modelsLoading: true })
    try {
      const { models: live, litellmAvailable, defaultChatModel, autoRouting } =
        await modelsApi.list()
      set((s) => ({
        models: live,
        litellmAvailable,
        autoRouting: autoRouting ?? {
          enabled: false,
          available: false,
          reason: 'disabled',
          classifierModelId: null,
          economyModelIds: [],
        },
        modelsLoading: false,
        modelByKind: reconcileDefaults(s.modelByKind, live, defaultChatModel),
        compareModels: reconcileCompareModels(s.compareModels, live),
      }))
    } catch {
      // Leave whatever is already loaded; the picker keeps working offline.
      set({ modelsLoading: false, litellmAvailable: false })
    }
  },

  loadUsers: async () => {
    set({ usersLoading: true })
    try {
      set({ users: await adminApi.users(), usersLoading: false })
    } catch {
      set({ usersLoading: false })
    }
  },

  activeSessionId: null,
  streaming: false,
  abortStream: null,
  modelByKind: initialModelByKind,
  setModel: (kind, id) =>
    set((s) => {
      const next = { ...s.modelByKind, [kind]: id }
      // Remembered per surface; `reconcileDefaults` drops anything the
      // catalogue has stopped serving.
      localStorage.setItem(MODEL_STORAGE_KEY, JSON.stringify(next))
      return { modelByKind: next }
    }),
  setSessionModel: async (sessionId, modelId) => {
    const previous = get().sessions.find((session) => session.id === sessionId)
    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === sessionId ? { ...c, model: modelId, routingMode: 'manual' } : c,
      ),
    }))
    try {
      await queueSessionPersistence(sessionId, () =>
        sessionsApi
          .update(sessionId, { model: modelId, routingMode: 'manual' })
          .then(() => undefined),
      )
    } catch {
      const current = get().sessions.find((session) => session.id === sessionId)
      if (previous && current?.model === modelId && current.routingMode === 'manual') {
        set((s) => ({
          sessions: s.sessions.map((session) =>
            session.id === sessionId ? previous : session,
          ),
        }))
      }
      void get().loadSessions()
    }
  },
  setSessionRoutingMode: async (sessionId, mode) => {
    const previous = get().sessions.find((session) => session.id === sessionId)?.routingMode
    set((s) => ({
      sessions: s.sessions.map((session) =>
        session.id === sessionId ? { ...session, routingMode: mode } : session,
      ),
    }))
    try {
      await queueSessionPersistence(sessionId, () =>
        sessionsApi.update(sessionId, { routingMode: mode }).then(() => undefined),
      )
    } catch (error) {
      const current = get().sessions.find((session) => session.id === sessionId)
      if (previous && current?.routingMode === mode) {
        set((s) => ({
          sessions: s.sessions.map((session) =>
            session.id === sessionId ? { ...session, routingMode: previous } : session,
          ),
        }))
      }
      void get().loadSessions()
      throw error
    }
  },
  setActiveSession: (id) => {
    const session = id ? get().sessions.find((s) => s.id === id) : null
    set({
      activeSessionId: id,
      openArtifactId: session?.artifactId ?? null,
    })
  },

  loadSessions: async () => {
    try {
      const rows = await sessionsApi.list()
      set((s) => ({
        sessionsLoading: false,
        sessionsFailed: false,
        // The list response carries titles only, so an open transcript is kept.
        sessions: rows.map((row) =>
          toSession(row, s.sessions.find((c) => c.id === row.id)?.messages),
        ),
      }))
    } catch {
      set({ sessionsLoading: false, sessionsFailed: true })
      /* offline: leave the sidebar as it is */
    }
  },

  openSession: async (id) => {
    try {
      const row = await sessionsApi.get(id)
      const session = toSession(row)
      set((s) => ({
        sessions: s.sessions.some((c) => c.id === id)
          ? s.sessions.map((c) => (c.id === id ? session : c))
          : [session, ...s.sessions],
      }))

      /**
       * Artifacts the transcript names by id. Every surface that shows one
       * resolves the id against the store, so arriving by URL — reload, shared
       * link, back button — has to fill it too.
       */
      const wanted = new Set<string>()
      if (session.artifactId) wanted.add(session.artifactId)
      for (const m of session.messages) for (const a of m.artifactIds ?? []) wanted.add(a)
      const missing = [...wanted].filter((a) => !get().artifacts.some((x) => x.id === a))
      if (missing.length === 0) return
      const rows = await Promise.all(missing.map((a) => artifactsApi.get(a).catch(() => null)))
      const found = rows.filter((r) => r !== null).map(toArtifact)
      if (found.length) set((s) => ({ artifacts: [...found, ...s.artifacts] }))
    } catch {
      /* deleted or not ours — the page renders its empty state */
    }
  },

  newSession: async (
    kind,
    { projectId = null, agentId = null, routingMode = 'manual' } = {},
  ) => {
    // Writes projects[].sessionIds, so an earlier workspace snapshot would drop
    // the new chat back out of its project.
    touchWorkspace()
    const row = await sessionsApi.create({
      kind,
      projectId,
      agentId,
      // Left empty for an agent that pins a model, because the agent is the
      // *last* step of the server's precedence: a model here is a model the
      // conversation chose, and it would out-rank the very setting the agent
      // screen prints as a badge. Picking one in the composer afterwards is
      // then a deliberate override rather than something the client did on
      // its own.
      //
      // An agent that pins nothing — which is every seeded one — still needs
      // the screen default sent. Withholding it there would leave the server
      // with no model at all, and its no-model fallback is the cheapest usable
      // row, not the model this screen has been showing all along.
      model: get().agents.find((a) => a.id === agentId)?.model ? null : get().modelByKind[kind],
      routingMode,
    })
    const session = toSession(row, [])
    set((s) => ({
      sessions: [session, ...s.sessions],
      activeSessionId: session.id,
      openArtifactId: null,
      projects: projectId
        ? s.projects.map((p) =>
            p.id === projectId ? { ...p, sessionIds: [session.id, ...p.sessionIds] } : p,
          )
        : s.projects,
    }))
    return session.id
  },

  /**
   * The single entry point for all five surfaces.
   *
   * Chat, report and slides take the streaming-turn path; image and
   * audio/video take the job path.
   */
  send: async (sessionId, kind, text, opts = {}) => {
    const id = sessionId ?? (await get().newSession(kind, { projectId: opts.projectId ?? null }))
    await waitForSessionPersistence(id)
    // A chat can be refused before it becomes a turn. Keep the originating
    // composer visible until the first SSE event; non-chat surfaces navigate
    // as soon as their session exists.
    const acceptSession = () => opts.onSession?.(id)
    if (kind !== 'chat') acceptSession()

    // Snapshot after a new empty session is created but before the optimistic
    // turn. A 4xx means the API rejected before its first Message write, so
    // every local bubble/artifact added for this attempt must be reversible.
    const before = get().sessions.find((session) => session.id === id)
    const beforeArtifactIds = new Set(get().artifacts.map((artifact) => artifact.id))
    const beforeOpenArtifactId = get().openArtifactId
    const now = new Date().toISOString()
    // The conversation's own model wins; the surface default would undo the
    // in-session picker every turn.
    const model = effectiveModelId(
      get().sessions.find((c) => c.id === id),
      kind,
      get().agents,
      get().modelByKind,
    )
    const userMsg: Message = {
      id: uid('m'),
      role: 'user',
      content: text,
      createdAt: now,
      attachments: opts.attachmentNames?.map((name) => ({ name, size: '', type: '' })),
      startedFrom: opts.startingTemplate
        ? { templateId: opts.startingTemplate.id, title: opts.startingTemplate.title }
        : undefined,
    }

    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === id
          // `model` is deliberately not written back. An empty one is how a
          // conversation says it is still deferring to its agent, and stamping
          // the resolved id here would silence that after the first turn.
          ? {
              ...c,
              title: c.messages.length === 0 ? text.slice(0, 40) : c.title,
              updatedAt: now,
              messages: [...c.messages, userMsg],
              // The turn carries the shape, so the row is about to wear it:
              // written here with the same optimism as the bubble above, and
              // the reason the composer can put its pick down at send without
              // the chip blinking out for the length of the turn. A refusal
              // restores `before`, which takes this back with it.
              ...(opts.renderTemplateId ? { renderTemplateId: opts.renderTemplateId } : {}),
            }
          : c,
      ),
    }))

    const perform = async () => {
      // A rendering template replaces the surface's built-in track, exactly as
      // it does on the server — the events are `block`/`page`, not
      // `section`/`slide`, so the runner has to match.
      const usesTemplate = Boolean(
        opts.renderTemplateId ??
          get().sessions.find((session) => session.id === id)?.renderTemplateId,
      )
      if (usesTemplate && (kind === 'report' || kind === 'slides')) {
        await streamPage(
          set,
          get,
          id,
          text,
          model,
          opts.activatedSkillIds,
          opts.renderTemplateId,
          opts.startingTemplate?.id,
        )
        return id
      }

      if (kind === 'report') {
        await streamReport(
          set,
          get,
          id,
          text,
          model,
          opts.activatedSkillIds,
          opts.startingTemplate?.id,
        )
        return id
      }

      if (kind === 'slides') {
        await streamDeck(
          set,
          get,
          id,
          text,
          model,
          opts.activatedSkillIds,
          opts.startingTemplate?.id,
        )
        return id
      }

      if (kind === 'image') {
        // The composer calls `generateImages` directly; this path only catches an
        // image prompt routed through chat.
        await get().generateImages(id, text, { projectId: opts.projectId ?? null })
        return id
      }

      if (kind !== 'chat') {
        // Likewise: the composer calls `generateAudio`/`generateVideo` directly,
        // since those are jobs rather than turns.
        handToTheComposer(set, text)
        return id
      }

      if (get().streaming) return id

      if (get().compareMode && get().compareModels.length >= 2) {
        await runComparison(set, get, id, text, {
          activatedSkillIds: opts.activatedSkillIds,
          startingTemplateId: opts.startingTemplate?.id,
          attachments: opts.attachments,
          attachmentNames: opts.attachmentNames,
          privacyAction: opts.privacyAction,
          privacyDecisionToken: opts.privacyDecisionToken,
          onAccepted: acceptSession,
        })
        return id
      }

      await streamTurn(set, get, id, text, model, {
        webSearch: opts.webSearch,
        attachments: opts.attachments,
        attachmentNames: opts.attachmentNames,
        activatedSkillIds: opts.activatedSkillIds,
        startingTemplateId: opts.startingTemplate?.id,
        privacyAction: opts.privacyAction,
        privacyDecisionToken: opts.privacyDecisionToken,
        onAccepted: acceptSession,
      })
      return id
    }

    try {
      return await perform()
    } catch (err) {
      if (err instanceof PrivacyDecisionError) err.sessionId = id
      if (isClientRefusal(err) && before) {
        set((state) => ({
          // Keep the newly created, empty server session so the restored draft
          // can be retried in place. Existing sessions return to their exact
          // pre-attempt transcript, which prevents duplicate optimistic turns.
          sessions: state.sessions.map((session) =>
            session.id === id ? before : session,
          ),
          artifacts: state.artifacts.filter(
            (artifact) =>
              beforeArtifactIds.has(artifact.id) ||
              artifact.sessionId !== id ||
              !artifact.id.startsWith('a_'),
          ),
          openArtifactId: beforeOpenArtifactId,
        }))
      } else if (kind === 'chat') {
        // Ordinary failures still leave the created session reachable; they are
        // not policy decisions and may already have server-side output.
        acceptSession()
      }
      throw err
    }
  },

  stopStreaming: () => get().abortStream?.(),

  // Optimistic, then persisted.
  setSessionTemplate: async (id, templateId) => {
    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === id ? { ...c, renderTemplateId: templateId } : c,
      ),
    }))
    // `''` is what clears it: `null` on the wire is indistinguishable from a
    // patch that never mentioned the field.
    await sessionsApi
      .update(id, { renderTemplateId: templateId ?? '' })
      .catch(() => get().loadSessions())
  },

  renameSession: async (id, title) => {
    set((s) => ({ sessions: s.sessions.map((c) => (c.id === id ? { ...c, title } : c)) }))
    await sessionsApi.update(id, { title }).catch(() => get().loadSessions())
  },
  retryJob: async (job) => {
    // Only video carries a resolution, which is what selects the producer. The
    // failed row is dropped: two cards for one request read as two charges.
    set((s) => ({ jobs: s.jobs.filter((j) => j.id !== job.id) }))
    const video = Boolean(job.params && 'resolution' in job.params)
    if (video) await get().generateVideo(job.sessionId, job.prompt)
    else await get().generateAudio(job.sessionId, job.prompt)
  },

  generateVideo: async (sessionId, prompt, opts = {}) => {
    const { avOptions, modelByKind } = get()
    let id = sessionId
    /**
     * Opening the session can be refused — surface switched off, or no credit.
     * Inside the `try`, or the rejection escapes and Enter does nothing.
     */
    if (!id) {
      try {
        id = await get().newSession('av', { projectId: opts.projectId ?? null })
      } catch (err) {
        set({ mediaError: errorMessage(err, '영상 작업을 시작하지 못했습니다.') })
        return
      }
      opts.onSession?.(id)
    }
    try {
      const job = await jobsApi.create(id, {
        prompt,
        model: modelByKind.av || undefined,
        resolution: avOptions.resolution,
        seconds: avOptions.durationSec,
        audio: avOptions.withAudio,
        aspect: avOptions.aspect,
      })
      set((s) => ({ jobs: [toJob(job), ...s.jobs.filter((j) => j.id !== job.id)] }))
      void get().followJob(id!, job.id)
    } catch (err) {
      set((s) => ({
        jobs: [
          {
            id: uid('j'),
            sessionId: id!,
            kind: 'av',
            status: 'failed',
            progress: 0,
            stage: '실패',
            creditsUsed: 0,
            creditsEstimated: 0,
            // Carried so the card's retry action has the request to rebuild.
            prompt,
            model: modelByKind.av || '',
            params: {
              resolution: avOptions.resolution,
              seconds: avOptions.durationSec,
              audio: avOptions.withAudio,
            },
            error: err instanceof Error ? err.message : '영상 작업을 시작하지 못했습니다.',
            createdAt: new Date().toISOString(),
            finishedAt: new Date().toISOString(),
          },
          ...s.jobs,
        ],
      }))
    }
  },
  /** Polls one job until it settles. The server does the work; this keeps the
   *  card current and stops when the row stops moving. */
  followJob: async (sessionId, jobId) => {
    for (let i = 0; i < 200; i++) {
      await new Promise((r) => setTimeout(r, 4000))
      const rows = await jobsApi.list(sessionId).catch(() => null)
      if (!rows) continue
      const job = rows.find((j) => j.id === jobId)
      if (!job) return
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === jobId ? toJob(job) : j)),
      }))
      if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'canceled') {
        // The artifact is the point; the card is how it was watched.
        await get().loadArtifacts()
        if (job.artifactId) set({ openArtifactId: job.artifactId })
        return
      }
    }
  },
  generateAudio: async (sessionId, prompt, opts = {}) => {
    const { avOptions, modelByKind, models } = get()
    let id = sessionId
    if (!id) {
      id = await get().newSession('av', { projectId: opts.projectId ?? null })
      opts.onSession?.(id)
    }
    const jobId = uid('j')
    const model = models.find((m) => m.id === modelByKind.av)
    set((s) => ({
      streaming: true,
      jobs: [
        {
          id: jobId,
          sessionId: id,
          kind: 'av' as const,
          status: 'running' as const,
          progress: 0,
          stage: '만드는 중',
          prompt,
          model: modelByKind.av || '',
          params: { seconds: avOptions.durationSec },
          creditsUsed: 0,
          creditsEstimated: model?.creditPerCall || model?.creditCost || 0,
          createdAt: new Date().toISOString(),
          finishedAt: null,
        },
        ...s.jobs,
      ],
    }))
    try {
      const row = await sessionsApi.audio(id, {
        prompt,
        model: modelByKind.av || undefined,
        // Speech or music: there is no third kind.
        audioKind: avOptions.audioKind === 'music' ? 'music' : 'narration',
        // Both were chips on screen that never left the browser: every
        // narration came back in the default voice, and the length picker
        // changed a label and nothing else.
        voice: avOptions.voice,
        seconds: avOptions.durationSec,
      })
      set((s) => ({
        artifacts: [toArtifact(row), ...s.artifacts],
        openArtifactId: row.id,
        jobs: s.jobs.map((j) =>
          j.id === jobId
            ? { ...j, status: 'succeeded' as const, progress: 100, finishedAt: new Date().toISOString() }
            : j,
        ),
      }))
      await get().loadSessions()
    } catch (err) {
      set((s) => ({
        jobs: s.jobs.map((j) =>
          j.id === jobId
            ? {
                ...j,
                status: 'failed' as const,
                finishedAt: new Date().toISOString(),
                error: err instanceof Error ? err.message : '오디오를 만들지 못했습니다.',
              }
            : j,
        ),
      }))
    } finally {
      set({ streaming: false })
    }
  },
  generateImages: async (sessionId, prompt, opts = {}) => {
    const { imageOptions, modelByKind } = get()
    let id = sessionId
    /**
     * Opening the session can be refused — surface switched off, or no credit.
     * Inside the `try`, or the rejection escapes and Enter does nothing.
     */
    if (!id) {
      // Same shape as a chat turn from /new/:kind: the session exists before
      // anything is generated, so a slow model cannot strand the result.
      try {
        id = await get().newSession('image', { projectId: opts.projectId ?? null })
      } catch (err) {
        set({ mediaError: errorMessage(err, '이미지를 만들지 못했습니다.') })
        return
      }
      opts.onSession?.(id)
    }
    const jobId = uid('j')
    set((s) => ({
      streaming: true,
      jobs: [
        {
          id: jobId,
          sessionId: id,
          kind: 'image' as const,
          status: 'running' as const,
          progress: 0,
          stage: '만드는 중',
          prompt,
          model: modelByKind.image || '',
          params: null,
          creditsUsed: 0,
          // Quoted from the catalogue; the charge lands on what the upstream
          // reports.
          creditsEstimated:
            (get().models.find((m) => m.id === modelByKind.image)?.creditPerImage ?? 0) *
            imageOptions.count,
          createdAt: new Date().toISOString(),
          finishedAt: null,
        },
        ...s.jobs,
      ],
    }))
    // An image template is spent on the picture it shaped, not kept on the
    // session: unlike a deck or a document there is no file whose shape it has
    // to keep matching. Which is exactly why it has to be put down here — no
    // session row will ever hold it, so a pick left standing would be carried
    // into the next conversation and shape a picture nobody chose it for.
    const picked = get().pendingTemplate
    const templateId = picked?.kind === 'image' ? picked.id : undefined
    if (picked) set({ pendingTemplate: null })
    try {
      const rows = await sessionsApi.images(id, {
        prompt,
        model: modelByKind.image || undefined,
        aspect: imageOptions.aspect,
        style: imageOptions.style,
        count: imageOptions.count,
        templateId,
      })
      set((s) => ({
        artifacts: [...rows.map(toArtifact), ...s.artifacts],
        openArtifactId: rows.length ? rows[rows.length - 1].id : s.openArtifactId,
        jobs: s.jobs.map((j) =>
          j.id === jobId
            ? { ...j, status: 'succeeded' as const, progress: 100, finishedAt: new Date().toISOString() }
            : j,
        ),
      }))
      // The gallery is the record; the sidebar entry should carry the prompt.
      await get().loadSessions()
    } catch (err) {
      set((s) => ({
        jobs: s.jobs.map((j) =>
          j.id === jobId
            ? {
                ...j,
                status: 'failed' as const,
                finishedAt: new Date().toISOString(),
                error: err instanceof Error ? err.message : '이미지를 만들지 못했습니다.',
              }
            : j,
        ),
      }))
    } finally {
      set({ streaming: false })
    }
  },
  deleteSessions: async (payload) => {
    const { deleted } = await sessionsApi.deleteMany(payload)
    // Refetched, not filtered locally: `all` is resolved server-side.
    await get().loadSessions()
    set((s) => ({
      activeSessionId: null,
      jobs: payload.all ? [] : s.jobs,
    }))
    return deleted
  },
  deleteSession: async (id) => {
    touchWorkspace()
    set((s) => ({
      sessions: s.sessions.filter((c) => c.id !== id),
      jobs: s.jobs.filter((j) => j.sessionId !== id),
      activeSessionId: s.activeSessionId === id ? null : s.activeSessionId,
      projects: s.projects.map((p) => ({
        ...p,
        sessionIds: p.sessionIds.filter((x) => x !== id),
      })),
    }))
    await sessionsApi.remove(id).catch(() => get().loadSessions())
  },
  togglePinSession: async (id) => {
    const pinned = !get().sessions.find((c) => c.id === id)?.pinned
    set((s) => ({ sessions: s.sessions.map((c) => (c.id === id ? { ...c, pinned } : c)) }))
    {
      await sessionsApi.update(id, { pinned }).catch(() => get().loadSessions())
    }
  },
  /**
   * 좋아요 / 싫어요 on one answer.
   *
   * Written through, not held in the tab. A person who marks an answer wrong
   * is saying something about a transcript they will come back to, and a
   * verdict that dies with the page never reaches the reading it was left for.
   *
   * Pressing the lit thumb again withdraws it, which is why `null` travels to
   * the server as a value rather than as a missing field.
   */
  rateMessage: async (sessionId, messageId, rating) => {
    const before =
      get()
        .sessions.find((c) => c.id === sessionId)
        ?.messages.find((m) => m.id === messageId)?.liked ?? null
    const next = before === rating ? null : rating
    const show = (liked: 'up' | 'down' | null) =>
      set((s) => ({
        sessions: s.sessions.map((c) =>
          c.id === sessionId
            ? {
                ...c,
                messages: c.messages.map((m) => (m.id === messageId ? { ...m, liked } : m)),
              }
            : c,
        ),
      }))
    // Lit first: the thumb is the acknowledgement, and waiting a round trip to
    // draw it is what makes a rating feel like it was not taken.
    show(next)
    await sessionsApi.rate(messageId, next).catch(() => show(before))
  },

  compareMode: false,
  compareModels: [],
  toggleCompareMode: () => set((s) => ({ compareMode: !s.compareMode })),
  toggleCompareModel: (id) =>
    set((s) => {
      const has = s.compareModels.includes(id)
      if (has && s.compareModels.length <= 2) return s
      if (!has && s.compareModels.length >= 3) return s
      return {
        compareModels: has
          ? s.compareModels.filter((m) => m !== id)
          : [...s.compareModels, id],
      }
    }),
  chooseVariant: async (sessionId, messageId, model) => {
    // Keeping a column decides this conversation and nothing else. It used to
    // move `modelByKind.chat` as well, so one answer preferred inside a
    // comparison silently became the model every later chat opened on — and,
    // because that write never reached storage, only until the next reload.
    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === sessionId
          ? {
              ...c,
              model,
              messages: c.messages.map((m) =>
                m.id === messageId
                  ? {
                      ...m,
                      model,
                      // The kept answer becomes the turn's content.
                      content: m.variants?.find((v) => v.model === model)?.content ?? m.content,
                      variants: m.variants?.map((v) => ({ ...v, chosen: v.model === model })),
                    }
                  : m,
              ),
            }
          : c,
      ),
    }))
    // Optimistic above, durable here: the choice has to survive a reload,
    // because the next turn's history is built from it.
    await sessionsApi
      .chooseVariant(sessionId, messageId, model)
      .catch(() => get().openSession(sessionId))
  },

  optionTemplate: null,
  setOptionTemplate: (optionTemplate) => set({ optionTemplate }),
  imageOptions: { aspect: '1:1', style: '미니멀', count: 1 },
  //: Every write but the template's own comes from a person turning a chip, so
  //: a write is where the 서식 stops being the author of these values.
  setImageOptions: (patch) =>
    set((s) => ({ imageOptions: { ...s.imageOptions, ...patch }, optionTemplate: null })),
  draft: '',
  setDraft: (draft) => set({ draft }),
  pendingAttachment: null,
  setPendingAttachment: (pendingAttachment) => set({ pendingAttachment }),
  pendingTemplate: null,
  setPendingTemplate: (pendingTemplate) => set({ pendingTemplate }),
  pendingStartingTemplate: null,
  setPendingStartingTemplate: (pendingStartingTemplate) => set({ pendingStartingTemplate }),
  dictationEnabled: false,
  brand: { name: 'KloudChat', logo: '' },
  refreshBrand: async () => {
    const c = await authConfig.get().catch(() => null)
    if (!c?.brand) return
    applyBrand(c.brand)
    set({ brand: c.brand })
  },
  enabledKinds: ['chat', 'report', 'slides'],
  avOptions: {
    mode: 'video',
    aspect: '16:9',
    durationSec: 4,
    audioKind: 'narration',
    voice: 'alloy',
    resolution: '720p',
    withAudio: false,
  },
  setAvOptions: (patch) =>
    set((s) => {
      const avOptions = { ...s.avOptions, ...patch }
      //: As with the image chips, a write is where the 서식 stops being the
      //: author of these values — every one but the template's own is a person
      //: turning a chip.
      const next = { avOptions, optionTemplate: null }
      if (!patch.mode) return next
      // Audio and video share one surface and one remembered model, and the
      // cheapest `av` model is a speech model. The model follows the mode
      // unless the one already chosen suits it.
      //
      // Whenever the mode is named, not only when it changes: 영상 is the mode
      // this surface opens in, so a speech model can be sitting under it
      // without anybody having turned 종류 at all, and a composer that only
      // ever reacted to the turn had no moment to notice.
      const wanted = patch.mode === 'video' ? 'video' : 'audio'
      const current = s.models.find((m) => m.id === s.modelByKind.av)
      if (current?.modality === wanted) return next
      const usable = s.models
        .filter((m) => m.kinds.includes('av') && m.modality === wanted)
        .sort((a, b) => a.creditCost - b.creditCost)
      if (!usable.length) return next
      return { ...next, modelByKind: { ...s.modelByKind, av: usable[0].id } }
    }),
  cancelJob: async (id) => {
    const before = get().jobs.find((j) => j.id === id)
    // Optimistic: the spinner stops on the press, not on the round trip.
    set((s) => ({
      jobs: s.jobs.map((j) =>
        j.id === id
          ? { ...j, status: 'canceled', stage: '취소됨', finishedAt: new Date().toISOString() }
          : j,
      ),
    }))
    // Only a clip is a row on the server — resolution is what says so, the same
    // way retry tells the two apart. A picture or a line of speech is a
    // placeholder for a call still in flight, with nothing there to cancel.
    if (!before?.params || !('resolution' in before.params)) return
    try {
      const row = await jobsApi.cancel(id)
      set((s) => ({ jobs: s.jobs.map((j) => (j.id === id ? toJob(row) : j)) }))
    } catch (err) {
      // The clip is still being made and will still be charged on delivery, so
      // the card goes back to running rather than reading 취소됨 over a job
      // nobody stopped.
      set((s) => ({
        jobs: s.jobs.map((j) => (j.id === id ? before : j)),
        mediaError: errorMessage(err, '작업을 취소하지 못했습니다.'),
      }))
    }
  },

  openArtifactId: null,
  /**
   * Opens the panel on the current document. The store's copy may be from a
   * late reply that landed on a fresher one; a refetch avoids editing text the
   * server no longer holds.
   */
  clearMediaError: () => set({ mediaError: null }),

  openArtifact: (id) => {
    set({ openArtifactId: id })
    if (id) void get().refreshArtifact(id)
  },

  refreshArtifact: async (id) => {
    const row = await artifactsApi.get(id).catch(() => null)
    // Offline or gone: the panel keeps what it had, and saving will refuse
    // rather than overwrite something it cannot see.
    if (!row) return null
    const fresh = toArtifact(row)
    // Inserted when it is not held: a document can be asked for before the
    // listing that would have carried it — the panel opening on a turn that
    // just finished is exactly that case.
    set((s) => ({
      artifacts: s.artifacts.some((a) => a.id === id)
        ? s.artifacts.map((a) => (a.id === id ? fresh : a))
        : [fresh, ...s.artifacts],
    }))
    return fresh
  },

  createProject: async (p) => {
    touchWorkspace()
    const row = await projectsApi.create(p)
    set((s) => ({ projects: [toProject(row), ...s.projects] }))
    return row.id
  },
  updateProject: async (id, patch) => {
    // Optimistic: a textarea that snaps back mid-edit is worse than one that
    // saves a beat late. The epoch is what stops an in-flight list request from
    // causing exactly that.
    touchWorkspace()
    set((s) => ({ projects: s.projects.map((p) => (p.id === id ? { ...p, ...patch } : p)) }))
    const row = await projectsApi.update(id, patch as never).catch(() => null)
    if (row) set((s) => ({ projects: s.projects.map((p) => (p.id === id ? toProject(row) : p)) }))
  },
  deleteProject: async (id) => {
    touchWorkspace()
    set((s) => ({
      projects: s.projects.filter((p) => p.id !== id),
      // The server detaches sessions rather than deleting them; mirror that.
      sessions: s.sessions.map((c) => (c.projectId === id ? { ...c, projectId: null } : c)),
    }))
    await projectsApi.remove(id).catch(() => get().loadWorkspace())
  },

  uploadFile: async (file, opts) => {
    // Epoch guard: stops a fetch that started just before the upload from
    // arriving later and overwriting the new file.
    touchWorkspace()
    const row = await filesApi.upload(file, opts)
    if (opts?.projectId) {
      set((s) => ({
        projects: s.projects.map((p) =>
          p.id === opts.projectId ? { ...p, files: [toProjectFile(row), ...p.files] } : p,
        ),
      }))
    }
    return row
  },
  deleteFile: async (id) => {
    touchWorkspace()
    set((s) => ({
      projects: s.projects.map((p) => ({ ...p, files: p.files.filter((f) => f.id !== id) })),
    }))
    await filesApi.remove(id).catch(() => get().loadWorkspace())
  },

  loadArtifacts: async (filter) => {
    const next = sameFilter(filter ?? get().artifactFilter)
    const key = JSON.stringify(next)
    if (artifactsInFlight === key) return
    artifactsInFlight = key
    const epoch = ++artifactsEpoch
    // A different question, so the old answer is not an answer. Without this
    // the grid keeps the previous kind's cards for the length of a round trip,
    // and they are clickable — a tab that opens something it is not showing.
    if (JSON.stringify(get().artifactFilter) !== key) {
      set({ artifacts: [], artifactsLoading: true, artifactFilter: next })
    }
    const [rows, counts] = await Promise.all([
      artifactsApi.list({ ...next, limit: ARTIFACT_PAGE }).catch(() => null),
      artifactsApi.counts(next.q).catch(() => null),
    ])
    if (artifactsInFlight === key) artifactsInFlight = null
    // A newer fetch has already answered; this one is history.
    if (epoch !== artifactsEpoch) return
    // Lowered either way: a failed fetch is still an answer, and leaving the
    // flag up would spin forever in place of the empty state.
    set((s) => ({
      artifacts: rows ? mergeArtifacts(rows.map(toArtifact), s.artifacts) : s.artifacts,
      artifactFilter: next,
      artifactsHasMore: rows ? rows.length === ARTIFACT_PAGE : s.artifactsHasMore,
      artifactCounts: counts ? counts.counts : s.artifactCounts,
      artifactsLoading: false,
      artifactsFailed: rows === null,
    }))
  },
  /**
   * The next page, from the oldest row on screen.
   *
   * Keyset rather than a page number: the list is ordered by a timestamp that
   * moves when somebody edits, and an offset would skip or repeat rows under
   * the person scrolling.
   */
  loadMoreArtifacts: async () => {
    const held = get().artifacts
    const last = held.at(-1)
    if (!last || get().artifactsLoadingMore) return
    set({ artifactsLoadingMore: true })
    const rows = await artifactsApi
      .list({
        ...get().artifactFilter,
        limit: ARTIFACT_PAGE,
        beforeAt: last.updatedAt,
        beforeId: last.id,
      })
      .catch(() => null)
    set((s) => ({
      artifacts: rows
        ? [
            ...s.artifacts,
            ...mergeArtifacts(rows.map(toArtifact), s.artifacts).filter(
              (row) => !s.artifacts.some((a) => a.id === row.id),
            ),
          ]
        : s.artifacts,
      artifactsHasMore: rows ? rows.length === ARTIFACT_PAGE : s.artifactsHasMore,
      artifactsLoadingMore: false,
    }))
  },
  deleteArtifact: async (id) => {
    touchWorkspace()
    const removed = get().artifacts.find((x) => x.id === id)
    set((s) => ({ artifacts: s.artifacts.filter((a) => a.id !== id) }))
    if (!removed) return
    const restore = () =>
      set((s) => ({
        artifacts: s.artifacts.some((x) => x.id === id) ? s.artifacts : [removed, ...s.artifacts],
        pendingDelete: null,
      }))
    await holdDelete(set, get, {
      label: nameOf(removed),
      restore,
      commit: () => artifactsApi.remove(id).catch(() => get().loadArtifacts()),
    })
  },

  connectors: [],
  updateConnectorEnv: async (id, env) => {
    touchWorkspace()
    const row = await connectorsApi.update(id, { env }).catch(() => null)
    if (row) set((s) => ({ connectors: s.connectors.map((c) => (c.id === id ? toConnector(row) : c)) }))
    // A new key is only worth having if the server can be reached with it.
    await get().syncConnector(id)
  },
  toggleConnector: async (id) => {
    touchWorkspace()
    const enabled = !get().connectors.find((c) => c.id === id)?.enabled
    set((s) => ({ connectors: s.connectors.map((c) => (c.id === id ? { ...c, enabled } : c)) }))
    const row = await connectorsApi.update(id, { enabled }).catch(() => null)
    if (row) set((s) => ({ connectors: s.connectors.map((c) => (c.id === id ? toConnector(row) : c)) }))
  },
  toggleConnectorTool: async (id, tool) => {
    touchWorkspace()
    const current = get()
      .connectors.find((c) => c.id === id)
      ?.tools.find((t) => t.name === tool)
    const row = await connectorsApi.toggleTool(id, tool, !current?.enabled).catch(() => null)
    if (row) set((s) => ({ connectors: s.connectors.map((c) => (c.id === id ? toConnector(row) : c)) }))
  },
  installConnector: async (slug, env) => {
    touchWorkspace()
    // Installing spawns the server to ask what it exposes — a real round trip,
    // so the catalogue entry stays disabled meanwhile.
    const row = await connectorsApi.install(slug, env)
    set((s) => ({
      connectors: [toConnector(row), ...s.connectors.filter((c) => c.id !== row.id)],
      connectorCatalog: s.connectorCatalog.map((e) =>
        e.slug === slug ? { ...e, installed: true } : e,
      ),
    }))
  },
  uninstallConnector: async (id) => {
    touchWorkspace()
    const slug = get().connectors.find((c) => c.id === id)?.slug
    set((s) => ({
      connectors: s.connectors.filter((c) => c.id !== id),
      connectorCatalog: s.connectorCatalog.map((e) =>
        e.slug === slug ? { ...e, installed: false } : e,
      ),
    }))
    await connectorsApi.uninstall(id).catch(() => get().loadWorkspace())
  },
  syncConnector: async (id) => {
    touchWorkspace()
    const row = await connectorsApi.sync(id).catch(() => null)
    if (row) set((s) => ({ connectors: s.connectors.map((c) => (c.id === id ? toConnector(row) : c)) }))
  },
  addCustomConnector: async (c) => {
    touchWorkspace()
    const row = await connectorsApi.addCustom({
      name: c.name,
      transport: c.transport,
      endpoint: c.endpoint,
      auth: c.auth,
      env: c.env,
      description: '직접 등록한 MCP 서버',
    })
    set((s) => ({ connectors: [toConnector(row), ...s.connectors] }))
  },

  toggleSkill: async (id) => {
    touchWorkspace()
    set((s) => ({
      skills: s.skills.map((sk) => (sk.id === id ? { ...sk, enabled: !sk.enabled } : sk)),
    }))
    const row = await skillsApi.toggle(id).catch(() => null)
    if (row) set((s) => ({ skills: s.skills.map((sk) => (sk.id === id ? toSkill(row) : sk)) }))
  },
  upsertSkill: async (skill) => {
    touchWorkspace()
    const payload = {
      name: skill.name,
      description: skill.description,
      whenToUse: skill.whenToUse,
      body: (skill as Skill & { body?: string }).body ?? '',
      kinds: skill.kinds,
      requiredTools: skill.requiredTools,
      enabled: skill.enabled,
    }
    const exists = get().skills.some((s) => s.id === skill.id)
    const row = exists ? await skillsApi.update(skill.id, payload) : await skillsApi.create(payload)
    set((s) => ({
      skills: exists
        ? s.skills.map((x) => (x.id === skill.id ? toSkill(row) : x))
        : [toSkill(row), ...s.skills],
    }))
  },
  deleteSkill: async (id) => {
    touchWorkspace()
    const removed = get().skills.find((x) => x.id === id)
    set((s) => ({ skills: s.skills.filter((sk) => sk.id !== id) }))
    if (!removed) return
    const restore = () =>
      set((s) => ({
        skills: s.skills.some((x) => x.id === id) ? s.skills : [removed, ...s.skills],
        pendingDelete: null,
      }))
    await holdDelete(set, get, {
      label: nameOf(removed),
      restore,
      commit: () => skillsApi.remove(id).catch(() => get().loadWorkspace()),
    })
  },

  upsertMemory: async (m) => {
    touchWorkspace()
    const payload = {
      name: m.name,
      description: m.description,
      type: m.type,
      body: m.body,
      scope: m.scope,
      links: m.links,
      pinned: m.pinned,
    }
    const exists = get().memories.some((x) => x.id === m.id)
    const row = exists ? await memoryApi.update(m.id, payload) : await memoryApi.create(payload)
    set((s) => ({
      memories: exists
        ? s.memories.map((x) => (x.id === m.id ? toMemory(row) : x))
        : [toMemory(row), ...s.memories],
    }))
  },
  deleteMemory: async (id) => {
    touchWorkspace()
    const removed = get().memories.find((x) => x.id === id)
    set((s) => ({ memories: s.memories.filter((m) => m.id !== id) }))
    if (!removed) return
    const restore = () =>
      set((s) => ({
        memories: s.memories.some((x) => x.id === id) ? s.memories : [removed, ...s.memories],
        pendingDelete: null,
      }))
    await holdDelete(set, get, {
      label: nameOf(removed),
      restore,
      commit: () => memoryApi.remove(id).catch(() => get().loadWorkspace()),
    })
  },
  togglePinMemory: async (id) => {
    touchWorkspace()
    set((s) => ({
      memories: s.memories.map((m) => (m.id === id ? { ...m, pinned: !m.pinned } : m)),
    }))
    const row = await memoryApi.pin(id).catch(() => null)
    if (row) set((s) => ({ memories: s.memories.map((m) => (m.id === id ? toMemory(row) : m)) }))
  },

  forkAgent: async (a) => {
    touchWorkspace()
    /*
     * What the copy is, said on the copy.
     *
     * An agent is its prompt plus what it was given to work with, and only the
     * first half can travel: skills are rows in the other person's account, and
     * the knowledge shelf is theirs to grant, not ours to duplicate. Neither
     * omission is visible on the card that comes back — the copy reads as
     * complete and answers differently, which is the worst of both.
     *
     * The sentence goes in the description rather than into a toast because
     * the question it answers ("why is this one worse than the one I tried?")
     * is asked days later, and by then a toast has been gone for days. It is
     * an ordinary editable field: whoever wired the missing pieces up deletes
     * the line, and that is the right way to dismiss it.
     *
     * Translated on the way in rather than on the way out: this is stored text
     * from here on, and the card runs the whole description through `t()` as
     * one string, which a joined sentence is never a key for.
     */
    const note = translate(
      get().lang,
      '스킬과 지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 연결하고 다시 올리세요.',
    )
    // A copy, not a reference: the original's owner keeps editing theirs.
    const row = await agentsApi.create({
      name: `${a.name} 사본`,
      description: a.description ? `${a.description} · ${note}` : note,
      model: a.model,
      systemPrompt: a.systemPrompt,
      tools: a.tools,
      /*
       * Skills as a policy, not as a list of the author's rows.
       *
       * Two of the three states name nothing and so travel intact: null is
       * "whatever you activate this turn", `[]` is "none, ever", and both mean
       * the same thing in any account. A populated allow-list is the one that
       * cannot come — every id in it is a row in the author's workspace, so
       * filtering it against this one could only ever come back empty, and an
       * emptied allow-list is not the residue of a copy that found nothing. It
       * is a refusal. The turn reads it as "never a skill", which is how an
       * imported agent came to turn down every skill its new owner switched
       * on, on every turn, with nothing said anywhere.
       *
       * Inheriting is wider than the curation the author wrote, and that is
       * the trade taken deliberately: the copy is this person's own agent over
       * this person's own skills, so it grants nothing they could not grant
       * themselves in the editor, and a yes they can see beats a no nobody
       * can. The curation is theirs to rebuild under 허용 목록 지정, which is
       * where it was sayable in the first place.
       */
      skillIds: a.skillIds?.length ? null : a.skillIds,
      kinds: a.kinds,
      temperature: a.temperature,
      color: a.color,
      visibility: 'private',
    })
    set((s) => ({ agents: [toAgent(row), ...s.agents] }))
    // Counts how useful the shared one turned out to be.
    await agentsApi.update(a.id, { installs: a.installs + 1 }).catch(() => {})
  },
  upsertAgent: async (a) => {
    touchWorkspace()
    const payload = {
      name: a.name,
      description: a.description,
      model: a.model,
      systemPrompt: a.systemPrompt,
      tools: a.tools,
      skillIds: a.skillIds,
      kinds: a.kinds,
      temperature: a.temperature,
      color: a.color,
      enabled: a.enabled,
      visibility: a.visibility,
    }
    const exists = get().agents.some((x) => x.id === a.id)
    const row = exists ? await agentsApi.update(a.id, payload) : await agentsApi.create(payload)
    set((s) => ({
      agents: exists
        ? s.agents.map((x) => (x.id === a.id ? toAgent(row) : x))
        : [toAgent(row), ...s.agents],
    }))
  },
  deleteAgent: async (id) => {
    touchWorkspace()
    const removed = get().agents.find((x) => x.id === id)
    set((s) => ({ agents: s.agents.filter((a) => a.id !== id) }))
    if (!removed) return
    const restore = () =>
      set((s) => ({
        agents: s.agents.some((x) => x.id === id) ? s.agents : [removed, ...s.agents],
        pendingDelete: null,
      }))
    await holdDelete(set, get, {
      label: nameOf(removed),
      restore,
      commit: () => agentsApi.remove(id).catch(() => get().loadWorkspace()),
    })
  },





  approveUser: (id, monthlyCredits) => applyUserChange(set, adminApi.approve(id, monthlyCredits)),
  rejectUser: (id) => applyUserChange(set, adminApi.reject(id)),
  suspendUser: (id) => applyUserChange(set, adminApi.suspend(id)),
  reinstateUser: (id) => applyUserChange(set, adminApi.reinstate(id)),
  rotateLitellmKey: (id) => applyUserChange(set, adminApi.rotateLitellmKey(id)),
  setUserModels: (id, models) => applyUserChange(set, adminApi.setUserModels(id, models)),
  removeUser: async (id) => {
    await adminApi.removeUser(id)
    set((s) => ({ users: s.users.filter((u) => u.id !== id) }))
  },
  setUserCredits: (id, monthlyCredits) =>
    applyUserChange(set, adminApi.setCredits(id, monthlyCredits)),
}))

/**
 * Admin mutations answer with the updated row, so the table patches itself from
 * the response rather than refetching, and a self-edit stays in sync.
 */
async function applyUserChange(set: Set, pending: Promise<User>) {
  const updated = await pending
  set((s) => ({
    users: s.users.map((u) => (u.id === updated.id ? updated : u)),
    user: s.user?.id === updated.id ? updated : s.user,
  }))
}

/* ── helpers ────────────────────────────────────────────────────────────
 * Out of the store body so `send` stays readable. They take set/get explicitly
 * rather than closing over them.
 */

type Set = (u: Partial<State> | ((s: State) => Partial<State>)) => void
type Get = () => State

/**
 * Fan the prompt out to the selected models and stream each column
 * independently, so a slow model does not hold up a fast one.
 */
/**
 * Server row → the shape components read.
 *
 * Stream `step` events carry no `type`, and stored attachments carry only a
 * key; both are filled in here.
 */
/** The UI's step categories. Anything else is a tool call. */
const STEP_TYPES = new Set<Step['type']>(['thinking', 'tool', 'artifact'])

/**
 * A stream that stops without closing. Every turn ends with a `usage` event, or
 * an explicit error; a dropped connection sends neither and the loop falls out
 * of `for await` with nothing to catch. Marks the turn as cut off.
 */
const CUT_OFF = '연결이 끊겨 답변이 중간에 멈췄습니다. 다시 시도해 주세요.'

/**
 * Delete held for a few seconds before it is sent. The row leaves the screen at
 * once and undo cancels the call before anything is destroyed — no server-side
 * retention needed.
 *
 * The window is short so nobody relies on it, and leaving the page flushes what
 * is pending rather than dropping it.
 */
const UNDO_MS = 6_000

/** Deletes whose requests have not been sent yet, so the page can flush them. */
const pendingDeletes = new Set<() => void>()

if (typeof window !== 'undefined') {
  // Leaving with a delete still held would silently undo it — the row is back
  // on the next load and nobody asked for that.
  window.addEventListener('beforeunload', () => {
    for (const flush of pendingDeletes) flush()
    pendingDeletes.clear()
  })
}

/** Whatever the row calls itself: artifacts have titles, the rest have names. */
function nameOf(row: { title?: string; name?: string }): string {
  return row.title ?? row.name ?? ''
}

type HeldDelete = { label: string; restore: () => void; commit: () => Promise<unknown> }

/** Removes it from the screen now, sends the request in a few seconds. */
async function holdDelete(
  set: (partial: Partial<State> | ((s: State) => Partial<State>)) => void,
  get: () => State,
  { label, restore, commit }: HeldDelete,
) {
  let cancelled = false
  const send = () => {
    pendingDeletes.delete(send)
    if (cancelled) return
    void commit()
  }
  pendingDeletes.add(send)
  const timer = window.setTimeout(send, UNDO_MS)

  set({
    pendingDelete: {
      label,
      undo: () => {
        cancelled = true
        window.clearTimeout(timer)
        pendingDeletes.delete(send)
        restore()
      },
    },
  })

  // The banner clears itself once the window has passed, whether or not this
  // is still the delete it was showing.
  window.setTimeout(() => {
    if (get().pendingDelete?.label === label) set({ pendingDelete: null })
  }, UNDO_MS)
}


function toStep(raw: Record<string, unknown>): Step {
  // `raw.type` is the stream event kind — always "step" — not `Step.type`, the
  // UI category that picks an icon. Only known categories are honoured;
  // anything else is a tool call. A step the server categorises itself — what
  // the turn was given, before it did any work — sends `category` alongside,
  // because the envelope has already spent `type` on the event name.
  const category = (raw.category ?? raw.type) as Step['type']
  const step: Step = {
    id: String(raw.id ?? uid('step')),
    type: STEP_TYPES.has(category) ? category : 'tool',
    label: String(raw.label ?? ''),
    status: (raw.status as Step['status']) ?? 'done',
    detail: raw.detail as string | undefined,
    // Carried through rather than dropped: a step that knows it is the third
    // of seven is the only thing on the surface that knows how much is left.
    progress: raw.progress as Step['progress'],
    skills: raw.skills as Step['skills'],
    memories: raw.memories as Step['memories'],
    files: raw.files as Step['files'],
    memoriesWritten: raw.memoriesWritten as number | undefined,
    totalMemories: raw.totalMemories as number | undefined,
    estimatedTokens: raw.estimatedTokens as number | undefined,
  }
  // The server writes these lines in Korean because it also stores them on the
  // message, where the document runners read them back. Rewritten here from
  // the numbers and names it sent alongside, so an English reader gets the
  // same line rather than the one the row happens to hold — the same thing
  // `appliedSkillsStep` does for the skills step.
  return { ...step, ...retold(step) }
}

/** How many names a line prints before it starts counting instead. */
const NAMES_SHOWN = 6

function named(names: string[], more: '외 {n}건' | '외 {n}개'): string {
  const shown = names.slice(0, NAMES_SHOWN).join(' · ')
  return names.length > NAMES_SHOWN
    ? `${shown} ${tr(more).replace('{n}', String(names.length - NAMES_SHOWN))}`
    : shown
}

function retold(step: Step): Partial<Step> {
  if (step.memoriesWritten !== undefined) {
    return {
      label: tr('메모리 {n}건 저장').replace('{n}', String(step.memoriesWritten)),
      detail: tr('자동 메모리에 추가됨'),
    }
  }
  if (step.memories) {
    const detail = named(step.memories, '외 {n}건')
    return {
      label: tr('메모리 {n}건 참고').replace('{n}', String(step.memories.length)),
      detail:
        step.totalMemories && step.totalMemories > step.memories.length
          ? `${detail} · ${tr('저장된 {total}건 중 최근 {n}건')
              .replace('{total}', String(step.totalMemories))
              .replace('{n}', String(step.memories.length))}`
          : detail,
    }
  }
  if (step.files) {
    const subject = step.id === 'context-knowledge' ? tr('프로젝트 지식') : tr('첨부 파일')
    const short = step.files.filter((file) => file.state !== 'included')
    // Cut and dropped are counted apart: a document that arrived at half
    // length and one that never arrived are different things to have been
    // answered without.
    const cut = short.filter((file) => file.state === 'truncated').length
    const dropped = short.length - cut
    const fates = [
      ...(cut ? [tr('{n}개 잘림').replace('{n}', String(cut))] : []),
      ...(dropped ? [tr('{n}개 빠짐').replace('{n}', String(dropped))] : []),
    ]
    const note = (file: NonNullable<Step['files']>[number]) =>
      file.state === 'truncated'
        ? tr('{name} {kept}자만 반영')
            .replace('{name}', file.name)
            .replace('{kept}', file.keptChars.toLocaleString())
        : tr(file.state === 'omitted' ? '{name} 분량을 넘겨 제외' : '{name} 읽지 못함').replace(
            '{name}',
            file.name,
          )
    return {
      label: fates.length
        ? `${tr('{subject} {n}개 중').replace('{subject}', subject).replace('{n}', String(step.files.length))} ${fates.join(', ')}`
        : tr('{subject} {n}개 반영')
            .replace('{subject}', subject)
            .replace('{n}', String(step.files.length)),
      detail: named(
        short.length ? short.map(note) : step.files.map((file) => file.name),
        '외 {n}개',
      ),
    }
  }
  return {}
}

function appliedSkillsStep(event: {
  skills: {
    id: string
    name: string
    catalogKey: string | null
    estimatedTokens: number
  }[]
  estimatedTokens: number
}): Step {
  return {
    id: 'skills-applied',
    type: 'thinking',
    label: tr('스킬 {n}개 적용').replace('{n}', String(event.skills.length)),
    status: 'done',
    detail: `${event.skills.map((skill) => tr(skill.name)).join(' · ')} · ${tr('약 {n} 토큰').replace(
      '{n}',
      event.estimatedTokens.toLocaleString(),
    )}`,
    skills: event.skills,
    estimatedTokens: event.estimatedTokens,
  }
}

function upsertStep(steps: Step[] | undefined, step: Step): Step[] {
  const current = steps ?? []
  return current.some((item) => item.id === step.id)
    ? current.map((item) => (item.id === step.id ? step : item))
    : [...current, step]
}

function toMessage(raw: MessageRow): Message {
  return {
    id: raw.id,
    role: raw.role,
    content: raw.content,
    createdAt: raw.createdAt,
    model: raw.model ?? undefined,
    routing: raw.routing ?? undefined,
    steps: raw.steps?.map((s) => toStep(s as Record<string, unknown>)),
    attachments: raw.attachments?.map((a) =>
      typeof a === 'string'
        ? { name: a, size: '', type: '' }
        : (a as { name: string; size: string; type: string }),
    ),
    usage: raw.usage ?? undefined,
    startedFrom: raw.startedFrom ?? undefined,
    liked: raw.rating ?? null,
    // A comparison turn stores columns rather than one body, so a reload has to
    // rebuild them.
    variants: raw.variants?.map((v) => ({
      model: v.model,
      routedModel: v.routedModel,
      actualModel: v.actualModel,
      dataBoundary: v.dataBoundary,
      content: v.content,
      status: v.error ? ('error' as const) : ('done' as const),
      chosen: v.chosen ?? false,
      usage: v.usage
        ? { ...v.usage, credits: v.credits }
        : { inputTokens: 0, outputTokens: 0, credits: v.credits },
    })),
  }
}

function toSession(raw: SessionRow, keepMessages?: Message[]): Session {
  return {
    id: raw.id,
    kind: raw.kind,
    title: raw.title || tr('새 작업'),
    projectId: raw.projectId,
    agentId: raw.agentId,
    model: raw.model,
    routingMode: raw.routingMode ?? 'manual',
    artifactId: raw.artifactId,
    renderTemplateId: raw.renderTemplateId ?? null,
    pinned: raw.pinned,
    createdAt: raw.createdAt,
    updatedAt: raw.updatedAt,
    messages: raw.messages ? raw.messages.map(toMessage) : (keepMessages ?? []),
    preview: raw.preview,
    messageCount: raw.messageCount,
  }
}

/* ── workspace mappers ───────────────────────────────────────────────────
 * Wire format and component props are separate contracts; these bridge the two
 * rather than reshaping either.
 */

const bytes = (n: number) =>
  n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`

function toProjectFile(f: FileRow): ProjectFile {
  return {
    id: f.id,
    name: f.name,
    size: bytes(f.size),
    type: f.mime || f.name.split('.').pop() || '',
    addedAt: f.createdAt,
    tokens: f.tokens,
  }
}

function toProject(p: ProjectRow): Project {
  return {
    id: p.id,
    name: p.name,
    description: p.description,
    emoji: p.emoji,
    instructions: p.instructions,
    files: p.files.map(toProjectFile),
    sessionIds: p.sessionIds,
    skillIds: p.skillIds,
    designSystemId: p.designSystemId ?? null,
    renderTemplates: p.renderTemplates ?? {},
    updatedAt: p.updatedAt,
  }
}

/**
 * `data` holds the kind-specific body (report sections, deck slides, …), which
 * the union in types.ts carries at the top level — so it is spread, not nested.
 */
/** Wire row → the shape the job card renders. */
function toJob(row: JobRow): Job {
  return {
    id: row.id,
    sessionId: row.sessionId,
    kind: (row.kind as Job['kind']) ?? 'av',
    status: row.status,
    progress: row.progress,
    stage: row.stage,
    creditsUsed: row.creditsUsed,
    creditsEstimated: row.creditsEstimated,
    error: row.error ?? undefined,
    createdAt: row.createdAt,
    finishedAt: row.finishedAt,
    prompt: row.prompt ?? '',
    model: row.model ?? '',
    params: row.params ?? null,
  }
}

function toArtifact(a: ArtifactRow): Artifact {
  return {
    id: a.id,
    title: a.title,
    version: a.version,
    createdAt: a.createdAt,
    updatedAt: a.updatedAt,
    sessionId: a.sessionId,
    projectId: a.projectId,
    kind: a.kind,
    ...(a.data ?? {}),
    // After the spread: a body that happens to carry the key must not decide
    // whether the row is a card.
    partial: a.partial === true,
  } as Artifact
}

/**
 * A page of cards laid over what the store already holds.
 *
 * A listing row carries a trimmed body, so taking it wholesale would blank the
 * document a panel is showing — or worse, hand an editor a truncated copy to
 * save. The fuller copy wins while it is the same version; anything newer on
 * the server replaces it.
 */
function mergeArtifacts(incoming: Artifact[], held: Artifact[]): Artifact[] {
  const byId = new Map(held.map((a) => [a.id, a]))
  return incoming.map((row) => {
    const mine = byId.get(row.id)
    if (!row.partial || !mine || mine.partial) return row
    // The body stays; the facts come from the row. Spreading the row over it
    // would put the card's empty `content` back on top of the document. When
    // the server has moved on, the body is stale — say so, and whatever needs
    // it next will fetch it.
    return {
      ...mine,
      title: row.title,
      version: row.version,
      updatedAt: row.updatedAt,
      sessionId: row.sessionId,
      projectId: row.projectId,
      partial: mine.version !== row.version,
    } as Artifact
  })
}

function toSkill(s: SkillRow): Skill {
  return {
    id: s.id,
    name: s.name,
    slug: s.slug,
    description: s.description,
    whenToUse: s.whenToUse,
    body: s.body ?? '',
    catalogKey: s.catalogKey ?? null,
    // During a rolling deployment the web bundle can briefly see a pre-0018
    // API row. Treat absent catalogue metadata as the least-capable legacy
    // shape instead of crashing the whole composer.
    requiredTools: s.requiredTools ?? [],
    estimatedTokens: s.estimatedTokens ?? 0,
    source: (s.source as Skill['source']) ?? 'personal',
    kinds: (s.kinds ?? []) as SessionKind[],
    enabled: s.enabled,
    version: s.version,
    files: ['SKILL.md'],
    updatedAt: s.updatedAt,
  }
}

function toMemory(m: MemoryRow): MemoryEntry {
  return {
    id: m.id,
    name: m.name,
    description: m.description,
    type: m.type as MemoryEntry['type'],
    body: m.body,
    scope: m.scope,
    links: m.links,
    pinned: m.pinned,
    updatedAt: m.updatedAt,
  }
}

function toAgent(a: AgentRow): Agent {
  return {
    ownerId: a.ownerId,
    ownerName: a.ownerName,
    id: a.id,
    name: a.name,
    slug: a.slug,
    description: a.description,
    model: a.model,
    systemPrompt: a.systemPrompt,
    tools: a.tools,
    skillIds: a.skillIds,
    kinds: a.kinds as SessionKind[],
    temperature: a.temperature,
    color: a.color,
    enabled: a.enabled,
    visibility: a.visibility as Agent['visibility'],
    installs: a.installs,
    runs: a.runs,
    hasKnowledge: a.hasKnowledge,
    updatedAt: a.updatedAt,
  }
}

/** Catalog entries have no id until installed; the row id is what the UI keys on. */
function toConnector(c: ConnectorRow): Connector {
  return {
    id: c.id,
    name: c.name,
    slug: c.slug,
    description: c.description,
    category: c.category,
    transport: c.transport as Connector['transport'],
    endpoint: c.endpoint,
    auth: c.auth as Connector['auth'],
    status: c.status as Connector['status'],
    installed: c.installed,
    enabled: c.enabled,
    kinds: c.kinds as SessionKind[],
    tools: c.tools.map((t) => ({
      name: t.name,
      description: t.description,
      readOnly: t.readOnly,
      enabled: t.enabled,
    })),
    envKeys: c.envKeys ?? [],
    official: c.official,
    icon: '🔌',
    color: '#6b7280',
    lastSyncAt: c.lastSyncAt,
    error: c.error ?? undefined,
  }
}

/**
 * One conversational turn.
 *
 * An empty message is created and filled in place as events arrive. `stop`
 * aborts the request only; the server still stores what it had produced.
 */
async function streamTurn(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  model: string,
  opts: {
    webSearch?: boolean
    attachments?: string[]
    attachmentNames?: string[]
    activatedSkillIds?: string[]
    startingTemplateId?: string
    privacyAction?: PrivacyAction
    privacyDecisionToken?: string
    onAccepted?: () => void
  } = {},
) {
  const assistantId = uid('m')
  const controller = new AbortController()

  set((s) => ({
    streaming: true,
    abortStream: () => controller.abort(),
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            messages: [
              ...c.messages,
              {
                id: assistantId,
                role: 'assistant',
                content: '',
                model,
                createdAt: new Date().toISOString(),
                steps: [],
              } as Message,
            ],
          }
        : c,
    ),
  }))

  const patch = (fn: (m: Message) => Message) =>
    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === sessionId
          ? { ...c, messages: c.messages.map((m) => (m.id === assistantId ? fn(m) : m)) }
          : c,
      ),
    }))

  // With streaming off the transport still streams; the text is buffered and
  // shown in one piece. Steps stay live — progress is not half an answer.
  const live = get().user?.preferences.streamResponses !== false
  let buffered = ''

  //: Whether the turn ended on purpose. See CUT_OFF.
  let settled = false
  let accepted = false

  try {
    for await (const event of streamSession(
      sessionId,
      {
        content: text,
        webSearch: opts.webSearch,
        attachments: opts.attachments,
        activatedSkillIds: opts.activatedSkillIds,
        startingTemplateId: opts.startingTemplateId,
        privacyAction: opts.privacyAction,
        privacyDecisionToken: opts.privacyDecisionToken,
      },
      controller.signal,
    )) {
      if (!accepted) {
        accepted = true
        opts.onAccepted?.()
      }
      switch (event.type) {
        case 'privacy_route':
          if ('findingCounts' in event && event.findingCounts?.length) {
            const { type: _type, ...routing } = event
            markPrompt(set, sessionId, routing)
          }
          patch((m) => ({
            ...m,
            model:
              'actualModel' in event && event.actualModel
                ? event.actualModel
                : 'effectiveModels' in event
                  ? event.effectiveModels[0]
                  : m.model,
            routing:
              'effectiveModels' in event
                ? { ...event, costRouting: m.routing?.costRouting ?? event.costRouting }
                : m.routing
                  ? {
                      ...m.routing,
                      initialAction: m.routing.initialAction ?? m.routing.action,
                      action: event.action,
                      toolOutputMasked: event.count,
                    }
                  : m.routing,
          }))
          break
        case 'model_route': {
          // Provider routing also uses this name internally. Only the public
          // adaptive-routing event carries this complete, user-facing shape.
          if (!event.decision || !event.requestedModel || !event.selectedModel) break
          const { type: _type, ...costRouting } = event
          patch((m) => ({
            ...m,
            model: event.executedModel ?? event.selectedModel,
            routing: m.routing
              ? { ...m.routing, costRouting }
              : {
                  requestedModels: [event.requestedModel],
                  routedModels: [event.selectedModel],
                  effectiveModels: [event.selectedModel],
                  actualModels: event.executedModel ? [event.executedModel] : [],
                  actualModel: event.executedModel,
                  action: 'none',
                  dataBoundary: 'unknown',
                  costRouting,
                },
          }))
          break
        }
        case 'delta':
          if (live) patch((m) => ({ ...m, content: m.content + event.text }))
          else buffered += event.text
          break
        case 'skills_applied':
          patch((m) => ({ ...m, steps: upsertStep(m.steps, appliedSkillsStep(event)) }))
          break
        case 'artifact':
          // Load it and open the panel, as opening a session with an artifact
          // does.
          void get()
            .loadArtifacts()
            .then(() => set({ openArtifactId: event.artifactId }))
          break
        case 'step':
          patch((m) => {
            const step = toStep(event as unknown as Record<string, unknown>)
            const steps = m.steps ?? []
            const at = steps.findIndex((s) => s.id === step.id)
            return {
              ...m,
              steps: at >= 0 ? steps.map((s, i) => (i === at ? step : s)) : [...steps, step],
            }
          })
          break
        case 'usage':
          settled = true
          patch((m) => ({
            ...m,
            usage: {
              inputTokens: event.inputTokens,
              outputTokens: event.outputTokens,
              credits: event.credits,
            },
          }))
          if (event.credits) chargeCredits(set, get, event.credits)
          break
        case 'title':
          set((s) => ({
            sessions: s.sessions.map((c) =>
              c.id === sessionId ? { ...c, title: event.title } : c,
            ),
          }))
          break
        case 'error':
          // Content is left alone: a partial answer beats none.
          settled = true
          patch((m) => ({ ...m, error: event.message }))
          break
      }
    }
  } catch (err) {
    // Abort is the stop button doing its job, not a failure.
    if (err instanceof PrivacyDecisionError) {
      settled = true
      set((s) => ({
        sessions: s.sessions.map((c) =>
          c.id === sessionId
            ? { ...c, messages: c.messages.filter((m) => m.id !== assistantId) }
            : c,
        ),
      }))
      throw err
    } else if (err instanceof DOMException && err.name === 'AbortError') {
      settled = true
    } else if (isClientRefusal(err)) {
      settled = true
      patch((m) => ({ ...m, error: errorMessage(err, tr('요청을 처리하지 못했습니다.')) }))
      // HTTP refusal means the server did not store the turn. Let the
      // composer restore its draft, attachments, and one-turn skill choice.
      throw err
    } else {
      settled = true
      patch((m) => ({ ...m, error: '응답을 받지 못했습니다. 잠시 후 다시 시도하세요.' }))
      throw err
    }
  } finally {
    // Buffered text lands here, including on abort or error.
    if (!live && buffered) patch((m) => ({ ...m, content: buffered }))
    if (!settled) patch((m) => ({ ...m, error: CUT_OFF }))
    set({ streaming: false, abortStream: null })
    // Ordering, pinning, and the generated title all live server-side.
    void get().loadSessions()
  }
}

/**
 * Sends one prompt to several models and stores the result as a single turn.
 *
 * The columns arrive interleaved on one connection, so the merged stream is
 * reduced here. Billing is counted by the server.
 */
async function runComparison(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  opts: {
    activatedSkillIds?: string[]
    startingTemplateId?: string
    attachments?: string[]
    attachmentNames?: string[]
    privacyAction?: PrivacyAction
    privacyDecisionToken?: string
    onAccepted?: () => void
  } = {},
) {
  const models = get().compareModels
  const assistantId = uid('m')
  const variants: Variant[] = models.map((model) => ({ model, content: '', status: 'streaming' }))

  set((s) => ({
    streaming: true,
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            messages: [
              ...c.messages,
              {
                id: assistantId,
                role: 'assistant' as const,
                content: '',
                createdAt: new Date().toISOString(),
                variants,
              },
            ],
          }
        : c,
    ),
  }))

  const patch = (model: string, next: Partial<Variant>) =>
    set((s) => ({
      sessions: s.sessions.map((c) =>
        c.id === sessionId
          ? {
              ...c,
              messages: c.messages.map((m) =>
                m.id === assistantId
                  ? { ...m, variants: m.variants?.map((v) => (v.model === model ? { ...v, ...next } : v)) }
                  : m,
              ),
            }
          : c,
      ),
    }))

  const controller = new AbortController()
  set({ abortStream: () => controller.abort() })
  let accepted = false
  try {
    for await (const e of streamComparison(
      sessionId,
      {
        content: text,
        models,
        activatedSkillIds: opts.activatedSkillIds,
        startingTemplateId: opts.startingTemplateId,
        attachments: opts.attachments,
        privacyAction: opts.privacyAction,
        privacyDecisionToken: opts.privacyDecisionToken,
      },
      controller.signal,
    )) {
      if (!accepted) {
        accepted = true
        opts.onAccepted?.()
      }
      if (e.type === 'privacy_route') {
        if ('effectiveModels' in e) {
          if (e.findingCounts?.length) {
            const { type: _type, ...routing } = e
            markPrompt(set, sessionId, routing)
          }
          set((s) => ({
            sessions: s.sessions.map((c) =>
              c.id === sessionId
                ? {
                    ...c,
                    messages: c.messages.map((m) =>
                      m.id === assistantId
                        ? {
                            ...m,
                            routing: e,
                            variants: e.effectiveModels.map((model) => {
                              const existing = m.variants?.find((v) => v.model === model)
                              const route = e.modelRoutes?.find(
                                (candidate) => candidate.routedModel === model,
                              )
                              return {
                                model,
                                content: existing?.content ?? '',
                                status: existing?.status ?? ('streaming' as const),
                                usage: existing?.usage,
                                routedModel: model,
                                actualModel: route?.actualModel ?? existing?.actualModel,
                                dataBoundary: route?.dataBoundary ?? existing?.dataBoundary,
                              }
                            }),
                          }
                        : m,
                    ),
                  }
                : c,
            ),
          }))
        }
      } else if (e.type === 'variant') {
        const current = get()
          .sessions.find((c) => c.id === sessionId)
          ?.messages.find((m) => m.id === assistantId)
          ?.variants?.find((v) => v.model === e.model)
        patch(e.model, { content: (current?.content ?? '') + e.text })
      } else if (e.type === 'variant_done') {
        patch(e.model, {
          routedModel: e.routedModel ?? e.model,
          actualModel: e.actualModel,
          status: e.error ? 'error' : 'done',
          usage: {
            inputTokens: e.inputTokens,
            outputTokens: e.outputTokens,
            credits: e.credits,
          },
        })
      } else if (e.type === 'done') {
        chargeCredits(set, get, e.credits ?? 0)
      } else if (e.type === 'skills_applied') {
        patchMessage(set, sessionId, assistantId, (message) => ({
          ...message,
          steps: upsertStep(message.steps, appliedSkillsStep(e)),
        }))
      } else if (e.type === 'step') {
        // A comparison is answered from the same memories and the same
        // attachments as a single-model turn, and it spends several times the
        // credits doing it — so it says what it was given, too.
        patchMessage(set, sessionId, assistantId, (message) => ({
          ...message,
          steps: upsertStep(message.steps, toStep(e as unknown as Record<string, unknown>)),
        }))
      }
    }
  } catch (err) {
    if (err instanceof PrivacyDecisionError) {
      set((s) => ({
        sessions: s.sessions.map((c) =>
          c.id === sessionId
            ? { ...c, messages: c.messages.filter((m) => m.id !== assistantId) }
            : c,
        ),
      }))
      throw err
    }
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      for (const m of models) patch(m, { status: 'error' })
      throw err
    }
    if (isClientRefusal(err)) throw err
  } finally {
    set({ streaming: false, abortStream: null })
    void get().loadSessions()
  }
}

/**
 * A report turn.
 *
 * The table of contents arrives first as empty headings, fixing the progress
 * denominator from the start; sections fill in after. The artifact is built
 * locally while streaming and swapped for the server's copy at the end.
 */
async function streamReport(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  model: string,
  activatedSkillIds?: string[],
  startingTemplateId?: string,
) {
  const draftId = uid('a')
  const assistantId = uid('m')
  const now = new Date().toISOString()

  const draft: Artifact = {
    id: draftId,
    kind: 'report',
    title: text.slice(0, 60),
    version: 1,
    createdAt: now,
    updatedAt: now,
    sessionId,
    projectId: get().sessions.find((s) => s.id === sessionId)?.projectId ?? null,
    sections: [],
    sources: [],
    citationStyle: 'APA',
    wordCount: 0,
  }

  set((s) => ({
    streaming: true,
    artifacts: [draft, ...s.artifacts],
    openArtifactId: draftId,
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            artifactId: draftId,
            messages: [
              ...c.messages,
              { id: assistantId, role: 'assistant' as const, content: '', createdAt: now },
            ],
          }
        : c,
    ),
  }))

  const patchReport = (fn: (a: ReportArtifact) => ReportArtifact) =>
    set((s) => ({
      artifacts: s.artifacts.map((a) =>
        a.id === draftId && a.kind === 'report' ? fn(a) : a,
      ),
    }))

  const controller = new AbortController()
  set({ abortStream: () => controller.abort() })
  //: Whether the turn ended on purpose. See CUT_OFF.
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      { content: text, model, activatedSkillIds, startingTemplateId },
      controller.signal,
    )) {
      switch (e.type) {
        case 'skills_applied':
          patchMessage(set, sessionId, assistantId, (message) => ({
            ...message,
            steps: upsertStep(message.steps, appliedSkillsStep(e)),
          }))
          break
        // The outline names the document; until then the draft carries the
        // request, which is a prompt rather than a title.
        case 'title':
          patchReport((a) => ({ ...a, title: e.title }))
          break
        // The shelf arrives before the first section, so the 출처 tab is
        // populated by the time there is prose citing it.
        case 'sources':
          patchReport((a) => ({ ...a, sources: e.sources }))
          break
        case 'section':
          patchReport((a) => {
            const at = a.sections.findIndex((x) => x.id === e.sectionId)
            const next: ReportSection = {
              id: e.sectionId,
              heading: e.heading,
              level: 1,
              status: e.done ? 'done' : 'pending',
              content: e.content,
            }
            const sections =
              at >= 0
                ? a.sections.map((x, i) => (i === at ? next : x))
                : [...a.sections, next]
            return {
              ...a,
              sections,
              wordCount: sections.reduce((n, x) => n + x.content.split(/\s+/).filter(Boolean).length, 0),
            }
          })
          break
        case 'step':
          set((s) => ({
            sessions: s.sessions.map((c) =>
              c.id === sessionId
                ? {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== assistantId) return m
                      const step = toStep(e as unknown as Record<string, unknown>)
                      const steps = m.steps ?? []
                      const at = steps.findIndex((x) => x.id === step.id)
                      return {
                        ...m,
                        steps: at >= 0 ? steps.map((x, i) => (i === at ? step : x)) : [...steps, step],
                      }
                    }),
                  }
                : c,
            ),
          }))
          break
        case 'usage':
          settled = true
          chargeCredits(set, get, e.credits)
          break
        case 'error':
          settled = true
          patchMessage(set, sessionId, assistantId, (m) => ({ ...m, content: e.message }))
          break
        case 'artifact':
          // The server's copy supersedes the draft: same content, real id, and
          // the version history hangs off it. Fetched as well as listed — a
          // listing row carries a card, and the panel needs the document.
          await Promise.all([get().loadArtifacts(), get().refreshArtifact(e.artifactId)])
          set((s) => ({
            artifacts: s.artifacts.filter((a) => a.id !== draftId),
            openArtifactId: e.artifactId,
            sessions: s.sessions.map((c) =>
              c.id === sessionId ? { ...c, artifactId: e.artifactId } : c,
            ),
          }))
          break
      }
    }
  } catch (err) {
    settled = true
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      patchMessage(set, sessionId, assistantId, (m) => ({
        ...m,
        error: errorMessage(err, '보고서를 만들지 못했습니다.'),
      }))
    }
    if (isClientRefusal(err)) throw err
  } finally {
    if (!settled) patchMessage(set, sessionId, assistantId, (m) => ({ ...m, error: CUT_OFF }))
    set({ streaming: false, abortStream: null })
    void get().loadSessions()
  }
}

/**
 * A turn that writes into a rendering template.
 *
 * The draft is an `html` artifact from the first event, so the panel shows the
 * template's own shape filling in rather than a blank frame — the blocks
 * arrive one at a time and are stitched into a preview until the server sends
 * the real file.
 */
async function streamPage(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  model: string,
  activatedSkillIds?: string[],
  renderTemplateId?: string,
  startingTemplateId?: string,
) {
  const draftId = uid('a')
  const assistantId = uid('m')
  const now = new Date().toISOString()

  const draft: Artifact = {
    id: draftId,
    kind: 'html',
    title: text.slice(0, 60),
    version: 1,
    createdAt: now,
    updatedAt: now,
    sessionId,
    projectId: get().sessions.find((s) => s.id === sessionId)?.projectId ?? null,
    language: 'html',
    content: '',
  }

  set((s) => ({
    streaming: true,
    artifacts: [draft, ...s.artifacts],
    openArtifactId: draftId,
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            artifactId: draftId,
            messages: [
              ...c.messages,
              { id: assistantId, role: 'assistant' as const, content: '', createdAt: now },
            ],
          }
        : c,
    ),
  }))

  const patchPage = (fn: (a: CodeArtifact) => CodeArtifact) =>
    set((s) => ({
      artifacts: s.artifacts.map((a) => (a.id === draftId && a.kind === 'html' ? fn(a) : a)),
    }))

  // What has been written so far, in order. Kept beside the artifact rather
  // than parsed back out of its markup: the finished file is the server's, and
  // this is only the scaffold that stands until it arrives.
  const written = new Map<string, string>()
  const order: string[] = []

  const controller = new AbortController()
  set({ abortStream: () => controller.abort() })
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      { content: text, model, activatedSkillIds, renderTemplateId, startingTemplateId },
      controller.signal,
    )) {
      switch (e.type) {
        case 'skills_applied':
          patchMessage(set, sessionId, assistantId, (message) => ({
            ...message,
            steps: upsertStep(message.steps, appliedSkillsStep(e)),
          }))
          break
        case 'title':
          patchPage((a) => ({ ...a, title: e.title }))
          break
        case 'block': {
          if (!order.includes(e.block.title)) order.push(e.block.title)
          written.set(e.block.title, e.block.html)
          // A plain scaffold, not the template's CSS: pretending to be the
          // finished design while the blocks are still arriving would make the
          // real file look like a change of mind when it lands.
          const body = order
            .map((title) => {
              const html = written.get(title) ?? ''
              return `<section><h2>${escapeHtml(title)}</h2>${html || '<p class="pending">…</p>'}</section>`
            })
            .join('\n')
          patchPage((a) => ({ ...a, content: draftDocument(body) }))
          break
        }
        case 'page':
          patchPage((a) => ({ ...a, content: e.html }))
          break
        case 'step':
          patchMessage(set, sessionId, assistantId, (m) => ({
            ...m,
            steps: upsertStep(m.steps, toStep(e as unknown as Record<string, unknown>)),
          }))
          break
        case 'usage':
          settled = true
          chargeCredits(set, get, e.credits)
          break
        case 'error':
          settled = true
          patchMessage(set, sessionId, assistantId, (m) => ({ ...m, content: e.message }))
          break
        case 'artifact':
          // The document itself, not the listing's card of it: the panel is
          // about to open on this and its controls are the blocks.
          await Promise.all([get().loadArtifacts(), get().refreshArtifact(e.artifactId)])
          set((s) => ({
            artifacts: s.artifacts.filter((a) => a.id !== draftId),
            openArtifactId: e.artifactId,
            sessions: s.sessions.map((c) =>
              c.id === sessionId ? { ...c, artifactId: e.artifactId } : c,
            ),
          }))
          break
      }
    }
  } catch (err) {
    settled = true
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      patchMessage(set, sessionId, assistantId, (m) => ({
        ...m,
        error: errorMessage(err, tr('문서를 만들지 못했습니다.')),
      }))
    }
    if (isClientRefusal(err)) throw err
  } finally {
    if (!settled) patchMessage(set, sessionId, assistantId, (m) => ({ ...m, error: CUT_OFF }))
    set({ streaming: false, abortStream: null })
    void get().loadSessions()
  }
}

const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** The holding page a template turn shows while its blocks are still arriving. */
const draftDocument = (body: string) =>
  `<!doctype html><html lang="ko"><head><meta charset="utf-8" />` +
  `<style>body{margin:0;padding:2rem 2.4rem;font-family:system-ui,sans-serif;` +
  `line-height:1.6;color:#1a1a1a}section{margin:0 0 1.6rem}` +
  `h2{font-size:1.1rem;margin:0 0 .4rem}.pending{color:#9aa0a6}</style>` +
  `</head><body>${body}</body></html>`

/**
 * A slides turn. Slides fill into a local draft, which is replaced by the
 * server's copy when the turn ends.
 */
async function streamDeck(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  model: string,
  activatedSkillIds?: string[],
  startingTemplateId?: string,
) {
  const draftId = uid('a')
  const assistantId = uid('m')
  const now = new Date().toISOString()

  const draft: Artifact = {
    id: draftId,
    kind: 'deck',
    title: text.slice(0, 60),
    version: 1,
    createdAt: now,
    updatedAt: now,
    sessionId,
    projectId: get().sessions.find((s) => s.id === sessionId)?.projectId ?? null,
    theme: '기본',
    slides: [],
  }

  set((s) => ({
    streaming: true,
    artifacts: [draft, ...s.artifacts],
    openArtifactId: draftId,
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            artifactId: draftId,
            messages: [
              ...c.messages,
              { id: assistantId, role: 'assistant' as const, content: '', createdAt: now },
            ],
          }
        : c,
    ),
  }))

  const patchDeck = (fn: (a: DeckArtifact) => DeckArtifact) =>
    set((s) => ({
      artifacts: s.artifacts.map((a) => (a.id === draftId && a.kind === 'deck' ? fn(a) : a)),
    }))

  const controller = new AbortController()
  set({ abortStream: () => controller.abort() })
  //: Whether the turn ended on purpose. See CUT_OFF.
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      { content: text, model, activatedSkillIds, startingTemplateId },
      controller.signal,
    )) {
      switch (e.type) {
        case 'skills_applied':
          patchMessage(set, sessionId, assistantId, (message) => ({
            ...message,
            steps: upsertStep(message.steps, appliedSkillsStep(e)),
          }))
          break
        // The outline step names the deck. Until it lands the draft carries the
        // request itself, which is a prompt rather than a title.
        case 'title':
          patchDeck((a) => ({ ...a, title: e.title }))
          break
        case 'slide':
          patchDeck((a) => {
            const at = a.slides.findIndex((x) => x.id === e.slide.id)
            return {
              ...a,
              slides:
                at >= 0
                  ? a.slides.map((x, i) => (i === at ? e.slide : x))
                  : [...a.slides, e.slide],
            }
          })
          break
        case 'step':
          set((s) => ({
            sessions: s.sessions.map((c) =>
              c.id === sessionId
                ? {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== assistantId) return m
                      const step = toStep(e as unknown as Record<string, unknown>)
                      const steps = m.steps ?? []
                      const at = steps.findIndex((x) => x.id === step.id)
                      return {
                        ...m,
                        steps:
                          at >= 0 ? steps.map((x, i) => (i === at ? step : x)) : [...steps, step],
                      }
                    }),
                  }
                : c,
            ),
          }))
          break
        case 'usage':
          settled = true
          chargeCredits(set, get, e.credits)
          break
        case 'error':
          settled = true
          patchMessage(set, sessionId, assistantId, (m) => ({ ...m, content: e.message }))
          break
        case 'artifact':
          // The document itself, not the listing's card of it: the panel is
          // about to open on this and its controls are the blocks.
          await Promise.all([get().loadArtifacts(), get().refreshArtifact(e.artifactId)])
          set((s) => ({
            artifacts: s.artifacts.filter((a) => a.id !== draftId),
            openArtifactId: e.artifactId,
            sessions: s.sessions.map((c) =>
              c.id === sessionId ? { ...c, artifactId: e.artifactId } : c,
            ),
          }))
          break
      }
    }
  } catch (err) {
    settled = true
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      patchMessage(set, sessionId, assistantId, (m) => ({
        ...m,
        error: errorMessage(err, '슬라이드를 만들지 못했습니다.'),
      }))
    }
    if (isClientRefusal(err)) throw err
  } finally {
    if (!settled) patchMessage(set, sessionId, assistantId, (m) => ({ ...m, error: CUT_OFF }))
    set({ streaming: false, abortStream: null })
    void get().loadSessions()
  }
}

/**
 * Puts the turn's privacy routing on the prompt it was decided for.
 *
 * The server writes a detected message masked and keeps the same routing
 * beside it, so this is what a reload would show anyway. Doing it while the
 * turn is still running is what lets the transcript admit the substitution
 * now, instead of at a reopening that may be a week away.
 */
function markPrompt(set: Set, sessionId: string, routing: PrivacyRouting) {
  set((s) => ({
    sessions: s.sessions.map((c) => {
      if (c.id !== sessionId) return c
      const at = c.messages.map((m) => m.role).lastIndexOf('user')
      if (at < 0) return c
      return { ...c, messages: c.messages.map((m, i) => (i === at ? { ...m, routing } : m)) }
    }),
  }))
}

function patchMessage(set: Set, sessionId: string, messageId: string, fn: (m: Message) => Message) {
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? { ...c, messages: c.messages.map((m) => (m.id === messageId ? fn(m) : m)) }
        : c,
    ),
  }))
}

/** Deduct on completion. Nothing is held up front, so failures cost nothing. */
function chargeCredits(set: Set, _get: Get, credits: number) {
  set((s) => ({
    user: s.user ? { ...s.user, creditsUsed: s.user.creditsUsed + credits } : s.user,
  }))
}
