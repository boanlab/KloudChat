import { create } from 'zustand'
import { applyBrand } from '@/lib/brand'
import { errorMessage } from '@/lib/api'
import {
  ApiError,
  PrivacyDecisionError,
  StreamStalledError,
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
  StoreSkillRow,
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
  PendingPlan,
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
  StoreSkill,
  ToolCatalogEntry,
  User,
} from '@/types'
import { uid } from '@/lib/utils'
import { currentLang, translate, type Lang } from '@/lib/i18n'

/** The store is not a component and cannot use hooks, so only strings that
 *  reach the screen are translated here. */
const tr = (text: string) => translate(currentLang(), text)

type Theme = 'light' | 'dark' | 'system'
export type SidebarMode = 'full' | 'rail' | 'hidden'

/** A client refusal is returned before send/compare writes its first Message. */
const isClientRefusal = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status >= 400 && error.status < 500

/**
 * Per-conversation PATCH queue for model and routing-mode changes. A send
 * waits for the latest one, or a quick Enter after choosing Auto reaches the
 * server while the conversation is still manual.
 */
const sessionPersistence = new Map<string, Promise<void>>()

/**
 * Clips followed by a poll loop in this tab.
 *
 * A clip is followed by whoever started it and again by whoever opens the
 * conversation; without this both loops spend rate limit on the same row.
 */
const followedJobs = new Set<string>()

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

const noop = () => {}

/** Marks a session as having a turn in flight, with the abort for that turn. */
function beginRun(set: Set, sessionId: string, abort: () => void = noop) {
  set((s) => ({ running: { ...s.running, [sessionId]: abort } }))
}

/** The turn is over, however it ended; other sessions' runs are untouched. */
function endRun(set: Set, sessionId: string) {
  set((s) => {
    if (!(sessionId in s.running)) return {}
    const { [sessionId]: _done, ...rest } = s.running
    return { running: rest }
  })
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
  /**
   * Why the last session ended, when it did not end by somebody pressing
   * 로그아웃. The sign-in screen says so rather than looking like an ordinary
   * visit: a person who walked away and came back to a login form deserves to
   * know it was the timeout and not a fault.
   */
  signedOutReason: 'idle' | null
  /** Minutes of inactivity this instance allows. 0 is off. */
  idleTimeoutMinutes: number
  bootstrap: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  signup: (email: string, password: string, name: string) => Promise<void>
  logout: (reason?: 'idle') => Promise<void>
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
  sidebar: SidebarMode
  /** full → rail → hidden → full. On a narrow layout there is no rail. */
  cycleSidebar: () => void

  // ── data ──────────────────────────────────────────────────────────────
  users: User[]
  sessions: Session[]
  jobs: Job[]
  projects: Project[]
  artifacts: Artifact[]
  skills: Skill[]
  /**
   * The workspace store: shared skills this account has not taken yet.
   *
   * Loaded on its own rather than with the workspace. Every screen pays for
   * `loadWorkspace`, and only one of them opens a store.
   */
  skillStore: StoreSkill[]
  skillStoreLoading: boolean
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
  /**
   * Sessions with a turn in flight, keyed by id, each holding the abort for
   * its own stream. Per session rather than one flag: with a single boolean,
   * every conversation looked busy while any one of them generated — a caret
   * after a finished answer, a stop button where send belonged — and that
   * stop went to whichever session was on screen, not the one running.
   */
  running: Record<string, () => void>
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
       * Write the outline waiting on this session rather than planning another.
       *
       * What 이대로 생성 sends. Everything else — an answer, a note, a plain
       * sentence — plans again, so what finally gets written is always
       * something somebody looked at first.
       */
      approve?: boolean
      /** Answers to a stopped turn's questions, keyed by question id. */
      answers?: Record<string, string>
      /**
       * The failed question to run again in place, by its message id — what
       * 다시 시도 sends. The bubble is not repeated and whatever failed under
       * it is replaced; a plain send appends. Chat turns only: the document
       * surfaces have their own retry paths.
       */
      retryOf?: string
      /**
       * Run this one turn on a named model, whatever the conversation is set
       * to. What 다른 모델로 다시 생성 sends: a turn that failed on the model
       * the session carries is the moment somebody wants to try another one,
       * and making them change the picker first — then change it back — is
       * three steps for one question.
       */
      model?: string
            /**
             * Called as soon as a session id exists. Waiting for the stream to
             * finish would lose the conversation on a refresh mid-answer.
             */
      onSession?: (id: string) => void
    },
  ) => Promise<string>
  stopStreaming: (sessionId: string) => void
  renameSession: (id: string, title: string) => Promise<void>
    /**
     * Puts a rendering template on a session, or takes it off.
     *
     * The turn makes it sticky server-side, so clearing the chip has to reach
     * the row too.
     */
  setSessionTemplate: (id: string, templateId: string | null) => Promise<void>
  /**
   * Files an existing conversation into a project, or takes it out of one.
   *
   * A project could only ever be filled by starting work inside it. Anything
   * begun in the ordinary way — which is how work begins — could not be moved
   * in afterwards, so using a project meant doing it all again from scratch,
   * and the feature went unused for the most ordinary reason there is.
   */
  moveSessionToProject: (id: string, projectId: string | null) => Promise<void>
  deleteSession: (id: string) => Promise<void>
  /** Bulk removal from the history screen. Returns how many the server removed. */
  deleteSessions: (payload: {
    ids?: string[]
    all?: boolean
    /** Delete what they produced too. Off unless the reader asked. */
    artifacts?: boolean
  }) => Promise<number>
  /** Polls one job until it settles. */
  followJob: (sessionId: string, jobId: string) => Promise<void>
  /** Starts a video and follows it to completion. */
  generateVideo: (
    sessionId: string | null,
    prompt: string,
    opts?: { projectId?: string | null; onSession?: (id: string) => void },
  ) => Promise<void>
  /** Runs a failed clip's request again. Nothing was charged for the failure. */
  retryJob: (job: Job) => Promise<void>
  /** Sends a failed picture or narration prompt again, as a second turn. */
  retryMediaTurn: (sessionId: string, prompt: string) => Promise<void>
  /** Generates one sound clip on the a/v surface. */
  generateAudio: (
    sessionId: string | null,
    prompt: string,
    opts?: { projectId?: string | null; onSession?: (id: string) => void },
  ) => Promise<void>
  /** Generates pictures on the image surface, as one turn in the conversation. */
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
     * The 서식 whose defaults the option chips still show, if any.
     *
     * A media 서식 leaves no chip: it is spent on the sentence and on these
     * values, which are one workspace-wide preference rather than a property of
     * the session. Any hand-made change to an option drops the name.
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
   * A refused turn, handed back to whichever composer is on screen now.
   *
   * Submit clears the composer optimistically and, when the turn creates a
   * session, moves the person from the start screen to the conversation — two
   * different screens, so the composer that sent the turn is unmounted before
   * the refusal arrives. Restoring through its own setters put the draft and
   * the uploads back into a component nobody was looking at any more, and the
   * work was gone with no error shown either.
   *
   * Addressed to a session rather than broadcast: a refusal belongs to the
   * conversation it was sent to, not to whatever is open when it lands.
   */
  composerRestore: {
    sessionId: string | null
    value: string
    attachments: FileRow[]
    activatedSkillIds: string[]
    startingTemplate: StartingPoint | null
    error: string
  } | null
  setComposerRestore: (restore: State['composerRestore']) => void
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
  /**
   * Several at once. No undo window, unlike the single deletes: a list of
   * twelve is a decision somebody made deliberately behind a confirm, and a
   * held delete would leave the screen disagreeing with the server for as
   * long as it lasted.
   */
  deleteMany: (
    kind: 'projects' | 'artifacts' | 'skills' | 'agents' | 'designs' | 'connectors',
    ids: string[],
  ) => Promise<number>
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
  loadSkillStore: () => Promise<void>
  /** Copies a shared skill into your own workspace. */
  installSkill: (id: string) => Promise<void>
  upsertMemory: (m: MemoryEntry) => Promise<void>
  deleteMemory: (id: string) => Promise<void>
  togglePinMemory: (id: string) => Promise<void>
  upsertAgent: (a: Agent) => Promise<void>
  /** Copies someone else's shared agent, and the skills it runs on, into yours. */
  installAgent: (a: Agent) => Promise<void>
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
 * Which artifact fetch is current. `loadArtifacts` and `loadWorkspace` fill
 * the same list, and without this the later *reply* wins over the later
 * *request* — a stale snapshot that surfaces as a phantom edit conflict.
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
 * The same filter written the same way. `{}` and `{ kind: undefined, q: '' }`
 * mean one thing and hash to two.
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
 * resolved colour instead opts the app out of the OS setting permanently.
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
 * Signs an abandoned browser out.
 *
 * The silent refresh above runs on a timer whether or not anybody is at the
 * keyboard, so on its own it keeps a session alive indefinitely — which on a
 * lab or library PC is the next person opening the previous person's
 * conversations. Idleness is a fact only the browser has, so the browser is
 * what enforces it: the instance policy names a number of minutes and this
 * ends the session when nothing has happened for that long.
 *
 * `pointerdown`/`keydown`/`scroll` rather than `mousemove`: a nudged desk
 * should not count as somebody being there. A tab returning to the foreground
 * does count — that is a person coming back — and the clock is re-checked on
 * the way in, because a laptop asleep for an hour fires no timer at all.
 */
let idleTimer: ReturnType<typeof setTimeout> | null = null
let idleTeardown: (() => void) | null = null

function armIdleWatch(minutes: number, onIdle: () => void) {
  disarmIdleWatch()
  if (minutes <= 0) return
  const limitMs = minutes * 60_000
  let lastSeen = Date.now()

  const fire = () => {
    disarmIdleWatch()
    onIdle()
  }
  const restart = () => {
    lastSeen = Date.now()
    if (idleTimer) clearTimeout(idleTimer)
    idleTimer = setTimeout(fire, limitMs)
  }
  const onWake = () => {
    // Timers do not run while the machine is suspended, so coming back is when
    // the elapsed time is actually measured.
    if (Date.now() - lastSeen >= limitMs) fire()
    else restart()
  }

  const events = ['pointerdown', 'keydown', 'scroll'] as const
  for (const name of events) window.addEventListener(name, restart, { passive: true })
  document.addEventListener('visibilitychange', onWake)
  window.addEventListener('focus', onWake)
  restart()

  idleTeardown = () => {
    for (const name of events) window.removeEventListener(name, restart)
    document.removeEventListener('visibilitychange', onWake)
    window.removeEventListener('focus', onWake)
  }
}

function disarmIdleWatch() {
  if (idleTimer) clearTimeout(idleTimer)
  idleTimer = null
  idleTeardown?.()
  idleTeardown = null
}

/**
 * What to say when a conversational turn arrives on a job surface. Duration and
 * voice come from the composer's controls, so this points at them rather than
 * imitating an answer.
 */
function handToTheComposer(set: Set, text: string) {
    // A clip and a piece of audio are started from the composer, where their
    // length, resolution and voice are chosen — so a prompt arriving here is
    // handed back to it rather than answered. No assistant message is invented:
    // a made-up reply is a worse way to say "not here" than moving the sentence.
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
 * The API's precedence minus the turn override, which no screen knows in
 * advance: conversation, then agent. A conversation opened against an agent
 * carries no model of its own until somebody picks one.
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
  signedOutReason: null,
  idleTimeoutMinutes: 0,

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
              idleTimeoutMinutes: c.idleTimeoutMinutes ?? 0,
            })
            // Armed here rather than at login: a reload re-enters through
            // bootstrap, and a tab left open across one is the case the policy
            // exists for.
            armIdleWatch(c.idleTimeoutMinutes ?? 0, () => void get().logout('idle'))
          })
          .catch(() => {})
        scheduleRefresh(session.expiresIn, () => void get().bootstrap())
        void get().loadModels()
        void get().loadSessions()
        void get().loadWorkspace()
      } catch {
        // No cookie, expired, or the account was suspended — all mean "log in".
        cancelRefresh()
        disarmIdleWatch()
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
      set({ authenticated: true, user: session.user, authLoading: false, signedOutReason: null })
      scheduleRefresh(session.expiresIn, () => void get().bootstrap())
      armIdleWatch(get().idleTimeoutMinutes, () => void get().logout('idle'))
      // Only for an account that can use them. `/models`, `/sessions` and the
      // workspace are all gated on `active`, so a pending account asking is a
      // guaranteed refusal — and one of those refusals used to leave the
      // picker calling the catalogue partial for the rest of the session.
      if (session.user.status === 'active') {
        void get().loadModels()
        void get().loadSessions()
        void get().loadWorkspace()
      }
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

  logout: async (reason) => {
    try {
      await auth.logout()
    } catch {
      // Already gone server-side; the local teardown below is what matters.
    }
    cancelRefresh()
    disarmIdleWatch()
    setAccessToken(null)
    // Invalidates any workspace load still in the air: otherwise a response
    // requested by the previous account repopulates the screen after logout.
    touchWorkspace()
    set({
      authenticated: false,
      user: null,
      activeSessionId: null,
      authError: null,
      signedOutReason: reason ?? null,
      // Never leave one account's work on screen for the next.
      sessions: [],
      users: [],
      projects: [],
      artifacts: [],
      skills: [],
      skillStore: [],
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
      const before = get().user?.status
      const user = await auth.me()
      set({ user })
      // The waiting screen polls this, so this is the moment an approval
      // becomes true for a tab that has been sitting on it. Nothing else runs
      // then, and the screen it advances to needs a workspace to draw.
      if (before !== 'active' && user.status === 'active') {
        void get().loadModels()
        void get().loadSessions()
        void get().loadWorkspace()
      }
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
  // Three columns do not fit under ~1024px; start hidden there.
  sidebar: window.matchMedia('(min-width: 1024px)').matches ? 'full' : 'hidden',
  cycleSidebar: () =>
    set((s) => {
      // A 64px rail on a phone is a column of icons over the content it is
      // covering, so the narrow layout keeps the two states it can use.
      const order: SidebarMode[] = window.matchMedia('(min-width: 1024px)').matches
        ? ['full', 'rail', 'hidden']
        : ['full', 'hidden']
      const at = order.indexOf(s.sidebar)
      return { sidebar: order[(at + 1) % order.length] ?? 'full' }
    }),

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
    qualityAvailable: false,
    qualityReason: 'disabled',
    qualityModelIds: [],
  },

  sessions: [],
  jobs: [],
  projects: [],
  artifacts: [],
  skills: [],
  skillStore: [],
  skillStoreLoading: false,
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
          qualityAvailable: false,
          qualityReason: 'disabled',
          qualityModelIds: [],
        },
        modelsLoading: false,
        modelByKind: reconcileDefaults(s.modelByKind, live, defaultChatModel),
        compareModels: reconcileCompareModels(s.compareModels, live),
      }))
    } catch {
      // Leave whatever is already loaded; the picker keeps working offline.
      //
      // And leave the flag alone when there is a catalogue to leave. It says
      // whether the *list* is complete, not whether *this request* worked: a
      // refresh that fails behind a list the server already sent in full does
      // not make that list partial, and saying so put "일부 모델만 고를 수
      // 있습니다" above every model the instance has.
      set((s) => ({
        modelsLoading: false,
        litellmAvailable: s.models.length > 0 ? s.litellmAvailable : false,
      }))
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
  running: {},
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
        /**
         * A document opens beside its conversation; a picture does not.
         *
         * A picture, clip or piece of speech is in the transcript at a readable
         * size, so opening the panel would show it twice and squeeze the
         * conversation to a third. It stays one click away.
         *
         * Sessions with no messages are the exception — they predate the recording
         * and have a result but no turn to show it in.
         */
    const shownInTranscript =
      (session?.kind === 'image' || session?.kind === 'av') && session.messageCount > 0
    const artifactId = shownInTranscript ? null : (session?.artifactId ?? null)
    set((state) => ({
      activeSessionId: id,
      openArtifactId: artifactId,
      // Comparison belongs to the conversation somebody turned it on in.
      // Held globally it followed them everywhere: switch to another chat and
      // every question there is silently answered by three models, at three
      // times the cost, with no chip on screen until the composer renders.
      compareMode: id === state.activeSessionId ? state.compareMode : false,
    }))
        // Opening a panel means fetching the document. The listing carries cards:
        // a report's card has empty `sources` and sections cut to 400 characters,
        // so a panel drawn from one contradicts itself. Whole copies are kept.
    const held = artifactId ? get().artifacts.find((a) => a.id === artifactId) : null
    if (artifactId && (!held || held.partial)) void get().refreshArtifact(artifactId)
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
             * Whatever is still being made in it.
             *
             * A clip's card is a server row, so a reload or another machine has to
             * fetch it — otherwise the prompt sits alone while credits are spent.
             */
      const jobRows = await jobsApi.list(id).catch(() => null)
      if (jobRows) {
        set((s) => ({
          jobs: [
            ...jobRows.map(toJob),
            ...s.jobs.filter((j) => !jobRows.some((row) => row.id === j.id)),
          ],
        }))
        for (const job of jobRows) {
          if (job.status === 'running' || job.status === 'queued') void get().followJob(id, job.id)
        }
      }

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
            // Left empty for an agent that pins a model: the agent is the *last*
            // step of the server's precedence, and a model here would out-rank the
            // setting the agent screen prints as a badge.
            //
            // An agent that pins nothing still needs the screen default — the
            // server's no-model fallback is the cheapest usable row, not this one.
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
    const model =
      opts.model ??
      effectiveModelId(
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

    // A retry keeps the question where it is and drops what failed under it;
    // everything else appends. Falls back to appending if the row is not on
    // screen, so a stale id never loses a sentence.
    const retryOf =
      kind === 'chat' && opts.retryOf && before?.messages.some((m) => m.id === opts.retryOf)
        ? opts.retryOf
        : undefined
    const rerun = (messages: Message[]): Message[] => {
      const at = messages.findIndex((m) => m.id === retryOf)
      return [
        ...messages.slice(0, at),
        { ...messages[at], failure: undefined, error: undefined },
      ]
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
              messages: retryOf ? rerun(c.messages) : [...c.messages, userMsg],
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
          { approve: opts.approve, answers: opts.answers },
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
          { approve: opts.approve, answers: opts.answers },
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
          { approve: opts.approve, answers: opts.answers },
        )
        return id
      }

      if (kind === 'image') {
        // The composer calls `generateImages` directly; this path only catches
        // an image prompt routed through chat. That call puts up the turn
        // itself, so the optimistic bubble above comes back off rather than
        // standing there twice.
        dropMediaTurn(set, id, userMsg.id)
        await get().generateImages(id, text, { projectId: opts.projectId ?? null })
        return id
      }

      if (kind !== 'chat') {
        // Likewise: the composer calls `generateAudio`/`generateVideo` directly,
        // since those are jobs rather than turns.
        handToTheComposer(set, text)
        return id
      }

      if (get().running[id]) return id

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
        model: opts.model,
        retryOf,
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

  stopStreaming: (sessionId) => {
    // Said out loud before the socket closes. The server cannot tell 중단 from
    // a closed tab, and it must: a reader who navigates away still wants the
    // answer when they come back, and a reader who pressed this does not.
    // Addressed to the session that is running, not the one on screen: the
    // two differ whenever somebody opens another conversation mid-turn.
    const abort = get().running[sessionId]
    if (!abort) return
    void sessionsApi.stop(sessionId).catch(() => undefined)
    abort()
  },

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

  moveSessionToProject: async (id, projectId) => {
    // Optimistic, like every other session patch here: the row moves between
    // lists at once and a failed call reloads the truth.
    set((s) => ({
      sessions: s.sessions.map((c) => (c.id === id ? { ...c, projectId } : c)),
    }))
    await sessionsApi.update(id, { projectId }).catch(() => get().loadSessions())
  },

  renameSession: async (id, title) => {
    set((s) => ({ sessions: s.sessions.map((c) => (c.id === id ? { ...c, title } : c)) }))
    await sessionsApi.update(id, { title }).catch(() => get().loadSessions())
  },
  retryJob: async (job) => {
    // A job is a clip and nothing else now: a picture and a piece of speech
    // come back inside the call that asked for them, and their turn carries
    // its own way to try again. The failed row is dropped, because two cards
    // for one request read as two charges.
    set((s) => ({ jobs: s.jobs.filter((j) => j.id !== job.id) }))
    await get().generateVideo(job.sessionId, job.prompt)
  },

  retryMediaTurn: async (sessionId, prompt) => {
    // Sent again rather than repaired in place. The first attempt is a real
    // thing that happened and stays in the conversation saying so, the same
    // way asking a chat question twice leaves two questions.
    const kind = get().sessions.find((c) => c.id === sessionId)?.kind
    if (kind === 'image') await get().generateImages(sessionId, prompt)
    else if (kind === 'av') await get().generateAudio(sessionId, prompt)
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
        /**
         * The prompt goes up before the request leaves, as a chat turn's does: a
         * clip takes minutes, and the sentence and the card under it are all
         * there is to look at.
         *
         * A refusal takes it back off — nothing was accepted, so nothing was
         * written.
         */
    const { promptId } = beginMediaTurn(set, id, prompt, false)
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
      dropMediaTurn(set, id!, promptId)
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
            error: errorMessage(err, tr('영상 작업을 시작하지 못했습니다.')),
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
    // One loop per clip. Opening the conversation picks up whatever is still
    // running in it, which in the tab that started the clip is the loop
    // already watching it — and two loops polling one job spend rate limit to
    // learn the same thing twice.
    if (followedJobs.has(jobId)) return
    followedJobs.add(jobId)
    try {
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
                    // The clip lands in the conversation under its prompt; the worker
                    // wrote that turn on delivery, so the transcript is re-read rather
                    // than guessed at. The panel stays shut — opening minutes later over
                    // whatever the person moved on to is an interruption.
          if (job.artifactId) await get().openSession(sessionId)
          return
        }
      }
    } finally {
      followedJobs.delete(jobId)
    }
  },
  generateAudio: async (sessionId, prompt, opts = {}) => {
    const { avOptions, modelByKind } = get()
    let id = sessionId
    if (!id) {
      id = await get().newSession('av', { projectId: opts.projectId ?? null })
      opts.onSession?.(id)
    }
    // Speech comes back inside this call, so the turn is the whole of what is
    // shown: the prompt, then an answer row waiting a few seconds for its
    // player. No job card — there is no server-side job to report on.
    const { promptId, answerId } = beginMediaTurn(set, id, prompt, true)
    beginRun(set, id)
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
      set((s) => ({ artifacts: [toArtifact(row), ...s.artifacts] }))
      // On screen the moment the bytes land, from what this call returned.
      finishMediaTurn(set, id, answerId, [row.id], false)
      await get().loadSessions()
      // Then handed over to the server's own rows. They carry the ids a rating
      // hangs on and the charge that was settled, and what a clip cost is not
      // something this call was ever told.
      await get().openSession(id)
    } catch (err) {
      failMediaTurn(set, id, promptId, answerId, errorMessage(err, tr('오디오를 만들지 못했습니다.')))
    } finally {
      endRun(set, id)
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
    const { promptId, answerId } = beginMediaTurn(set, id, prompt, true)
    beginRun(set, id)
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
      set((s) => ({ artifacts: [...rows.map(toArtifact), ...s.artifacts] }))
      // One prompt, one answer, however many pictures came back: four of them
      // are one reply to one request, not four turns. Fewer than were asked
      // for is a batch that broke partway, and the answer says so rather than
      // presenting three as though three had been the question.
      finishMediaTurn(
        set,
        id,
        answerId,
        rows.map((row) => row.id),
        rows.length < imageOptions.count,
      )
      // The gallery is the record; the sidebar entry should carry the prompt.
      await get().loadSessions()
      // And the transcript is handed to the server's own rows, which know what
      // the batch was charged.
      await get().openSession(id)
    } catch (err) {
      failMediaTurn(set, id, promptId, answerId, errorMessage(err, tr('이미지를 만들지 못했습니다.')))
    } finally {
      endRun(set, id)
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
    // The gallery holds its own copies, and some of them may have just been
    // destroyed. Cheaper to re-read than to work out which.
    if (payload.artifacts) await get().loadArtifacts()
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
     * Written through rather than held in the tab: a verdict about a transcript
     * has to survive the page.
     *
     * Pressing the lit thumb withdraws it, which is why `null` travels as a
     * value rather than as a missing field.
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
    // Keeping a column decides this conversation and nothing else. Moving
    // `modelByKind.chat` too would make one preference inside a comparison the
    // model every later chat opens on.
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
  composerRestore: null,
  setComposerRestore: (composerRestore) => set({ composerRestore }),
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
            // cheapest `av` model is a speech model — so the model follows the mode
            // unless the one already chosen suits it. Applied whenever the mode is
            // named, since 영상 is the mode this surface opens in.
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
  deleteMany: async (kind, ids) => {
    if (ids.length === 0) return 0
    touchWorkspace()
    const api = {
      projects: projectsApi,
      artifacts: artifactsApi,
      skills: skillsApi,
      agents: agentsApi,
      designs: designsApi,
      connectors: connectorsApi,
    }[kind]
    const { deleted } = await api.removeMany(ids)
    const gone = new Set(ids)
    set((s) => ({
      projects: kind === 'projects' ? s.projects.filter((r) => !gone.has(r.id)) : s.projects,
      artifacts: kind === 'artifacts' ? s.artifacts.filter((r) => !gone.has(r.id)) : s.artifacts,
      skills: kind === 'skills' ? s.skills.filter((r) => !gone.has(r.id)) : s.skills,
      agents: kind === 'agents' ? s.agents.filter((r) => !gone.has(r.id)) : s.agents,
      designs: kind === 'designs' ? s.designs.filter((r) => !gone.has(r.id)) : s.designs,
      connectors:
        kind === 'connectors' ? s.connectors.filter((r) => !gone.has(r.id)) : s.connectors,
      // The server detaches rather than deletes on both of these.
      sessions:
        kind === 'projects'
          ? s.sessions.map((c) => (c.projectId && gone.has(c.projectId) ? { ...c, projectId: null } : c))
          : s.sessions,
    }))
    if (kind === 'projects' || kind === 'designs') {
      // A project loses its look, a look loses its projects; both are rows the
      // other screens are already showing.
      await get().loadWorkspace()
    }
    // The gallery's tab counts and its "N개 더 보기" come from a second
    // endpoint that counts the whole workspace, so filtering the list in place
    // leaves every number beside it describing the workspace as it was.
    if (kind === 'artifacts') await get().loadArtifacts()
    return deleted
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
     * Keyset rather than offset: the list is ordered by a timestamp that moves
     * on edit, and an offset would skip or repeat rows under the reader.
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
      // Counted again afterwards, for the same reason the bulk delete does:
      // the numbers on the filter row are a separate query over the whole
      // workspace, not a length of what is on screen.
      commit: () => artifactsApi.remove(id).then(() => get().loadArtifacts()).catch(() => get().loadArtifacts()),
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
      visibility: skill.visibility,
    }
    const exists = get().skills.some((s) => s.id === skill.id)
    const row = exists ? await skillsApi.update(skill.id, payload) : await skillsApi.create(payload)
    set((s) => ({
      skills: exists
        ? s.skills.map((x) => (x.id === skill.id ? toSkill(row) : x))
        : [toSkill(row), ...s.skills],
    }))
  },
  loadSkillStore: async () => {
    set({ skillStoreLoading: true })
    const rows = await skillsApi.store().catch(() => null)
    set((st) => ({
      skillStore: rows ? rows.map(toStoreSkill) : st.skillStore,
      skillStoreLoading: false,
    }))
  },
  installSkill: async (id) => {
    touchWorkspace()
    const row = await skillsApi.install(id)
    const copy = toSkill(row)
    set((st) => ({
      // Idempotent on the server, so a double press returns the copy already
      // held rather than a second row — and this has to agree with it.
      skills: st.skills.some((x) => x.id === copy.id)
        ? st.skills.map((x) => (x.id === copy.id ? copy : x))
        : [copy, ...st.skills],
      // The store card flips to 가져옴 without a second round trip.
      skillStore: st.skillStore.map((x) => (x.id === id ? { ...x, installed: true } : x)),
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

  installAgent: async (a) => {
    touchWorkspace()
    /*
     * The copy is made by the server, which is a change from doing it here.
     *
     * Only the prompt used to travel. A skill allow-list is a list of rows in
     * the author's account and the same ids resolve to nothing here, so the
     * copy answered differently from the agent on the card and said so only in
     * a line appended to its description. The install route copies the shared
     * skills too and rewrites the list against them.
     *
     * The knowledge shelf still stays behind — those are the author's
     * documents, and copying their agent is not a grant over their files.
     */
    const row = await agentsApi.install(a.id)
    const copy = toAgent(row)
    set((s) => ({
      agents: s.agents.some((x) => x.id === copy.id)
        ? s.agents.map((x) => (x.id === copy.id ? copy : x))
        : [copy, ...s.agents],
    }))
    // The store's own copy of the original: its install count moved, and the
    // card it is on should stop offering an import that is already done.
    await get().loadWorkspace()
  },
  upsertAgent: async (a) => {
    touchWorkspace()
    const payload = {
      name: a.name,
      // Carried at last: the form had a slug field whose value went nowhere.
      slug: a.slug,
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
 * Why the turn failed, in a sentence somebody can act on.
 *
 * 응답을 받지 못했습니다 was the whole vocabulary, and it covers a model this
 * instance cannot serve, a backend that never answered, an account out of
 * credits and a proxy that is down — four situations with four different next
 * moves. Naming which one it was is the difference between "pick another
 * model" and "wait and try again", and a person who cannot tell them apart
 * reads every one of them as the service being broken.
 */
function turnFailure(err: unknown): string {
  if (err instanceof StreamStalledError) {
    return '모델이 응답하지 않아 요청을 중단했습니다. 다른 모델로 다시 생성해 보세요.'
  }
  const code = err instanceof ApiError ? err.detail : ''
  switch (code) {
    case 'model_unavailable':
      return '이 모델은 지금 이 화면에서 쓸 수 없습니다. 모델을 바꿔 다시 시도하세요.'
    case 'model_not_allowed':
      return '이 계정에 허용되지 않은 모델입니다. 모델을 바꿔 다시 시도하세요.'
    case 'no_models_available':
      return '지금 사용할 수 있는 모델이 없습니다. 관리자에게 문의하세요.'
    case 'insufficient_credits':
      return '이번 달 크레딧이 부족합니다.'
  }
  if (err instanceof ApiError && err.status >= 500) {
    return '모델 서버가 응답하지 않습니다. 잠시 후 다시 시도하세요.'
  }
  return '응답을 받지 못했습니다. 잠시 후 다시 시도하세요.'
}

/**
 * Delete held for a few seconds before it is sent: the row leaves the screen
 * at once and undo cancels the call before anything is destroyed.
 *
 * Short enough that nobody relies on it. Leaving the page flushes what is
 * pending rather than dropping it.
 */
const UNDO_MS = 6_000

/**
 * Swaps the id this tab made up for an answer for the one the server stored
 * it under, once `done` says which that is.
 *
 * Everything addressed to a message afterwards — a rating, a comparison's
 * 이 답변으로 계속 — used to go out under the made-up id, meet a 404, and fall
 * back to reloading the session, which then showed the server's default in
 * place of the click. The transcript only learned the real ids on reopen.
 */
function adoptServerId(set: Set, sessionId: string, localId: string, serverId: string) {
  if (localId === serverId) return
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? { ...c, messages: c.messages.map((m) => (m.id === localId ? { ...m, id: serverId } : m)) }
        : c,
    ),
  }))
}

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
        : (a as { id?: string; name: string; size: number | string; type: string }),
    ),
    usage: raw.usage ?? undefined,
    startedFrom: raw.startedFrom ?? undefined,
    artifactIds: raw.artifactIds ?? undefined,
    failure: raw.failure ?? undefined,
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
    pending: raw.pending ?? null,
    renderTemplateId: raw.renderTemplateId ?? null,
    pinned: raw.pinned,
    createdAt: raw.createdAt,
    updatedAt: raw.updatedAt,
    messages: raw.messages ? raw.messages.map(toMessage) : (keepMessages ?? []),
    preview: raw.preview,
    messageCount: raw.messageCount,
    made: raw.made ?? null,
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
 * A listing row carries a trimmed body, so taking it wholesale would blank a
 * panel or hand an editor a truncated copy to save. The fuller copy wins
 * while it is the same version.
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
    visibility: (s.visibility as Skill['visibility']) ?? 'private',
    installs: s.installs ?? 0,
    originId: s.originId ?? null,
    version: s.version,
    files: ['SKILL.md'],
    updatedAt: s.updatedAt,
  }
}

function toStoreSkill(s: StoreSkillRow): StoreSkill {
  return {
    ...toSkill(s),
    ownerId: s.ownerId,
    ownerName: s.ownerName,
    official: s.official ?? false,
    installed: s.installed ?? false,
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
    catalogKey: a.catalogKey ?? null,
    originId: a.originId ?? null,
    official: a.official ?? false,
    installed: a.installed ?? false,
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
    /** Turn-only model override; absent means the conversation's own. */
    model?: string
    webSearch?: boolean
    attachments?: string[]
    attachmentNames?: string[]
    activatedSkillIds?: string[]
    startingTemplateId?: string
    privacyAction?: PrivacyAction
    privacyDecisionToken?: string
    /** The failed question this turn reruns in place. See `send`. */
    retryOf?: string
    onAccepted?: () => void
  } = {},
) {
  let assistantId = uid('m')
  const controller = new AbortController()

  set((s) => ({
    running: { ...s.running, [sessionId]: () => controller.abort() },
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
        model: opts.model,
        webSearch: opts.webSearch,
        attachments: opts.attachments,
        activatedSkillIds: opts.activatedSkillIds,
        startingTemplateId: opts.startingTemplateId,
        privacyAction: opts.privacyAction,
        privacyDecisionToken: opts.privacyDecisionToken,
        retryOf: opts.retryOf,
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
                    // The document as well as the listing: an `html` card has its
                    // `content` emptied for the grid, so opening on the card alone puts
                    // a finished document on screen as a white rectangle.
          void Promise.all([get().loadArtifacts(), get().refreshArtifact(event.artifactId)]).then(
            () => set({ openArtifactId: event.artifactId }),
          )
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
        case 'done':
          if (event.messageId) {
            adoptServerId(set, sessionId, assistantId, event.messageId)
            // Later patches — buffered text in `finally`, a late error — must
            // find the row under its new name.
            assistantId = event.messageId
          }
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
      // Said now, not after a reload. The server stores the same mark once the
      // turn settles, but this tab only learns that by reopening the session —
      // until then the answer just ended mid-sentence with nothing to say why,
      // and the retry that belongs under it was nowhere.
      patch((m) => ({ ...m, failure: 'stopped' }))
    } else if (isClientRefusal(err)) {
      settled = true
      patch((m) => ({ ...m, error: errorMessage(err, tr('요청을 처리하지 못했습니다.')) }))
      // HTTP refusal means the server did not store the turn. Let the
      // composer restore its draft, attachments, and one-turn skill choice.
      throw err
    } else {
      settled = true
      patch((m) => ({ ...m, error: tr(turnFailure(err)) }))
      // A stall is the turn ending, not the session breaking. Swallowed here so
      // the composer does not also put the sentence back in the box — the
      // failed turn already carries its own retry.
      if (err instanceof StreamStalledError) return
      throw err
    }
  } finally {
    // Buffered text lands here, including on abort or error.
    if (!live && buffered) patch((m) => ({ ...m, content: buffered }))
    if (!settled) patch((m) => ({ ...m, error: CUT_OFF }))
    endRun(set, sessionId)
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
  let assistantId = uid('m')
  const variants: Variant[] = models.map((model) => ({ model, content: '', status: 'streaming' }))

  set((s) => ({
    running: { ...s.running, [sessionId]: noop },
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
  beginRun(set, sessionId, () => controller.abort())
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
        if (e.messageId) {
          adoptServerId(set, sessionId, assistantId, e.messageId)
          assistantId = e.messageId
        }
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
    endRun(set, sessionId)
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
/**
 * Puts the waiting generation on the session, or takes it off.
 *
 * The server owns this row — it is what a reload reads — but the browser has
 * just watched the events that produced it, and waiting for a round trip to
 * redraw would leave the proposal invisible for as long as that took.
 */
function setPending(
  set: Set,
  sessionId: string,
  next: (current: PendingPlan | null) => PendingPlan | null,
) {
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId ? { ...c, pending: next(c.pending ?? null) } : c,
    ),
  }))
}

async function streamReport(
  set: Set,
  get: Get,
  sessionId: string,
  text: string,
  model: string,
  activatedSkillIds?: string[],
  startingTemplateId?: string,
  /**
   * The approval, when this run is the second half of one.
   *
   * `approve` is what turns a proposal into a document, and it is also what
   * decides whether to put a draft artifact on screen at all: a planning pass
   * writes nothing, so an empty panel opening over the deck already there
   * would say the opposite of what is happening.
   */
  gate: { approve?: boolean; answers?: Record<string, string> } = {},
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

  // A planning pass produces no document, so it must not open one. Before
  // this, every request put an empty panel over whatever was already there —
  // which is the picture somebody sees a second before their deck is replaced.
  const willWrite = gate.approve === true
  set((s) => ({
    running: { ...s.running, [sessionId]: noop },
    ...(willWrite ? { artifacts: [draft, ...s.artifacts], openArtifactId: draftId } : {}),
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            ...(willWrite ? { artifactId: draftId } : {}),
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
  beginRun(set, sessionId, () => controller.abort())
  //: Whether the turn ended on purpose. See CUT_OFF.
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      {
        content: text,
        model,
        activatedSkillIds,
        startingTemplateId,
        approve: gate.approve,
        answers: gate.answers,
      },
      controller.signal,
    )) {
      switch (e.type) {
        // The turn stopped on purpose: it planned, or it asked. Neither wrote
        // anything, so the session keeps whatever document it already had and
        // simply carries the offer until somebody answers it.
        case 'proposal':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'outline',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            plan: e.plan,
          }))
          break
        case 'needs':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'clarify',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            questions: e.questions,
          }))
          break
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
    endRun(set, sessionId)
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
  /**
   * The approval, when this run is the second half of one.
   *
   * `approve` is what turns a proposal into a document, and it is also what
   * decides whether to put a draft artifact on screen at all: a planning pass
   * writes nothing, so an empty panel opening over the deck already there
   * would say the opposite of what is happening.
   */
  gate: { approve?: boolean; answers?: Record<string, string> } = {},
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

  // A planning pass produces no document, so it must not open one. Before
  // this, every request put an empty panel over whatever was already there —
  // which is the picture somebody sees a second before their deck is replaced.
  const willWrite = gate.approve === true
  set((s) => ({
    running: { ...s.running, [sessionId]: noop },
    ...(willWrite ? { artifacts: [draft, ...s.artifacts], openArtifactId: draftId } : {}),
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            ...(willWrite ? { artifactId: draftId } : {}),
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
  beginRun(set, sessionId, () => controller.abort())
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      {
        content: text,
        model,
        activatedSkillIds,
        renderTemplateId,
        startingTemplateId,
        approve: gate.approve,
        answers: gate.answers,
      },
      controller.signal,
    )) {
      switch (e.type) {
        // The turn stopped on purpose: it planned, or it asked. Neither wrote
        // anything, so the session keeps whatever document it already had and
        // simply carries the offer until somebody answers it.
        case 'proposal':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'outline',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            plan: e.plan,
          }))
          break
        case 'needs':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'clarify',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            questions: e.questions,
          }))
          break
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
    endRun(set, sessionId)
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
  /**
   * The approval, when this run is the second half of one.
   *
   * `approve` is what turns a proposal into a document, and it is also what
   * decides whether to put a draft artifact on screen at all: a planning pass
   * writes nothing, so an empty panel opening over the deck already there
   * would say the opposite of what is happening.
   */
  gate: { approve?: boolean; answers?: Record<string, string> } = {},
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

  // A planning pass produces no document, so it must not open one. Before
  // this, every request put an empty panel over whatever was already there —
  // which is the picture somebody sees a second before their deck is replaced.
  const willWrite = gate.approve === true
  set((s) => ({
    running: { ...s.running, [sessionId]: noop },
    ...(willWrite ? { artifacts: [draft, ...s.artifacts], openArtifactId: draftId } : {}),
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            ...(willWrite ? { artifactId: draftId } : {}),
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
  beginRun(set, sessionId, () => controller.abort())
  //: Whether the turn ended on purpose. See CUT_OFF.
  let settled = false

  try {
    for await (const e of streamSession(
      sessionId,
      {
        content: text,
        model,
        activatedSkillIds,
        startingTemplateId,
        approve: gate.approve,
        answers: gate.answers,
      },
      controller.signal,
    )) {
      switch (e.type) {
        // The turn stopped on purpose: it planned, or it asked. Neither wrote
        // anything, so the session keeps whatever document it already had and
        // simply carries the offer until somebody answers it.
        case 'proposal':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'outline',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            plan: e.plan,
          }))
          break
        case 'needs':
          settled = true
          setPending(set, sessionId, (p) => ({
            stage: 'clarify',
            request: text,
            attachments: p?.attachments ?? [],
            answers: p?.answers ?? {},
            questions: e.questions,
          }))
          break
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
    endRun(set, sessionId)
    void get().loadSessions()
  }
}

/**
 * Puts the turn's privacy routing on the prompt it was decided for.
 *
 * The server stores the same thing, so this is what a reload would show. Doing
 * it live is what lets the transcript admit the substitution now rather than
 * at a reopening a week away.
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

/**
 * The two halves of a media turn, on screen before the server has them.
 *
 * The same optimism the chat composer runs on: the sentence appears where it
 * was typed and the answer takes shape under it. The server writes both rows
 * and a reload replaces these.
 *
 * `pending` is the empty answer row that says the picture is coming. A clip
 * gets none — its job card carries a stage, a percentage and a way to stop,
 * and two placeholders for one request would read as two requests.
 */
function beginMediaTurn(set: Set, sessionId: string, prompt: string, pending: boolean) {
  const promptId = uid('m')
  const answerId = pending ? uid('m') : ''
  const now = new Date().toISOString()
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            // The server names the row from the same prompt. Doing it here too
            // keeps the sidebar from showing 새 작업 for the length of the call.
            title: c.messages.length === 0 ? prompt.slice(0, 40) : c.title,
            updatedAt: now,
            messages: [
              ...c.messages,
              { id: promptId, role: 'user' as const, content: prompt, createdAt: now },
              ...(answerId
                ? [{ id: answerId, role: 'assistant' as const, content: '', createdAt: now }]
                : []),
            ],
          }
        : c,
    ),
  }))
  return { promptId, answerId }
}

/**
 * What came back, under the prompt that asked for it.
 *
 * No charge is written here: this call was handed pictures and not a bill, and
 * the figure arrives with the server's own copy of the turn.
 */
function finishMediaTurn(
  set: Set,
  sessionId: string,
  answerId: string,
  artifactIds: string[],
  short: boolean,
) {
  patchMessage(set, sessionId, answerId, (m) => ({
    ...m,
    artifactIds,
    // Fewer pictures than were asked for. The batch is billed per call, so the
    // ones that arrived are kept and the shortfall is said rather than quietly
    // rounded down to "here is what you asked for".
    failure: short ? ('interrupted' as const) : undefined,
  }))
}

/**
 * A request that came back with nothing.
 *
 * The empty answer row goes away and the question carries the mark. The server
 * records the same thing on the same row, which is what survives a reload.
 */
function failMediaTurn(
  set: Set,
  sessionId: string,
  promptId: string,
  answerId: string,
  said: string,
) {
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? {
            ...c,
            messages: c.messages
              .filter((m) => m.id !== answerId)
              .map((m) =>
                // What the upstream said, while this tab still has it. The
                // stored mark is the same failure in the product's own words,
                // and it is all a reload has left to go on.
                m.id === promptId ? { ...m, failure: 'no_answer' as const, error: said } : m,
              ),
          }
        : c,
    ),
  }))
}

/** Takes an optimistic turn back off, for a request that was never accepted. */
function dropMediaTurn(set: Set, sessionId: string, ...ids: string[]) {
  set((s) => ({
    sessions: s.sessions.map((c) =>
      c.id === sessionId
        ? { ...c, messages: c.messages.filter((m) => !ids.includes(m.id)) }
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
