/**
 * The seam between the app and its backend.
 *
 * The browser talks to the KloudChat API and nothing else — never to LiteLLM. The
 * master key and every virtual key stay on the server.
 */

import type {
  Preferences,
  Message,
  ModelInfo,
  PrivacyAction,
  PrivacyRouting,
  CostRouting,
  Session,
  SessionKind,
  Slide,
  Source,
  Step,
  User,
} from '@/types'

export const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/**
 * In memory only; the refresh token is an httpOnly cookie. A reload starts with
 * no token and recovers via `auth.refresh()` — see `bootstrap()` in the store.
 */
let accessToken: string | null = null
export const setAccessToken = (t: string | null) => {
  accessToken = t
}
/** Carries the backend's `detail` code so callers can branch without parsing prose. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * What to put on the screen when a call fails.
 *
 * A 4xx `detail` is written for the person who made the request — "이미 사용
 * 중인 이메일입니다" is the answer they need. A 5xx one is written for whoever
 * reads the logs, and putting it on screen hands somebody "upstream exploded"
 * as if it were an instruction. Same for a network error, whose message is the
 * browser's own English.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.detail) {
    return err.detail
  }
  return fallback
}

export class UnauthorizedError extends ApiError {
  constructor(detail = 'unauthorized') {
    super(401, detail)
    this.name = 'UnauthorizedError'
  }
}

export interface PrivacyDecision {
  code: 'privacy_decision_required'
  findings: { category: string; source: string; count: number }[]
  requestedModels: string[]
  safeModels: { id: string; label: string }[]
  allowedActions: (PrivacyAction | 'edit' | 'cancel')[]
  decisionToken: string
  detectorVersion: string
  policyVersion: string
}

export class PrivacyDecisionError extends ApiError {
  readonly decision: PrivacyDecision
  sessionId?: string

  constructor(decision: PrivacyDecision) {
    super(409, decision.code)
    this.name = 'PrivacyDecisionError'
    this.decision = decision
  }
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    // FastAPI: a string in `detail` for explicit raises, an array of field
    // errors for validation failures.
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) return body.detail[0]?.msg ?? 'invalid_request'
  } catch {
    /* non-JSON error body */
  }
  return `http_${res.status}`
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  })
  if (!res.ok) {
    const detail = await readDetail(res)
    if (res.status === 401) throw new UnauthorizedError(detail)
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

const body = (v: unknown) => ({ method: 'POST', body: JSON.stringify(v) })

/* ── auth — KloudChat's own ─────────────────────────────────────────────────
 * argon2id hashes, a short-lived JWT access token, a rotating refresh cookie.
 * Signup lands in `pending` until an admin approves and funds the account.
 */

export interface AuthSession {
  accessToken: string
  /** Access-token lifetime in seconds; drives the silent-refresh timer. */
  expiresIn: number
  user: User
}

/** Signup returns no session while the account waits for approval. */
export interface SignupResult {
  user: User
  session: AuthSession | null
}

export const auth = {
  login: (email: string, password: string) =>
    call<AuthSession>('/auth/login', body({ email, password })),
  signup: (email: string, password: string, name: string) =>
    call<SignupResult>('/auth/signup', body({ email, password, name })),
  refresh: () => call<AuthSession>('/auth/refresh', { method: 'POST' }),
  logout: () => call<void>('/auth/logout', { method: 'POST' }),
  /** Reachable while `pending` — the approval-waiting screen polls it. */
  me: () => call<User>('/auth/me'),
  updateMe: (patch: { name?: string; avatarColor?: string; preferences?: Partial<Preferences> }) =>
    call<User>('/auth/me', { method: 'PATCH', body: JSON.stringify(patch) }),
  /** Ends every other session; this one is re-issued a fresh refresh cookie. */
  changePassword: (currentPassword: string, newPassword: string) =>
    call<void>('/auth/password', body({ currentPassword, newPassword })),
}

/* ── models & credits ─────────────────────────────────────────────────── */

export interface ModelCatalogue {
  models: ModelInfo[]
  /**
   * False when the proxy did not answer. Adapter-backed models are still
   * listed, so the UI can distinguish "proxy down" from "empty catalogue".
   */
  litellmAvailable: boolean
  /** Model used when the user has not chosen one. Empty when it is not in
   *  the catalogue. */
  defaultChatModel?: string
  autoRouting: {
    enabled: boolean
    available: boolean
    reason: 'disabled' | 'classifier_unavailable' | 'no_economy_models' | null
    classifierModelId: string | null
    economyModelIds: string[]
  }
}

export const modelsApi = {
  /** Merged view: LiteLLM `/model/info` plus locally-configured adapter models. */
  list: () => call<ModelCatalogue>('/models'),
  /** Admin-only. Drops the server's 30-second cache. */
  refresh: () => call<ModelCatalogue>('/models/refresh', { method: 'POST' }),
}

/* ── admin ──────────────────────────────────────────────────────────────*/

export interface SystemSettings {
  litellm: {
    baseUrl: string
    /** Where the value came from. `backend` means it was derived from the
     *  gateway address rather than typed into this field. */
    baseUrlSource: 'database' | 'backend' | 'environment'
    masterKeySet: boolean
    /** Last four characters. The key itself is never sent. */
    masterKeyPreview: string
    masterKeySource: 'database' | 'environment'
  }
  /** Outgoing mail. Empty host means it is off, and the password reset with it. */
  smtp: {
    host: string
    port: string
    security: 'starttls' | 'ssl' | 'none'
    username: string
    from: string
    /** Origin the reset link is built from — never the request's own Host. */
    appBaseUrl: string
    passwordSet: boolean
    passwordPreview: string
    hostSource: 'database' | 'environment'
    /** What the sign-in page keys its reset link off. */
    passwordResetEnabled: boolean
  }
  status: 'ok' | 'unavailable'
  /** Service name and logo URL to render. */
  brand: { name: string; logo: string }
  /** Enabled surfaces. Chat is always included. */
  enabledKinds: string[]
  /** Feature integration: one gateway address fans out into six features. */
  tools: {
    /** Gateway address. Empty means each feature was configured on its own,
     *  or the environment is supplying them. */
    backendBaseUrl: string
    features: {
      key: 'search' | 'fetch' | 'exec' | 'research' | 'stt' | 'index'
      label: string
      url: string
      /** `backend` means derived from the gateway address rather than typed
       *  into this field. */
      source: 'database' | 'backend' | 'environment'
    }[]
  }
  /** Instance economics, served so the screens quoting them cannot drift. */
  credits: {
    /** Credits to one US dollar. */
    perUsd: number
    /** How far above the KloudChat allowance the LiteLLM budget is set. */
    budgetHeadroom: number
  }
  /** Served by the proxy, withheld from the picker for want of a price. */
  unpricedModels: { id: string; provider: string }[]
}

export const authConfig = {
  /** What the sign-in page may offer. Public because the browser cannot read
   *  the admin settings, and a dead reset link is worse than none. */
  get: () =>
    call<{
      passwordResetEnabled: boolean
      dictationEnabled: boolean
      brand: { name: string; logo: string }
      enabledKinds: string[]
      privacy: { externalDataGuard: boolean; allowUserRawExternal: boolean }
    }>('/auth/config'),
  forgotPassword: (email: string) => call<void>('/auth/password/forgot', body({ email })),
  resetPassword: (token: string, newPassword: string) =>
    call<void>('/auth/password/reset', body({ token, newPassword })),
}

export interface MyUsage {
  days: number
  totals: { credits: number; requests: number }
  /** This month's allowance and what is left of it. */
  cycle: { allowance: number; used: number; remaining: number }
  daily: { date: string; credits: number; requests: number }[]
  byModel: { model: string; credits: number; requests: number }[]
  bySurface: { kind: string; credits: number; requests: number }[]
  /** Spend through issued keys, aggregated by the proxy — shown beside the
   *  number above rather than added to it. */
  apiKeys: {
    id: string
    name: string
    preview: string
    spendUsd: number
    credits: number
    budgetUsd: number
  }[]
}

export const meApi = {
  /** The caller's own spending. `/admin/usage` answers for everyone. */
  usage: (days = 30) => call<MyUsage>(`/me/usage?days=${days}`),
}

export const adminApi = {
  settings: () => call<SystemSettings>('/admin/settings'),
  /** Omit a field to leave it; send an empty string to clear it. */
  updateSettings: (patch: {
    baseUrl?: string
    masterKey?: string
    brandName?: string
    enabledKinds?: string
    backendBaseUrl?: string
    toolsSearchUrl?: string
    toolsFetchUrl?: string
    toolsExecUrl?: string
    toolsResearchUrl?: string
    toolsSttUrl?: string
    toolsIndexUrl?: string
    smtpHost?: string
    smtpPort?: string
    smtpSecurity?: string
    smtpUsername?: string
    smtpPassword?: string
    smtpFrom?: string
    appBaseUrl?: string
  }) => call<SystemSettings>('/admin/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  testSettings: () =>
    call<{ ok: boolean; models?: number; detail?: string }>('/admin/settings/test', {
      method: 'POST',
    }),
  /** Logo image. multipart, so it does not go through call()'s JSON path. */
  uploadLogo: async (file: File) => {
    // With multipart the browser has to set Content-Type itself, boundary
    // included, so this bypasses call()'s JSON headers.
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE_URL}/admin/branding/logo`, {
      method: 'POST',
      body: form,
      credentials: 'include',
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
    if (!res.ok) throw new ApiError(res.status, await readDetail(res))
    return (await res.json()) as { logo: string }
  },
  deleteLogo: () => call<void>('/admin/branding/logo', { method: 'DELETE' }),
  /** Sends a real request to one feature's address. */
  testTool: (feature: string) =>
    call<{ ok: boolean; detail?: string }>(`/admin/settings/test-tool/${feature}`, {
      method: 'POST',
    }),
  /** Sends a real message: a connection check passes with a sender the relay
   *  refuses, which is the failure operators hit. */
  testSmtp: (to?: string) =>
    call<{ ok: boolean; detail?: string }>('/admin/settings/smtp-test', body({ to: to ?? null })),
  users: () => call<User[]>('/admin/users'),
  /** Approval and the allowance are one action: an active account with no
   *  credits reads as a bug. */
  approve: (id: string, monthlyCredits?: number) =>
    call<User>(`/admin/users/${id}/approve`, body({ monthlyCredits: monthlyCredits ?? null })),
  /** Turns down a pending signup. Suspends rather than deletes — see the router. */
  reject: (id: string) => call<User>(`/admin/users/${id}/reject`, { method: 'POST' }),
  suspend: (id: string) => call<User>(`/admin/users/${id}/suspend`, { method: 'POST' }),
  reinstate: (id: string) => call<User>(`/admin/users/${id}/reinstate`, { method: 'POST' }),
  /** Removes the account and everything it owns. Not reversible — suspend is. */
  removeUser: (id: string) => call<void>(`/admin/users/${id}`, { method: 'DELETE' }),
  /** Issues a fresh LiteLLM key, retiring the old one. Also the first key for
   *  an account that signed up while the proxy was unreachable. */
  rotateLitellmKey: (id: string) =>
    call<User>(`/admin/users/${id}/litellm-key`, { method: 'POST' }),
  /** Restricts an account to a set of models. Pushed to every key it holds. */
  setUserModels: (id: string, models: string[]) =>
    call<User>(`/admin/users/${id}/models`, body({ models })),
  /** Sets the monthly allowance. Takes effect now and at every refill. */
  setCredits: (id: string, monthlyCredits: number) =>
    call<User>(`/admin/users/${id}/credits`, body({ monthlyCredits })),
}

/* ── admin: usage & audit ──────────────────────────────────────────────
 * Both from stored rows — turns for usage, the audit table for the trail. No
 * seeded fallback: an empty instance reports empty.
 */

export interface UsageReport {
  days: number
  since: string
  totals: { credits: number; requests: number; activeUsers: number; allocatedCredits: number }
  daily: { date: string; credits: number; requests: number }[]
  byModel: { model: string; credits: number; requests: number; users: number }[]
  bySurface: { kind: SessionKind; credits: number; requests: number }[]
  topUsers: { id: string; name: string; email: string; credits: number; allowance: number }[]
}

export interface AuditRow {
  id: string
  at: string
  actor: string
  action: string
  target: string
  detail: string
  ip: string
  severity: string
  metadata?: Record<string, unknown> | null
}

export interface GovernancePolicy {
  piiMasking: boolean
  externalDataGuard: boolean
  allowUserRawExternal: boolean
  privacySafeModelIds: string[]
  intentFilter: boolean
  blockedCategories: string[]
  /** 0 keeps everything; anything above clears message bodies older than that. */
  retentionDays: number
  adaptiveRoutingEnabled: boolean
  adaptiveClassifierModelId: string | null
  adaptiveEconomyModelIds: string[]
  /** Plans documents when set; null lets each surface's own model plan. */
  outlineModelId: string | null
}

export interface ApiKeyRow {
  id: string
  name: string
  /** Last four characters. */
  preview: string
  createdAt: string
  lastUsedAt: string | null
  /** Present exactly once, in the response that created it. */
  secret?: string | null
}

export const keysApi = {
  list: () => call<ApiKeyRow[]>('/keys'),
  create: (name: string) => call<ApiKeyRow>('/keys', body({ name })),
  revoke: (id: string) => call<void>(`/keys/${id}`, { method: 'DELETE' }),
}

/**
 * Downloads a report export. A plain link cannot carry the access token, so
 * the file is fetched and handed to the browser as a blob.
 */
export async function downloadArtifact(
  id: string,
  format: 'docx' | 'pdf' | 'hwpx' | 'pptx' | 'md' | 'html',
  title: string,
) {
  const res = await fetch(`${BASE_URL}/artifacts/${id}/export?format=${format}`, {
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) throw new ApiError(res.status, await readDetail(res))

  const url = URL.createObjectURL(await res.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'report'}.${format}`
  anchor.click()
  URL.revokeObjectURL(url)
}

/**
 * URL for a stored file, used as the `src` of `<img>`, `<audio>` and `<video>`.
 *
 * Those elements cannot attach an Authorization header and the token lives in
 * memory rather than a cookie, so it goes in the query string. With no token,
 * the bare path is returned.
 */
export function fileUrl(src: string | null | undefined): string | undefined {
  if (!src) return undefined
  if (!accessToken || !src.startsWith(`${BASE_URL}/files/`)) return src
  return `${src}${src.includes('?') ? '&' : '?'}t=${encodeURIComponent(accessToken)}`
}

/**
 * Dictated audio → text. Multipart, so it cannot go through `call()`. Nothing
 * is stored server-side — the clip is a way of typing, not a document.
 */
export async function transcribe(blob: Blob): Promise<string> {
  const form = new FormData()
  form.append('file', blob, 'speech.webm')
  const res = await fetch(`${BASE_URL}/transcribe`, {
    method: 'POST',
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    body: form,
  })
  if (!res.ok) throw new ApiError(res.status, await readDetail(res))
  return ((await res.json()) as { text: string }).text
}

export interface ShareRow {
  id: string
  token: string
  artifactId: string | null
  sessionId: string | null
  scope: 'workspace' | 'link'
  views: number
  createdAt: string
}

/** What the public page gets. Deliberately narrow — see routers/shares.py. */
export type SharedPayload =
  | { kind: 'artifact'; title: string; artifactKind: string; data: unknown; updatedAt: string }
  | {
      kind: 'session'
      title: string
      sessionKind: string
      messages: MessageRow[]
      /** What the conversation produced, when it produced something. */
      artifact: {
        title: string
        artifactKind: string
        data: unknown
        updatedAt: string
      } | null
      updatedAt: string
    }

export const sharesApi = {
  list: () => call<ShareRow[]>('/shares'),
  create: (payload: { artifactId?: string; sessionId?: string; scope: 'workspace' | 'link' }) =>
    call<ShareRow>('/shares', body(payload)),
  revoke: (id: string) => call<void>(`/shares/${id}`, { method: 'DELETE' }),
  /** The public read. No token of ours — the URL is the whole permission. */
  read: (token: string) => call<SharedPayload>(`/shared/${token}`),
}

export const usageApi = {
  governance: () => call<GovernancePolicy>('/admin/governance'),
  setGovernance: (patch: Partial<GovernancePolicy>) =>
    call<{ ok: boolean; clearedMessages: number }>('/admin/governance', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  report: (days = 7) => call<UsageReport>(`/admin/usage?days=${days}`),
  audit: (limit = 100) => call<AuditRow[]>(`/admin/audit?limit=${limit}`),
}

/* ── sessions ───────────────────────────────────────────────────────────
 * One resource for all five surfaces, discriminated by `kind`. Chat, report and
 * slides stream; image and a/v run as jobs.
 */

/** Server shape. Differs from `Message` in `steps` and `attachments` — see `toMessage`. */
export interface MessageRow {
  id: string
  role: Message['role']
  content: string
  steps: unknown[] | null
  attachments: unknown[] | null
  /** Present only on comparison turns — one entry per model that answered. */
  variants:
    | {
        model: string
        routedModel?: string
        actualModel?: string
        dataBoundary?: ModelInfo['dataBoundary']
        content: string
        credits: number
        usage: { inputTokens: number; outputTokens: number } | null
        error: string | null
        chosen?: boolean
      }[]
    | null
  usage: Message['usage'] | null
  model: string | null
  routing: PrivacyRouting | null
  /**
   * The 시작점 this turn was begun from, if there was one. Carries the title
   * as it read that day, so a transcript opened a year later still names the
   * template even after somebody deleted it.
   */
  startedFrom: { templateId: string; title: string } | null
  /** What the reader thought of this answer. Null until somebody says. */
  rating: 'up' | 'down' | null
  createdAt: string
}

export interface SessionRow {
  id: string
  kind: SessionKind
  title: string
  projectId: string | null
  agentId: string | null
  model: string
  routingMode: Session['routingMode']
  artifactId: string | null
  /** The rendering template this session writes into, if one was picked. */
  renderTemplateId: string | null
  pinned: boolean
  createdAt: string
  updatedAt: string
  /** Null on list responses — the sidebar needs titles, not transcripts. */
  messages: MessageRow[] | null
  /** Latest message, one line. On list responses because `messages` is not. */
  preview: string | null
  messageCount: number
}

export const sessionsApi = {
  /** Which of a comparison's answers the conversation continues from. */
  chooseVariant: (sessionId: string, messageId: string, model: string) =>
    call<MessageRow>(`/sessions/${sessionId}/messages/${messageId}/variant`, body({ model })),
  list: (params?: { kind?: string; projectId?: string }) =>
    call<SessionRow[]>(
      `/sessions${params ? `?${new URLSearchParams(params as Record<string, string>)}` : ''}`,
    ),
  get: (id: string) => call<SessionRow>(`/sessions/${id}`),
  /** Many at once. `all` is resolved server-side, so a conversation started in
   *  another tab is not silently spared. */
  deleteMany: (payload: { ids?: string[]; all?: boolean }) =>
    call<{ deleted: number }>('/sessions/delete', body(payload)),
  /** Pictures. Synchronous: the upstream is a completion whose answer is a
   *  PNG, so there is nothing to poll. */
  images: (
    sessionId: string,
    payload: {
      prompt: string
      model?: string
      aspect: string
      style: string
      count: number
      /** An `image` design template. Shapes the prompt; produces no file of its own. */
      templateId?: string
    },
  ) => call<ArtifactRow[]>(`/sessions/${sessionId}/images`, body(payload)),
  /** One sound clip. Speech and music are different models behind `audioKind`. */
  audio: (
    sessionId: string,
    payload: {
      prompt: string
      model?: string
      audioKind: 'narration' | 'music'
      /** One of the gateway's six; narration only. */
      voice?: string
      /** Asked for in the prompt — no audio model here takes a duration. */
      seconds?: number
    },
  ) => call<ArtifactRow>(`/sessions/${sessionId}/audio`, body(payload)),
  create: (payload: {
    kind: string
    projectId?: string | null
    agentId?: string | null
    model?: string | null
    routingMode?: Session['routingMode']
  }) => call<SessionRow>('/sessions', body(payload)),
  update: (
    id: string,
    patch: Partial<Pick<Session, 'title' | 'pinned' | 'model' | 'routingMode'>> & {
      /** A rendering template id, or `''` to take the template off. */
      renderTemplateId?: string
    },
  ) =>
    call<SessionRow>(`/sessions/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/sessions/${id}`, { method: 'DELETE' }),
  /** 좋아요 / 싫어요, or `null` to take the verdict back. Addressed by message
   *  rather than by session: the id is unique and the server checks the
   *  transcript it belongs to anyway. */
  rate: (messageId: string, rating: 'up' | 'down' | null) =>
    call<MessageRow>(`/messages/${messageId}/rating`, {
      method: 'PATCH',
      body: JSON.stringify({ rating }),
    }),
}

/* ── workspace ──────────────────────────────────────────────────────────
 * Projects, files, artifacts, skills, memories, agents. All owned by the
 * caller; anything else answers 404.
 */

export interface FileRow {
  id: string
  name: string
  size: number
  mime: string
  tokens: number
  projectId: string | null
  sessionId: string | null
  /** Set when the file is an agent's searchable knowledge. */
  agentId?: string | null
  /** Set when the text was read from a page rather than uploaded. */
  sourceUrl?: string | null
  /** First few hundred characters of the extracted text. */
  preview: string
  /** Set when extraction failed. The file still uploaded. */
  error: string | null
  /** False when the vector index does not cover this document yet. */
  indexed?: boolean
  createdAt: string
}

export const filesApi = {
  list: (params?: { projectId?: string; sessionId?: string }) =>
    call<FileRow[]>(
      `/files${params ? `?${new URLSearchParams(params as Record<string, string>)}` : ''}`,
    ),
  upload: async (file: File, opts?: { projectId?: string; sessionId?: string }) => {
    const form = new FormData()
    form.append('file', file)
    if (opts?.projectId) form.append('project_id', opts.projectId)
    if (opts?.sessionId) form.append('session_id', opts.sessionId)
    const res = await fetch(`${BASE_URL}/files`, {
      method: 'POST',
      credentials: 'include',
      // No Content-Type — the browser has to set the multipart boundary.
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body: form,
    })
    if (!res.ok) {
      const detail = await readDetail(res)
      if (res.status === 401) throw new UnauthorizedError(detail)
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as FileRow
  },
  downloadUrl: (id: string) => `${BASE_URL}/files/${id}/content`,
  remove: (id: string) => call<void>(`/files/${id}`, { method: 'DELETE' }),
}

/**
 * Hands a stored file back to whoever uploaded it, under its own name.
 *
 * A fetch and a blob rather than a link on `downloadUrl`: a click can carry the
 * Authorization header that an `<img>` cannot, so the token stays out of the
 * URL — and out of the proxy's access log, where `fileUrl()` has to leave it.
 */
export async function downloadFile(id: string, name: string) {
  const res = await fetch(filesApi.downloadUrl(id), {
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) {
    const detail = await readDetail(res)
    if (res.status === 401) throw new UnauthorizedError(detail)
    throw new ApiError(res.status, detail)
  }

  const url = URL.createObjectURL(await res.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}

export interface ProjectRow {
  id: string
  name: string
  description: string
  emoji: string
  instructions: string
  skillIds: string[]
  /** The design system everything in this project wears. Null is the default look. */
  designSystemId: string | null
  /** Surface → rendering template. Sent whole: a missing key is that surface
   *  going back to the built-in track. */
  renderTemplates: Record<string, string>
  files: FileRow[]
  sessionIds: string[]
  createdAt: string
  updatedAt: string
}

export const projectsApi = {
  list: () => call<ProjectRow[]>('/projects'),
  get: (id: string) => call<ProjectRow>(`/projects/${id}`),
  create: (payload: Partial<ProjectRow> & { name: string }) =>
    call<ProjectRow>('/projects', body(payload)),
  update: (id: string, patch: Partial<ProjectRow>) =>
    call<ProjectRow>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/projects/${id}`, { method: 'DELETE' }),
}

function artifactParams(params: ArtifactQuery = {}) {
  const query = new URLSearchParams()
  if (params.kind) query.set('kind', params.kind)
  if (params.projectId) query.set('project_id', params.projectId)
  if (params.q?.trim()) query.set('q', params.q.trim())
  if (params.limit) query.set('limit', String(params.limit))
  if (params.beforeAt) {
    query.set('before_at', params.beforeAt)
    query.set('before_id', params.beforeId ?? '')
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const artifactsApi = {
  /**
   * One page of the gallery, newest first, with the bodies cut down to what a
   * card shows. `partial` marks those rows; `get` is the whole document.
   */
  list: (params?: ArtifactQuery) => call<ArtifactRow[]>(`/artifacts${artifactParams(params)}`),
  /** How many of each kind exist, which a page of rows cannot say. */
  counts: (q?: string) =>
    call<{ counts: Record<string, number>; total: number }>(
      `/artifacts/counts${q?.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`,
    ),
  get: (id: string) => call<ArtifactRow>(`/artifacts/${id}`),
  create: (payload: { kind: string; title?: string; data?: unknown; sessionId?: string | null }) =>
    call<ArtifactRow>('/artifacts', body(payload)),
  /** `expectedVersion` makes the write conditional; the server answers 409 if
   *  somebody else got there first. */
  update: (
    id: string,
    patch: { title?: string; data?: unknown; summary?: string; expectedVersion?: number },
  ) => call<ArtifactRow>(`/artifacts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/artifacts/${id}`, { method: 'DELETE' }),
  versions: (id: string) => call<ArtifactVersionRow[]>(`/artifacts/${id}/versions`),
  /** Checks one slide's claims against the web. Costs searches and a model call. */
  factcheckSlide: (id: string, slideId: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/factcheck`, body({ slideId })),
  /** Rewrites one section. Costs a model call and snapshots the old text. */
  rewriteSection: (id: string, sectionId: string, note: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/rewrite`, body({ sectionId, note })),
  /** One block of an HTML artifact, addressed by position and re-rendered. */
  rewriteBlock: (id: string, index: number, note: string) =>
    call<ArtifactRow>(`/artifacts/${id}/blocks/rewrite`, body({ index, note })),
  /**
   * Puts a picture made on the image surface into one block of a page.
   *
   * The bytes are inlined by the server, so the document stays one file that
   * prints and downloads with the picture in it. Costs no model call.
   */
  addBlockImage: (id: string, index: number, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/blocks/image`, body({ index, artifactId, caption })),
  /** The same, for a slide of a JSON deck. Addressed by slide id, not position. */
  addSlideImage: (id: string, slideId: string, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/image`, body({ slideId, artifactId, caption })),
  /** One reading by a reviewer. Costs a model call; annotates, never edits. */
  critique: (id: string) => call<ArtifactRow>(`/artifacts/${id}/critique`, body({})),
  /** Puts a superseded revision back. Itself an edit, so it adds a version. */
  restore: (id: string, version: number) =>
    call<ArtifactRow>(`/artifacts/${id}/restore`, body({ version })),
}

export interface ArtifactVersionRow {
  version: number
  summary: string
  createdAt: string
}

export interface ArtifactRow {
  id: string
  kind: string
  title: string
  version: number
  data: Record<string, unknown> | null
  sessionId: string | null
  projectId: string | null
  createdAt: string
  updatedAt: string
  /** Set on a listing row: the body was trimmed to what a card shows. */
  partial?: boolean
}

/** One page of the gallery. The cursor is simply the last row it returned. */
export interface ArtifactQuery {
  kind?: string
  projectId?: string
  q?: string
  limit?: number
  beforeAt?: string
  beforeId?: string
}

/**
 * A 시작점 the instance ships with.
 *
 * These lived in the bundle until the turn began carrying one instead of
 * typing it: a template the server has to resolve by id has to be a thing the
 * server knows, and two copies of the same twenty-four would only differ.
 */
export interface PromptTemplateRow {
  id: string
  kind: SessionKind
  group: string
  title: string
  /** What you get. One line, no feature list. */
  description: string
  /** What you have to bring. The composer asks for these by name. */
  fills: string[]
  /**
   * The framing the turn carries. Read by the gallery only on the two media
   * surfaces, where the sentence is the prompt rather than a preamble to it;
   * everywhere else the server adds it and the composer never sees it.
   */
  prompt: string
}

export const promptTemplatesApi = {
  list: () => call<PromptTemplateRow[]>('/prompt-templates'),
}

/**
 * A 시작점 somebody added. Same fields as a built-in one so the gallery can
 * concatenate the two lists instead of branching on origin.
 */
export interface TemplateRow extends PromptTemplateRow {
  /** An uploaded form this template writes into, when there is one. */
  fileId: string | null
  fileName: string
  fileTokens: number
  fileError: string | null
  /** Offered to every account. Administrators only. */
  shared: boolean
  /** Whether the caller may edit or remove it. */
  mine: boolean
  updatedAt: string
}

export const templatesApi = {
  list: () => call<TemplateRow[]>('/templates'),
  create: (payload: Omit<Partial<TemplateRow>, 'kind'> & { kind: SessionKind; title: string }) =>
    call<TemplateRow>('/templates', body(payload)),
  update: (id: string, patch: Partial<TemplateRow>) =>
    call<TemplateRow>(`/templates/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/templates/${id}`, { method: 'DELETE' }),
}

/** The four values every renderer reads. Always complete on the wire. */
export interface DesignTokens {
  accent: string
  ink: string
  muted: string
  font: 'gothic' | 'serif'
}

export interface DesignRow {
  id: string
  name: string
  description: string
  tokens: DesignTokens
  /** Voice and vocabulary, capped short — it reaches the model on every turn. */
  body: string
  /** English phrase appended to this project's image prompts. */
  imageStyle: string
  /** Craft rule keys — see the API's `services/design.py`. */
  craft: string[]
  /** Offered to every account. Administrators only. */
  shared: boolean
  /** Whether the caller may edit or remove it. */
  mine: boolean
  updatedAt: string
}

/** A design system read out of a document — a proposal, not a row. */
export interface DesignDraftRow {
  name: string
  description: string
  tokens: DesignTokens
  body: string
  imageStyle: string
  craft: string[]
  /** What it was read from, so the draft can say so. */
  source: string
  credits: number
}

export const designsApi = {
  list: () => call<DesignRow[]>('/designs'),
  /**
   * Reads one out of an uploaded file or a page. Costs a model call and stores
   * nothing — what comes back opens the editor, where a person decides.
   */
  extract: (payload: { fileId?: string; url?: string }) =>
    call<DesignDraftRow>('/designs/extract', body(payload)),
  create: (payload: Partial<DesignRow> & { name: string }) =>
    call<DesignRow>('/designs', body(payload)),
  update: (id: string, patch: Partial<DesignRow>) =>
    call<DesignRow>(`/designs/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/designs/${id}`, { method: 'DELETE' }),
}

/**
 * A shape the answer comes out in, rather than a sentence it starts from.
 *
 * Read-only: the catalogue ships inside the API image. What a person writes
 * for themselves is a prompt template, which has a table.
 */
/** One blank in a media template's prompt, written `{name}` in the sentence. */
export interface DesignArgumentRow {
  name: string
  label: string
  labelEn: string
  default: string
  defaultEn: string
  /** A closed list renders as a picker; empty renders as a text field. */
  options: string[]
  optionsEn: string[]
}

export interface DesignTemplateRow {
  id: string
  /** `deck` · `document` · `image` */
  kind: string
  /** The surface it is offered on — `slides`, `report` or `image`. */
  surface: SessionKind
  name: string
  description: string
  category: string
  /** What you have to bring, shown as chips before you commit. */
  fills: string[]
  /** Ends mid-sentence, where the person takes over. */
  examplePrompt: string
  /** The English half of the same card; empty falls back to the Korean. */
  nameEn: string
  descriptionEn: string
  categoryEn: string
  fillsEn: string[]
  examplePromptEn: string
  /**
   * What a review will read the finished thing against, one line each.
   *
   * Korean in both languages: these are the rubric a Korean critique scores
   * against, so there is no English half to fall back to and none is faked.
   * Media templates send an empty list — a picture has nothing to review.
   */
  checks: string[]
  /** Blanks to fill before the sentence reaches the composer. */
  arguments: DesignArgumentRow[]
  /**
   * Composer settings this template implies — aspect, duration, voice. Keys
   * match the option stores: `aspect`, `style`, `count` for image; `mode`,
   * `aspect`, `seconds`, `resolution`, `audio`, `audioKind` for audio/video.
   */
  defaults: Record<string, string | number | boolean>
  hasPreview: boolean
}

/** One argument's text in the language on screen. */
export function argumentText(argument: DesignArgumentRow, english: boolean) {
  return {
    label: (english && argument.labelEn) || argument.label,
    initial: (english && argument.defaultEn) || argument.default,
    options: english && argument.optionsEn.length ? argument.optionsEn : argument.options,
  }
}

/** The sentence with its blanks filled. A blank left empty drops out. */
export function fillPrompt(prompt: string, values: Record<string, string>) {
  return prompt.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in values ? values[name] : whole,
  )
}

/** One card's text in the language on screen, falling back rather than blanking. */
export function templateText(row: DesignTemplateRow, english: boolean) {
  return {
    name: (english && row.nameEn) || row.name,
    description: (english && row.descriptionEn) || row.description,
    category: (english && row.categoryEn) || row.category,
    fills: english && row.fillsEn.length ? row.fillsEn : row.fills,
    examplePrompt: (english && row.examplePromptEn) || row.examplePrompt,
  }
}

export const designTemplatesApi = {
  list: (surface?: SessionKind) =>
    call<DesignTemplateRow[]>(`/design-templates${surface ? `?surface=${surface}` : ''}`),
}

/**
 * The preview document for a template card, in the look it will be made in.
 *
 * The tokens ride in the query string because an iframe asks for its document
 * by address and has no other way to say anything. The server validates all
 * four the way the exporters read them, so a card cannot advertise a colour no
 * file could come out in. No tokens is not a lesser preview: it is exactly
 * what a project without a design system produces.
 *
 * Unauthenticated on purpose — see the route's docstring. It is rendered in a
 * sandboxed iframe, so it never runs script even though nothing in it does.
 */
export const designTemplatePreviewUrl = (id: string, tokens?: DesignTokens | null) => {
  const url = `${BASE_URL}/design-templates/${encodeURIComponent(id)}/preview`
  if (!tokens) return url
  const query = new URLSearchParams({
    accent: tokens.accent,
    ink: tokens.ink,
    muted: tokens.muted,
    font: tokens.font,
  })
  return `${url}?${query}`
}

/**
 * The look a project wears, for a card that is standing in for its output.
 *
 * `null` where the project has no design system, or has one this account can
 * no longer see — both are rendered in the default tokens, so both should be
 * previewed in them.
 */
export const designTokensOf = (
  designs: DesignRow[],
  designSystemId: string | null | undefined,
): DesignTokens | null => designs.find((d) => d.id === designSystemId)?.tokens ?? null

export const skillsApi = {
  list: () => call<SkillRow[]>('/skills'),
  create: (payload: Partial<SkillRow> & { name: string }) =>
    call<SkillRow>('/skills', body(payload)),
  update: (id: string, patch: Partial<SkillRow>) =>
    call<SkillRow>(`/skills/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  toggle: (id: string) => call<SkillRow>(`/skills/${id}/toggle`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/skills/${id}`, { method: 'DELETE' }),
}

export interface SkillRow {
  id: string
  name: string
  slug: string
  description: string
  whenToUse: string
  body: string
  catalogKey: string | null
  source: string
  kinds: string[]
  requiredTools: string[]
  estimatedTokens: number
  version: string
  enabled: boolean
  updatedAt: string
}

export interface ToolCatalogRow {
  name: string
  label: string
  available: boolean
}

export const toolsApi = {
  list: () => call<ToolCatalogRow[]>('/tools'),
}

export const memoryApi = {
  list: () => call<MemoryRow[]>('/memory'),
  create: (payload: Partial<MemoryRow> & { name: string }) =>
    call<MemoryRow>('/memory', body(payload)),
  update: (id: string, patch: Partial<MemoryRow>) =>
    call<MemoryRow>(`/memory/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  pin: (id: string) => call<MemoryRow>(`/memory/${id}/pin`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/memory/${id}`, { method: 'DELETE' }),
}

export interface MemoryRow {
  id: string
  name: string
  description: string
  type: string
  body: string
  scope: string
  links: string[]
  pinned: boolean
  updatedAt: string
}

export const agentsApi = {
  list: () => call<AgentRow[]>('/agents'),
  create: (payload: Partial<AgentRow> & { name: string }) =>
    call<AgentRow>('/agents', body(payload)),
  update: (id: string, patch: Partial<AgentRow>) =>
    call<AgentRow>(`/agents/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/agents/${id}`, { method: 'DELETE' }),

  /**
   * An agent's own documents, which it searches through the `search_knowledge`
   * tool rather than having them pushed into every turn. Project files are the
   * other shape: always present, capped by a character budget.
   */
  knowledge: {
    list: (agentId: string) => call<FileRow[]>(`/agents/${agentId}/knowledge`),
    upload: async (agentId: string, file: File) => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${BASE_URL}/agents/${agentId}/knowledge`, {
        method: 'POST',
        credentials: 'include',
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
        body: form,
      })
      if (!res.ok) throw new ApiError(res.status, await readDetail(res))
      return (await res.json()) as FileRow
    },
    addUrl: (agentId: string, url: string) =>
      call<FileRow>(`/agents/${agentId}/knowledge/url`, body({ url })),
    remove: (agentId: string, fileId: string) =>
      call<void>(`/agents/${agentId}/knowledge/${fileId}`, { method: 'DELETE' }),
    /** `force` re-sends everything — what an embedding-model change needs. */
    reindex: (agentId: string, force = false) =>
      call<{ total: number; attempted: number; indexed: number }>(
        `/agents/${agentId}/knowledge/reindex${force ? '?force=true' : ''}`,
        { method: 'POST' },
      ),
  },
}

export interface AgentRow {
  /** Who made it — the store lists other people's agents beside your own. */
  ownerId: string
  ownerName: string
  id: string
  name: string
  slug: string
  description: string
  model: string
  systemPrompt: string
  tools: string[] | null
  skillIds: string[] | null
  kinds: string[]
  temperature: number
  color: string
  enabled: boolean
  visibility: string
  installs: number
  runs: number
  /** True only when this caller has readable documents on this agent's shelf. */
  hasKnowledge: boolean
  updatedAt: string
}

/* ── connectors (MCP) ───────────────────────────────────────────────────
 * Credentials live server-side only; nothing here ever carries one.
 */

export interface ConnectorToolRow {
  name: string
  description: string
  readOnly: boolean
  enabled: boolean
}

export interface ConnectorRow {
  /** Credential names this connector holds — never their values. */
  envKeys?: string[]
  id: string
  name: string
  slug: string
  description: string
  category: string
  transport: string
  endpoint: string
  auth: string
  kinds: string[]
  official: boolean
  installed: boolean
  enabled: boolean
  status: string
  tools: ConnectorToolRow[]
  lastSyncAt: string | null
  error: string | null
}

export interface RequiredEnvField {
  key: string
  label: string
  hint: string
  /** Rendered as a password field and never echoed back by the server. */
  secret: boolean
}

export interface CatalogEntry {
  slug: string
  name: string
  description: string
  category: string
  transport: string
  auth: string
  kinds: string[]
  official: boolean
  installed: boolean
  /** Credentials the server needs before it can start. */
  requiredEnv: RequiredEnvField[]
}

export const connectorsApi = {
  catalog: () => call<CatalogEntry[]>('/connectors/catalog'),
  list: () => call<ConnectorRow[]>('/connectors'),
  install: (slug: string, env: Record<string, string> = {}) =>
    call<ConnectorRow>(`/connectors/install/${slug}`, body({ env })),
  addCustom: (payload: {
    name: string
    transport: string
    endpoint: string
    auth?: string
    description?: string
    /** Credentials for the server process. Write-only — never read back. */
    env?: Record<string, string>
  }) => call<ConnectorRow>('/connectors', body(payload)),
  /** Re-asks the server what tools it exposes. */
  sync: (id: string) => call<ConnectorRow>(`/connectors/${id}/sync`, { method: 'POST' }),
  update: (id: string, patch: { enabled?: boolean; env?: Record<string, string> }) =>
    call<ConnectorRow>(`/connectors/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  toggleTool: (id: string, tool: string, enabled: boolean) =>
    call<ConnectorRow>(`/connectors/${id}/tools/${encodeURIComponent(tool)}`, body({ enabled })),
  uninstall: (id: string) => call<void>(`/connectors/${id}`, { method: 'DELETE' }),
}

/* ── jobs — video generation ───────────────────────────────────────────── */
export interface JobRow {
  id: string
  sessionId: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  progress: number
  stage: string
  creditsUsed: number
  creditsEstimated: number
  error: string | null
  artifactId: string | null
  createdAt: string
  finishedAt: string | null
  prompt: string
  model: string
  params: Record<string, unknown> | null
}

export const jobsApi = {
  /** Also the recovery point: the server restarts a poll loop for anything
   *  left running, so a reload picks a clip in flight back up. */
  list: (sessionId: string) => call<JobRow[]>(`/sessions/${sessionId}/jobs`),
  create: (
    sessionId: string,
    payload: {
      prompt: string
      model?: string
      resolution: string
      seconds: number
      audio: boolean
      aspect: string
    },
  ) => call<JobRow>(`/sessions/${sessionId}/jobs`, body(payload)),
  cancel: (id: string) => call<JobRow>(`/jobs/${id}/cancel`, { method: 'POST' }),
}

/* ── streaming ──────────────────────────────────────────────────────────*/

export type StreamEvent =
  | { type: 'step'; id: string; label: string; status: Step['status']; detail?: string }
  | { type: 'delta'; text: string }
  | {
      type: 'skills_applied'
      skills: { id: string; name: string; catalogKey: string | null; estimatedTokens: number }[]
      estimatedTokens: number
    }
  | { type: 'section'; sectionId: string; heading: string; content: string; done: boolean }
  /**
   * A deck's slides, announced empty by the outline pass and filled in one at a
   * time. Resent whole rather than patched: the layout can change mid-flight
   * when a quote slide comes back without a usable line.
   */
  | { type: 'slide'; slide: Slide; done: boolean }
  /**
   * One block of an HTML artifact — a slide of a design-template deck, a
   * section of a design-template document. Announced empty by the outline
   * pass, then resent whole once written, the same way slides are.
   */
  | { type: 'block'; block: { title: string; layout: string; html: string }; done: boolean }
  /** The finished single file. Arrives once, after the last block. */
  | { type: 'page'; html: string; blocks: { title: string; layout: string }[]; templateId: string }
  | { type: 'title'; title: string }
  /** The reference shelf a report's sections cite from, sent once, up front. */
  | { type: 'sources'; sources: Source[] }
  | { type: 'artifact'; artifactId: string }
  | { type: 'usage'; inputTokens: number; outputTokens: number; credits: number }
  | { type: 'error'; message: string }
  | ({ type: 'privacy_route' } & PrivacyRouting)
  | { type: 'privacy_route'; action: 'mask_external'; source: 'tool_output'; count: number }
  | ({ type: 'model_route' } & CostRouting)
  /** Model comparison: one column's text, then that column's final bill. */
  | { type: 'variant'; model: string; text: string; actualModel?: string }
  | {
      type: 'variant_done'
      model: string
      routedModel?: string
      actualModel?: string
      credits: number
      inputTokens: number
      outputTokens: number
      error: string | null
    }
  | { type: 'done'; credits?: number }

/**
 * Chat, report, and slides all stream from the same endpoint. Reports emit
 * `section` events so the panel can render each section the moment it completes
 * instead of waiting for the whole document.
 */
export async function* streamSession(
  sessionId: string,
  payload: {
    content: string
    /** File ids from `filesApi.upload` — the server reads their extracted text. */
    attachments?: string[]
    webSearch?: boolean
    model?: string
    activatedSkillIds?: string[]
    /** A rendering template. Sticky on the session; `''` clears it. */
    renderTemplateId?: string
    /**
     * A 시작점, carried by this turn the way an activated skill is. Never
     * sticky — unlike `renderTemplateId` it is not stored on the session,
     * because a starting point starts one turn and then it is over.
     */
    startingTemplateId?: string
    privacyAction?: PrivacyAction
    privacyDecisionToken?: string
  },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  yield* postStream(`/sessions/${sessionId}/messages`, payload, signal)
}

/**
 * Model comparison. Two or three real completions, interleaved on one
 * connection so the turn is stored and billed as one thing.
 */
export async function* streamComparison(
  sessionId: string,
  payload: {
    content: string
    models: string[]
    activatedSkillIds?: string[]
    startingTemplateId?: string
    attachments?: string[]
    privacyAction?: PrivacyAction
    privacyDecisionToken?: string
  },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  yield* postStream(`/sessions/${sessionId}/compare`, payload, signal)
}

async function* postStream(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    signal,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify(payload),
  })
  // A refused turn (no credits, unbuilt surface) answers with JSON, not a
  // stream. Surfacing it as an ApiError keeps the caller's error path uniform.
  if (!res.ok) {
    let detail: string | null = null
    if (res.status === 409) {
      try {
        const payload = (await res.json()) as PrivacyDecision & { detail?: unknown }
        if (payload.code === 'privacy_decision_required') throw new PrivacyDecisionError(payload)
        if (typeof payload.detail === 'string') detail = payload.detail
        else if (Array.isArray(payload.detail)) {
          detail = (payload.detail[0] as { msg?: string } | undefined)?.msg ?? 'invalid_request'
        }
      } catch (error) {
        if (error instanceof PrivacyDecisionError) throw error
      }
    }
    // A 409 body was consumed above; reading it twice loses the useful code
    // and turns `auto_quality_model_required` into the generic `http_409`.
    const resolved = detail ?? (res.status === 409 ? `http_${res.status}` : await readDetail(res))
    if (res.status === 401) throw new UnauthorizedError(resolved)
    throw new ApiError(res.status, resolved)
  }
  if (!res.body) throw new Error('no response body')

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += value
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const raw = line.slice(6).trim()
      if (raw === '[DONE]') return
      yield JSON.parse(raw) as StreamEvent
    }
  }
}

/**
 * Job progress for image and audio/video. A single connection multiplexes every
 * job the user owns, so the sidebar and home page stay live without polling.
 */
