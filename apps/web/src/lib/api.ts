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
  PendingPlan,
  PendingQuestion,
  PrivacyAction,
  PrivacyRouting,
  CostRouting,
  DesignTokens,
  Session,
  SessionKind,
  SessionMade,
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
 * A 4xx `detail` is written for the person who made the request; a 5xx one is
 * written for whoever reads the logs, and so is a network error's message.
 */
/** A bare status code, which is what `readDetail` falls back to. */
/**
 * A machine code rather than a sentence: `not_found`, `upstream_failed`,
 * `http_502`. This API answers 4xx with them by design, and `readDetail`
 * invents one when the body is not JSON at all.
 *
 * Recognised by shape rather than by a list, because the list grows: a code is
 * lowercase ASCII with underscores and no spaces, and a sentence written for a
 * reader has neither property — it has spaces, or it is Korean.
 */
const MACHINE_CODE = /^[a-z][a-z0-9_]*$/

/**
 * What to put on screen for a failed request.
 *
 * A 5xx `detail` is shown when the API wrote it: an image route answers 502
 * carrying the reason a picture was refused, and a generic fallback would
 * throw away the only sentence that said why.
 *
 * What never reaches a reader is a machine code — `upstream_failed`,
 * `not_found`, or the `http_502` `readDetail` invents when a gateway between
 * here and the API answers with something that is not JSON. Not messages; the
 * absence of one.
 */
/**
 * The machine code behind a failure, for the callers that have to branch on it.
 *
 * Separate from `errorMessage` on purpose: one answers "what do I put on the
 * screen", the other "which failure was this". A caller that reads the screen
 * string and compares it to a code gets whichever the humanising rule happened
 * to let through.
 */
export function errorCode(err: unknown): string {
  if (err instanceof ApiError && err.detail && MACHINE_CODE.test(err.detail)) {
    return err.detail
  }
  return ''
}

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.detail && !MACHINE_CODE.test(err.detail)) {
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

/**
 * The turn stopped arriving.
 *
 * Not an error the server sent — it is the absence of one. A model backend
 * that accepts the request and then never answers leaves the connection open
 * with nothing on it, and `reader.read()` waits for as long as that lasts,
 * which is why a conversation could sit on 생각하는 중… until the tab was
 * closed. A stall is now a failure like any other: it ends the turn, says what
 * happened, and leaves a retry.
 */
export class StreamStalledError extends ApiError {
  constructor(detail = 'stream_stalled') {
    super(504, detail)
    this.name = 'StreamStalledError'
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
    /** The upgrade lane shares the switch and the classifier; only the
     *  candidate list is its own. Coarse: whether a candidate is usable also
     *  depends on the model the turn is already on, which the catalogue cannot
     *  know — the server decides that per turn. */
    qualityAvailable: boolean
    qualityReason: 'disabled' | 'classifier_unavailable' | 'no_quality_models' | null
    qualityModelIds: string[]
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
      brand: { name: string; logo: string }
      enabledKinds: string[]
      privacy: { externalDataGuard: boolean; allowUserRawExternal: boolean }
      /** Minutes of inactivity before the browser ends the session. 0 is off. */
      idleTimeoutMinutes: number
    }>('/auth/config'),
  forgotPassword: (email: string) => call<void>('/auth/password/forgot', body({ email })),
  resetPassword: (token: string, newPassword: string) =>
    call<void>('/auth/password/reset', body({ token, newPassword })),
}

export interface MyUsage {
  days: number
  /** `otherCredits` is spend no single model can be named for — a comparison
   *  that ran several on one charge — not the part the breakdown forgot. */
  totals: { credits: number; requests: number; otherCredits: number }
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
  totals: {
    credits: number
    requests: number
    activeUsers: number
    allocatedCredits: number
    otherCredits: number
  }
  daily: { date: string; credits: number; requests: number }[]
  byModel: { model: string; credits: number; requests: number; users: number }[]
  /** `'other'` beside the five surfaces: spend charged against no session,
   *  which is how the bars keep adding up to the total. */
  bySurface: { kind: SessionKind | 'other'; credits: number; requests: number }[]
  topUsers: { id: string; name: string; email: string; credits: number; allowance: number }[]
}

/** One line of somebody's own 접속기록. */
export interface AccessEventRow {
  id: string
  at: string
  action: string
  detail: string
  ip: string
  /** Empty unless the server has a GeoLite2 database. Never a guess. */
  region: string
  userAgent: string
  severity: string
}

/** One browser this account is currently signed in on. */
export interface ActiveSessionRow {
  /** The refresh-token family. Stable for the life of the sign-in. */
  familyId: string
  startedAt: string
  lastSeenAt: string
  expiresAt: string
  ip: string
  /** Empty unless the server has a GeoLite2 database. Never a guess. */
  region: string
  userAgent: string
  /** The session this screen is being read from. Ending it signs you out. */
  current: boolean
}

export const accessApi = {
  mine: () => call<AccessEventRow[]>('/auth/me/access'),
  sessions: () => call<ActiveSessionRow[]>('/auth/me/sessions'),
  endSession: (familyId: string) =>
    call<{ revoked: number }>(`/auth/me/sessions/${familyId}`, { method: 'DELETE' }),
  /** Everywhere but here. */
  endOtherSessions: () =>
    call<{ revoked: number }>('/auth/me/sessions/revoke-others', { method: 'POST' }),
}

export interface AuditRow {
  id: string
  at: string
  actor: string
  action: string
  target: string
  detail: string
  ip: string
  /** Empty unless the server has a GeoLite2 database. Never a guess. */
  region: string
  userAgent: string
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
  /** Minutes of inactivity before a browser signs itself out. 0 is off. */
  idleTimeoutMinutes: number
  adaptiveRoutingEnabled: boolean
  adaptiveClassifierModelId: string | null
  adaptiveEconomyModelIds: string[]
  adaptiveQualityEnabled: boolean
  adaptiveQualityModelIds: string[]
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
 * The 서식 as a blank file to fill in by hand.
 *
 * Fetched rather than linked. The API takes a bearer token this app holds in
 * memory, so an `<a href>` to the route arrives unauthenticated and comes back
 * a 401 the browser renders as a broken download.
 */
export async function downloadDesignTemplateForm(id: string, name: string, format: string) {
  const res = await fetch(`${BASE_URL}/design-templates/${encodeURIComponent(id)}/form`, {
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) throw new ApiError(res.status, await readDetail(res))

  const url = URL.createObjectURL(await res.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${name.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'form'}.${format}`
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
/**
 * What shaped a shared conversation: three names and nothing else.
 *
 * Never bodies — an agent's system prompt and a project's instructions are the
 * owner's workspace, not part of what a share token buys.
 */
export interface SharedContext {
  agent: string | null
  project: string | null
  format: { name: string; nameEn: string } | null
}

export type SharedPayload =
  | { kind: 'artifact'; title: string; artifactKind: string; data: unknown; updatedAt: string }
  | {
      kind: 'session'
      title: string
      sessionKind: string
      messages: MessageRow[]
      /** Absent on a share minted before this travelled. */
      startedWith?: SharedContext | null
      /** What the conversation produced, when it produced something. */
      artifact: {
        title: string
        artifactKind: string
        data: unknown
        updatedAt: string
      } | null
      updatedAt: string
    }

/** One visit to a shared link. */
export interface ShareViewRow {
  id: string
  at: string
  lastAt: string
  opens: number
  /** Empty for a reader with no account here — see `ip`. */
  name: string
  email: string
  /** Empty when the proxy did not forward an address. */
  ip: string
  /** Empty unless the server has a GeoLite2 database. Never a guess. */
  region: string
  userAgent: string
}

export const sharesApi = {
  list: () => call<ShareRow[]>('/shares'),
  create: (payload: { artifactId?: string; sessionId?: string; scope: 'workspace' | 'link' }) =>
    call<ShareRow>('/shares', body(payload)),
  revoke: (id: string) => call<void>(`/shares/${id}`, { method: 'DELETE' }),
  /** Who has opened this link. Owner only; a revoked link keeps its visits. */
  views: (id: string) => call<ShareViewRow[]>(`/shares/${id}/views`),
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
  /**
   * What this turn made, where what it made is the answer — a picture, a clip,
   * a piece of speech. Null on every turn that answered in words.
   */
  artifactIds: string[] | null
  /** How the turn ended when it did not end in an answer. */
  failure: 'no_answer' | 'interrupted' | null
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
  /** A generation waiting to be answered or approved, or null. */
  pending: PendingPlan | null
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
  /** What the session produced. Sent only where there is no transcript. */
  made: SessionMade | null
}

export const sessionsApi = {
    /**
     * Asks the turn on this session to stop where it is.
     *
     * Sent before the fetch is aborted, not instead of it: a closed socket is
     * also what a changed tab looks like, and the server keeps generating for
     * that one.
     */
  stop: (sessionId: string) => call<void>(`/sessions/${sessionId}/stop`, { method: 'POST' }),
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
  deleteMany: (payload: { ids?: string[]; all?: boolean; artifacts?: boolean }) =>
    call<{ deleted: number; artifactsDeleted: number }>('/sessions/delete', body(payload)),
  /** Pictures. Synchronous: the upstream is a completion whose answer is a
   *  PNG, so there is nothing to poll. */
  /**
   * What picture to put here, proposed rather than asked for.
   *
   * Draws nothing and costs nothing: two lines of text the person then edits,
   * replaces or ignores. The credit is spent by `images` below.
   */
  suggestFigure: (
    sessionId: string,
    payload: { title?: string; about?: string; context?: string },
  ) =>
    call<{ caption: string; prompt: string }>(
      `/sessions/${sessionId}/figure-suggestion`,
      body(payload),
    ),
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
      /**
       * Asked for from inside a document rather than from the image surface.
       * Tells the server the picture goes *into* a slide or a section, so it
       * comes back as a figure and not as a picture of a whole slide.
       */
      figure?: boolean
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
      /** Which project it belongs to. `null` takes it out of every project. */
      projectId?: string | null
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
  /**
   * Opens an uploaded `.hwpx` as an editable document.
   *
   * Reads the file — headings, tables and lists — into an ordinary report, so
   * it lands in the same editor everything else does and exports back to
   * `.hwpx` through the exporter that was already there. Nothing is generated
   * and nothing is charged. Returns the new session's id.
   */
  openAsDocument: (id: string) =>
    call<{ id: string }>(`/files/${id}/open-as-document`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/files/${id}`, { method: 'DELETE' }),
}

/**
 * Hands a stored file back to whoever uploaded it, under its own name.
 *
 * A fetch and a blob rather than a link on `downloadUrl`: a click can carry
 * the Authorization header an `<img>` cannot, so the token stays out of the
 * URL and out of the proxy's access log.
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
  /** Several at once. Ids this account does not own are skipped. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/projects/delete', body({ ids })),
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
  /** Several at once. Ids this account does not own are skipped. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/artifacts/delete', body({ ids })),
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
  /** The same check on one report section. Same cost, same verdict shape. */
  factcheckSection: (id: string, sectionId: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/factcheck`, body({ sectionId })),
  /**
   * The picture this browser made of one mermaid diagram.
   *
   * Mermaid renders in JavaScript and the API has no headless browser, so the
   * reader's own render is the only one there will ever be. Stored so the
   * exports carry a figure where the source stands. Free, and takes no version
   * — opening a document is not editing it.
   */
  storeDiagram: (id: string, sectionId: string, key: string, src: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/diagram`, body({ sectionId, key, src })),
  /** Rewrites one section. Costs a model call and snapshots the old text. */
  rewriteSection: (id: string, sectionId: string, note: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/rewrite`, body({ sectionId, note })),
  /** The deck's half of the same thing, slide by slide. */
  rewriteSlide: (id: string, slideId: string, note: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/rewrite`, body({ slideId, note })),
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
  /**
   * Puts a picture made in this workspace into one section of a report.
   *
   * A report is Markdown and a Markdown picture is a shape every exporter
   * already reads, so the server appends a line rather than adding a field.
   */
  addSectionImage: (id: string, sectionId: string, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/image`, body({ sectionId, artifactId, caption })),
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
 * Server-side rather than bundled: a template the turn carries by id has to be
 * one the server can resolve.
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

//: Defined in `@/types` — the artifact types need it and this file already
//: imports from there, so the definition lives on the side with no cycle.
export type { DesignTokens }

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
  /** Several at once. Ids this account does not own are skipped. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/designs/delete', body({ ids })),
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
  /**
   * The extension of the blank form this 서식 ships — `docx`, `pptx`, or empty
   * where it has none. The card offers it by name, because "양식 내려받기" and
   * then a `.pptx` when somebody expected a `.docx` is a surprise the card
   * could have prevented.
   */
  formFormat: string
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

/** A template's CSS and its wrappers, for the document editor's shadow root. */
export interface TemplateStyle {
  css: string
  wrapCover: string
  wrapBlock: string
  wrapGroup: string
}

/** How often each rendering template has been started. Ids to counts. */
export interface DesignTemplateUsage {
  /** By this person. Empty on their first day, which is what `popular` is for. */
  mine: Record<string, number>
  /** Across the installation. An aggregate over a catalogue that ships in the image. */
  popular: Record<string, number>
}

export const designTemplatesApi = {
  list: (surface?: SessionKind) =>
    call<DesignTemplateRow[]>(`/design-templates${surface ? `?surface=${surface}` : ''}`),
  usage: () => call<DesignTemplateUsage>('/design-templates/usage'),
  /**
   * The stylesheet the editor draws the document in.
   *
   * The gallery card gets a finished document at `/preview` and shows it in a
   * sandboxed iframe, which is right for something nobody clicks. An editor is
   * clicked, so the document lives in the page inside a shadow root — and a
   * shadow root takes a stylesheet, not a URL.
   */
  style: (id: string, tokens?: DesignTokens | null) => {
    const query = tokens
      ? `?${new URLSearchParams({
          accent: tokens.accent,
          ink: tokens.ink,
          muted: tokens.muted,
          font: tokens.font,
        })}`
      : ''
    return call<TemplateStyle>(`/design-templates/${encodeURIComponent(id)}/style${query}`)
  },
}

export const designTokensOf = (
  designs: DesignRow[],
  designSystemId: string | null | undefined,
): DesignTokens | null => designs.find((d) => d.id === designSystemId)?.tokens ?? null

export const skillsApi = {
  /** Several at once. Ids this account does not own are skipped. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/skills/delete', body({ ids })),
  list: () => call<SkillRow[]>('/skills'),
  create: (payload: Partial<SkillRow> & { name: string }) =>
    call<SkillRow>('/skills', body(payload)),
  update: (id: string, patch: Partial<SkillRow>) =>
    call<SkillRow>(`/skills/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  toggle: (id: string) => call<SkillRow>(`/skills/${id}/toggle`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/skills/${id}`, { method: 'DELETE' }),
  /** Shared by the rest of the workspace, minus whatever is already yours. */
  store: () => call<StoreSkillRow[]>('/skills/store'),
  /** Takes a copy. Idempotent — a second press returns the copy you have. */
  install: (id: string) => call<SkillRow>(`/skills/${id}/install`, { method: 'POST' }),
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
  visibility: string
  installs: number
  originId: string | null
  updatedAt: string
}

export interface StoreSkillRow extends SkillRow {
  ownerId: string
  ownerName: string
  official: boolean
  installed: boolean
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
  /** Several at once. Ids this account does not own are skipped. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/agents/delete', body({ ids })),
  list: () => call<AgentRow[]>('/agents'),
  create: (payload: Partial<AgentRow> & { name: string }) =>
    call<AgentRow>('/agents', body(payload)),
  update: (id: string, patch: Partial<AgentRow>) =>
    call<AgentRow>(`/agents/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/agents/${id}`, { method: 'DELETE' }),
  /**
   * Takes a copy of a shared agent, with the shared skills it runs on.
   *
   * Server-side because the copy is more than the prompt: the skill allow-list
   * is a list of rows in the author's account, and the copy needs its own.
   */
  install: (id: string) => call<AgentRow>(`/agents/${id}/install`, { method: 'POST' }),

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
  catalogKey: string | null
  originId: string | null
  official: boolean
  installed: boolean
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
  /** Several at once. Credentials go with the rows they belong to. */
  removeMany: (ids: string[]) =>
    call<{ deleted: number }>('/connectors/delete', body({ ids })),
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
  /** The outline a document intends to write, offered before it writes it. */
  | { type: 'proposal'; plan: NonNullable<PendingPlan['plan']> }
  /** What it needs to know before it can plan at all. */
  | { type: 'needs'; questions: PendingQuestion[] }
  /** The reference shelf a report's sections cite from, sent once, up front. */
  | { type: 'sources'; sources: Source[] }
  | {
      type: 'artifact'
      artifactId: string
      /**
       * Whether the model set out to make this rather than the server keeping
       * a long fence out of the answer. Only the first opens the panel: a
       * nine-line example is not worth two thirds of the screen.
       */
      deliberate?: boolean
    }
  | { type: 'usage'; inputTokens: number; outputTokens: number; credits: number }
  | { type: 'error'; message: string; code?: string; reason?: string }
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
  /** `messageId` is the stored answer's id — the one every later call must use. */
  | { type: 'done'; credits?: number; messageId?: string }

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
    /** Write the outline waiting on this session instead of planning another. */
    approve?: boolean
    /**
     * The outline as the person edited it on the card. Sanitised server-side:
     * titles and order are theirs, layouts stay the planner's.
     */
    plan?: Record<string, unknown>
    /** The figure card's answer — the second of the two questions. */
    includeFigures?: boolean
    /** Answers to a stopped turn's questions, keyed by question id. */
    answers?: Record<string, string>
    /**
     * The failed question to run again in place, by message id. The server
     * reuses that row and replaces what failed under it, so the transcript
     * keeps one copy of the question.
     */
    retryOf?: string
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

/**
 * How long a turn may produce nothing at all before it is called stalled.
 *
 * Generous on purpose. A large local model can take a while to reach its first
 * token, and a tool call is a round trip to something else — so this is not a
 * response-time budget, it is the point past which silence stops being slow
 * and starts being broken. The server emits deltas and step events throughout
 * a healthy turn, and sends no heartbeat, so silence here really is silence.
 */
const STALL_MS = 120_000

/** Rejects when the stream has produced nothing for {@link STALL_MS}. */
function withStallGuard<T>(work: Promise<T>, onStall: () => void): Promise<T> {
  let timer: ReturnType<typeof setTimeout>
  return Promise.race([
    work,
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        // Closing the reader is what actually frees the connection; rejecting
        // alone would leave the request running behind an abandoned generator.
        onStall()
        reject(new StreamStalledError())
      }, STALL_MS)
    }),
  ]).finally(() => clearTimeout(timer)) as Promise<T>
}

async function* postStream(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  // The stop button's signal, plus one of our own so the stall guard has
  // something to pull. Forwarded rather than replaced: 중단 must still reach the
  // request, and the server tells 중단 from a dropped tab by it.
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()

  const res = await withStallGuard(
    fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      signal: controller.signal,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(payload),
    }),
    abort,
  )
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
  /*
   * A turn that stopped to ask answers in JSON, not as a stream.
   *
   * The server takes that route on purpose — see `_ask_before_writing`: a
   * one-event SSE would leave the browser holding a socket open to be told
   * that nothing is coming. But the caller here is a stream reader, and a JSON
   * body has no `data:` lines in it, so the loop below fell out having seen no
   * `usage` event and the runner marked the turn cut off. Every clarifying
   * question therefore arrived under 연결이 끊겨 답변이 중간에 멈췄습니다 and a
   * 다시 시도 button — an error message on the one screen that is working
   * exactly as designed.
   *
   * Translated here rather than in each of the four runners, which all read
   * this generator and all handle these events already.
   */
  if (res.headers.get('content-type')?.includes('application/json')) {
    const answered = (await res.json()) as { pending?: PendingPlan | null; message?: string }
    const pending = answered.pending
    // What the turn said, before what it is asking. Without it the answer
    // bubble stays empty and the panel draws 생각하는 중 into it — a spinner
    // over a turn that has already stopped and is waiting for a person.
    if (answered.message) yield { type: 'delta', text: answered.message }
    if (pending?.stage === 'clarify' && pending.questions) {
      yield { type: 'needs', questions: pending.questions }
    } else if (pending?.stage === 'outline' && pending.plan) {
      yield { type: 'proposal', plan: pending.plan }
    } else {
      // Any other shape: the turn is over and produced nothing to stream.
      // Said so, because falling out of this generator silently is what the
      // runners read as a dropped connection.
      yield { type: 'done' }
    }
    return
  }

  if (!res.body) throw new Error('no response body')

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await withStallGuard(reader.read(), abort)
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
  } finally {
    signal?.removeEventListener('abort', abort)
  }
}

/**
 * Job progress for image and audio/video. A single connection multiplexes every
 * job the user owns, so the sidebar and home page stay live without polling.
 */
