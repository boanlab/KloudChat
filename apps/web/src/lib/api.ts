/**
 * The seam between the app and its backend.
 *
 * The browser talks to the KloudChat API and nothing else — never to LiteLLM. The
 * master key and every virtual key stay on the server.
 */

import type {
  AgentDraft,
  AgentProcess,
  ArtifactKind,
  Bundle,
  BundleSelection,
  ProcessMode,
  CostRouting,
  DesignTokens,
  DocMarks,
  PipelineCatalogue,
  PipelineDefinition,
  PipelineStep,
  PipelineVerdict,
  UserPipeline,
  PipelineRun,
  PipelineStage,
  EarlierAnswer,
  FreshnessAbstention,
  LengthCheck,
  Message,
  MessageRouting,
  ModelInfo,
  PendingPlan,
  PendingQuestion,
  Preferences,
  PrivacyAction,
  PrivacyRouting,
  ReportArtifact,
  Session,
  SessionKind,
  SessionMade,
  GapQuestion,
  ProcessCatalogue,
  SkillScript,
  SkillServer,
  Slide,
  Source,
  Step,
  ToolResultAnswer,
  User,
} from '@/types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/**
 * In memory only; the refresh token is an httpOnly cookie. A reload starts with
 * no token and recovers via `auth.refresh()` — see `bootstrap()` in the store.
 */
let accessToken: string | null = null
/** The web-search toggle as sent: on, off, or left to the words. */
export type WebSearchSetting = boolean | 'auto'

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

/** Shape of a machine code (`not_found`, `http_502`): a sentence for a reader has spaces or is Korean. */
const MACHINE_CODE = /^[a-z][a-z0-9_]*$/

/** The machine code behind a failure, or `''`. */
export function errorCode(err: unknown): string {
  if (err instanceof ApiError && err.detail && MACHINE_CODE.test(err.detail)) {
    return err.detail
  }
  return ''
}

/**
 * Text for the screen: a 4xx `detail` that is a sentence, else `fallback`.
 * 5xx bodies may carry proxy or exception text and never reach a reader.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (
    err instanceof ApiError &&
    err.status < 500 &&
    err.detail &&
    !MACHINE_CODE.test(err.detail)
  ) {
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

/** The stream produced nothing for `STALL_MS`; ends the turn like any other failure. */
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
interface SignupResult {
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
  /** Per-surface instance default; absent keys fall back to `defaultChatModel`. */
  defaultModelByKind?: Partial<Record<SessionKind, string>>
  /** 오디오/동영상 is one surface with two kinds of model, so one default each. */
  defaultAvModelByMode?: Partial<Record<'audio' | 'video', string>>
  autoRouting: {
    enabled: boolean
    available: boolean
    reason: 'disabled' | 'classifier_unavailable' | 'no_economy_models' | null
    classifierModelId: string | null
    economyModelIds: string[]
    /** Coarse: whether a candidate is usable also depends on the turn's current model, decided server-side. */
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
  /** Where 관리자에게 문의 goes; `admin` means the first administrator's own. */
  contact: { email: string; source: 'database' | 'admin' }
  /** Enabled surfaces. Chat is always included. */
  enabledKinds: string[]
  /** Who may register: the mode, the mail domains allowed (empty = any), and
   *  whether the address is confirmed by a mailed link first. */
  signup: {
    mode: 'open' | 'approval' | 'closed'
    modeSource: 'database' | 'environment'
    domains: string[]
    verifyEmail: boolean
    /** `verifyEmail` with a mail server behind it. */
    verificationActive: boolean
  }
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

/** The composer's microphone: a recording in, its words out. Not stored. */
export const transcriptionsApi = {
  transcribe: async (
    blob: Blob,
    filename = 'speech.webm',
    hints: { language?: 'ko' | 'en'; prompt?: string } = {},
  ) => {
    // multipart: the browser sets the boundary, so this bypasses call()'s JSON headers.
    const form = new FormData()
    form.append('file', blob, filename)
    // Whisper reads `prompt` as a vocabulary hint and `language` as a pin.
    if (hints.language) form.append('language', hints.language)
    if (hints.prompt) form.append('prompt', hints.prompt.slice(0, 500))
    const res = await fetch(`${BASE_URL}/transcriptions`, {
      method: 'POST',
      body: form,
      credentials: 'include',
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
    if (!res.ok) throw new ApiError(res.status, await readDetail(res))
    return (await res.json()) as { text: string; seconds: number }
  },
}

export const authConfig = {
  /** What the sign-in page may offer. Public because the browser cannot read
   *  the admin settings, and a dead reset link is worse than none. */
  get: () =>
    call<{
      passwordResetEnabled: boolean
      /** A Whisper backend is configured, so the composer may show a microphone. */
      dictationEnabled: boolean
      brand: { name: string; logo: string }
      /** Where 관리자에게 문의 goes. Empty when nobody can be named. */
      contactEmail: string
      enabledKinds: string[]
      privacy: { externalDataGuard: boolean; allowUserRawExternal: boolean }
      /** Minutes of inactivity before the browser ends the session. 0 is off. */
      idleTimeoutMinutes: number
      /** What the signup form should say before somebody is refused. */
      signup: { mode: 'open' | 'approval' | 'closed'; domains: string[]; emailVerification: boolean }
    }>('/auth/config'),
  forgotPassword: (email: string) => call<void>('/auth/password/forgot', body({ email })),
  resetPassword: (token: string, newPassword: string) =>
    call<void>('/auth/password/reset', body({ token, newPassword })),
  /** The mailed signup link. A session comes back when verifying was the last step. */
  verifyEmail: (token: string) =>
    call<{ status: 'active' | 'pending' | 'suspended'; session: AuthSession | null }>(
      '/auth/verify-email',
      body({ token }),
    ),
  resendVerification: () => call<void>('/auth/verify-email/resend', { method: 'POST' }),
}

export interface MyUsage {
  days: number
  /** `otherCredits` is spend no single model can be named for — a comparison
   *  that ran several on one charge — not the part the breakdown forgot. */
  totals: { credits: number; requests: number; searches: number; otherCredits: number }
  /** This month's allowance and what is left of it. */
  cycle: { allowance: number; used: number; remaining: number }
  daily: { date: string; credits: number; requests: number; searches: number }[]
  byModel: { model: string; credits: number; requests: number; units?: number; unit?: string }[]
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
    contactEmail?: string
    signupMode?: string
    signupDomains?: string
    signupVerifyEmail?: string
  }) => call<SystemSettings>('/admin/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  testSettings: () =>
    call<{ ok: boolean; models?: number; detail?: string }>('/admin/settings/test', {
      method: 'POST',
    }),
  /** Logo image. multipart, so it does not go through call()'s JSON path. */
  uploadLogo: async (file: File) => {
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
  /** Removes the account and everything it owns; not reversible. `purgeFiles` takes its uploads and media too. */
  removeUser: (id: string, purgeFiles = true) =>
    call<void>(`/admin/users/${id}?purgeFiles=${purgeFiles ? 'true' : 'false'}`, {
      method: 'DELETE',
    }),
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
  /** Corrects a name or address; a field left out is left alone. */
  updateUser: (id: string, patch: { name?: string; email?: string }) =>
    call<User>(`/admin/users/${id}`, { ...body(patch), method: 'PATCH' }),
  /** Sets a new password and signs the account out everywhere. */
  resetPassword: (id: string, password: string) =>
    call<User>(`/admin/users/${id}/password`, body({ password })),
  /** The whole catalogue, for the restriction picker: not narrowed to the administrator's own list. */
  catalogue: () => call<{ id: string; label: string; kinds: string[] }[]>('/admin/models'),
  /** Every key an account holds: KloudChat's own (preview only) and the ones the person issued. */
  userKeys: (id: string) => call<AdminKeys>(`/admin/users/${id}/keys`),
  /** Revokes one key the person issued. */
  revokeUserKey: (id: string, keyId: string) =>
    call<void>(`/admin/users/${id}/keys/${keyId}`, { method: 'DELETE' }),
  /** Binds a LiteLLM key the administrator already holds as the account's KloudChat key. */
  replaceLitellmKey: (id: string, key: string) =>
    call<User>(`/admin/users/${id}/litellm-key`, { ...body({ key }), method: 'PUT' }),
}

export interface AdminKeys {
  kloudchat: { preview: string | null; issuedAt: string | null } | null
  named: { id: string; name: string; preview: string; createdAt: string; lastUsedAt: string | null }[]
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
    /** Web searches the turns ran, against the search engines' quotas. */
    searches: number
    activeUsers: number
    allocatedCredits: number
    otherCredits: number
  }
  daily: { date: string; credits: number; requests: number; searches: number }[]
  byModel: {
    model: string
    credits: number
    requests: number
    users: number
    /** Work a free model did, measured — seconds of speech, chunks embedded. */
    units?: number
    unit?: string
  }[]
  /** `'other'` beside the five surfaces: spend charged against no session,
   *  which is how the bars keep adding up to the total. */
  bySurface: { kind: SessionKind | 'other'; credits: number; requests: number }[]
  /** Every account with activity in the window, most spent first. */
  topUsers: {
    id: string
    name: string
    email: string
    credits: number
    requests: number
    searches: number
    allowance: number
  }[]
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

/** One page of a settings list, newest first. */
export interface AccessPage<T> {
  items: T[]
  total: number
  page: number
  size: number
}

/** The caller's own sign-in leads page 1; `others` counts every sign-in but that one. */
export interface ActiveSessionPage extends AccessPage<ActiveSessionRow> {
  others: number
}

export const accessApi = {
  mine: (page = 1, size = 20) =>
    call<AccessPage<AccessEventRow>>(`/auth/me/access?page=${page}&size=${size}`),
  sessions: (page = 1, size = 10) =>
    call<ActiveSessionPage>(`/auth/me/sessions?page=${page}&size=${size}`),
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
  /** `changes` is `{field: [old, new]}` on events written since the diff; secrets come masked. */
  metadata?: (Record<string, unknown> & { changes?: Record<string, [unknown, unknown]> }) | null
}

export interface AuditPage {
  items: AuditRow[]
  total: number
  page: number
  size: number
}

export interface AuditQuery {
  page: number
  size: number
  /** Comma list of codes; `x.*` takes a prefix. */
  action?: string
  account?: string
  q?: string
  /** Codes whose screen label matched `q`, searched with it. */
  qActions?: string
  /** ISO instants; `until` is exclusive. */
  since?: string
  until?: string
}

export interface GovernancePolicy {
  piiMasking: boolean
  externalDataGuard: boolean
  /** A turn carrying internal material (attachments, project files, memories) runs strict-local. */
  internalDataStrictLocal: boolean
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

/** One export of an owned artifact, as the response, for a download or a reuse. */
export async function fetchArtifactExport(
  id: string,
  format: 'docx' | 'pdf' | 'hwpx' | 'pptx' | 'md' | 'html' | 'source',
): Promise<Response> {
  const res = await fetch(`${BASE_URL}/artifacts/${id}/export?format=${format}`, {
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) throw new ApiError(res.status, await readDetail(res))
  return res
}

/**
 * Downloads an artifact export. A plain link cannot carry the access token, so
 * the file is fetched and handed to the browser as a blob.
 */
export async function downloadArtifact(
  id: string,
  format: 'docx' | 'pdf' | 'hwpx' | 'pptx' | 'md' | 'html' | 'source',
  title: string,
) {
  const res = await fetchArtifactExport(id, format)

  let filename = `${title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'report'}.${format === 'source' ? 'txt' : format}`
  if (format === 'source') {
    // The owner-scoped endpoint chooses the extension from stored language,
    // never from content sniffing in the browser.
    const encoded = res.headers.get('Content-Disposition')?.match(/(?:^|;)\s*filename\*=UTF-8''([^;]+)/i)?.[1]
    if (encoded) {
      try {
        filename = decodeURIComponent(encoded).replace(/[\\/:*?"<>|]+/g, '_').slice(0, 180) || filename
      } catch {
        // An invalid or absent filename keeps the conservative text fallback.
      }
    }
  }
  const url = URL.createObjectURL(await res.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
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

/** What shaped a shared conversation: names only, never prompt or instruction bodies. */
export interface SharedContext {
  agent: string | null
  project: string | null
  format: { name: string; nameEn: string } | null
}

/** What the public page gets. Deliberately narrow — see routers/shares.py. */
export type SharedPayload =
  | { kind: 'artifact'; title: string; artifactKind: string; data: unknown; updatedAt: string }
  | {
      kind: 'session'
      title: string
      sessionKind: string
      messages: MessageRow[]
      /** Absent on older shares. */
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

export interface StorageReport {
  path: string
  usedBytes: number
  files: number
  diskTotalBytes: number
  diskFreeBytes: number
  /** Fill (used / total) past which deleted accounts' files are swept, oldest first. 0 = off. */
  reclaimAt: number
  /** What a sweep would remove: files under directories whose account is gone. */
  orphanBytes: number
  orphanFiles: number
  byUser: { id: string; name: string; email: string; bytes: number; files: number }[]
}

export const usageApi = {
  governance: () => call<GovernancePolicy>('/admin/governance'),
  setGovernance: (patch: Partial<GovernancePolicy>) =>
    call<{ ok: boolean; clearedMessages: number }>('/admin/governance', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  report: (days = 7) => call<UsageReport>(`/admin/usage?days=${days}`),
  /** Disk the uploads and generated media take, per account, and what is left. */
  storage: () => call<StorageReport>('/admin/storage'),
  /** Removes every deleted account's files now, without waiting for the fill mark. */
  reclaimStorage: () =>
    call<{ freedBytes: number; freedFiles: number; fillBefore: number; fillAfter: number }>(
      '/admin/storage/reclaim',
      { method: 'POST' },
    ),
  audit: (query: AuditQuery) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    return call<AuditPage>(`/admin/audit?${params}`)
  },
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
  /** Stored on turns from the retired model comparison; read only for their text. */
  variants?: { content: string }[] | null
  /** Answers a rerun replaced, oldest first. */
  superseded?: EarlierAnswer[] | null
  usage: Message['usage'] | null
  model: string | null
  routing: MessageRouting | null
  /** Legacy: the retired 시작점 an old turn began from, with its title as it read then. */
  startedFrom: { templateId: string; title: string } | null
  /** What the reader thought of this answer. Null until somebody says. */
  rating: 'up' | 'down' | null
  /** Artifacts that are the answer (a picture, a clip); null when the turn answered in words. */
  artifactIds: string[] | null
  /** The version of `artifactIds[0]` this turn wrote, from its pipeline run; absent when unknown. */
  artifactVersion?: number | null
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
  pipelineId?: string | null
  model: string
  routingMode: Session['routingMode']
  artifactId: string | null
  /** A generation waiting to be answered or approved, or null. */
  pending: PendingPlan | null
  skillIds?: string[] | null
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
  /** 「에이전트로 저장」: this conversation as a draft agent. Nothing is saved. */
  agentDraft: (sessionId: string) => call<AgentDraft>(`/sessions/${sessionId}/agent-draft`),
  /** A report or slides session continuing this chat with its agent; the files carry the conversation. */
  handoff: (sessionId: string, kind: 'report' | 'slides') =>
    call<{ session: SessionRow; attachments: FileRow[] }>(
      `/sessions/${sessionId}/handoff`, body({ kind }),
    ),
  forkBeforeMessage: (sessionId: string, messageId: string) =>
    call<{ session: SessionRow; attachments: FileRow[]; attachmentIdMap: Record<string, string> }>(
      `/sessions/${sessionId}/messages/${messageId}/fork`, { method: 'POST' },
    ),
  /** Asks the running turn to stop. Sent before the fetch is aborted: a closed socket alone looks like a changed tab. */
  stop: (sessionId: string) => call<void>(`/sessions/${sessionId}/stop`, { method: 'POST' }),
  list: (params?: { kind?: string; projectId?: string }) =>
    call<SessionRow[]>(
      `/sessions${params ? `?${new URLSearchParams(params as Record<string, string>)}` : ''}`,
    ),
  get: (id: string) => call<SessionRow>(`/sessions/${id}`),
  /** Many at once. `all` is resolved server-side, so a conversation started in
   *  another tab is not silently spared. */
  deleteMany: (payload: { ids?: string[]; all?: boolean; artifacts?: boolean }) =>
    call<{ deleted: number; artifactsDeleted: number }>('/sessions/delete', body(payload)),
  /** Proposes a caption and prompt for a figure; draws nothing and costs nothing. */
  suggestFigure: (
    sessionId: string,
    payload: { title?: string; about?: string; context?: string; visualStyle?: string },
  ) =>
    call<{
      caption: string
      prompt: string
      /** `flow` / `method` / `concept` when this place wants a figure drawn as shapes; empty for a picture. */
      figure?: string
      /** What to draw, for the diagram path. */
      description?: string
      /** The style chip the picture should be drawn with. */
      style?: string
    }>(
      `/sessions/${sessionId}/figure-suggestion`,
      body(payload),
    ),
  /** Pictures. Synchronous: the upstream answers with a PNG, so there is nothing to poll. */
  images: (
    sessionId: string,
    payload: {
      prompt: string
      model?: string
      aspect: string
      style: string
      /** How the words in the picture are handled. */
      labels?: 'auto' | 'ko' | 'en' | 'none'
      count: number
      /** Send the prompt as typed, skipping the planner. */
      raw?: boolean
      /** Asked for from inside a document: comes back as a figure, not a picture of a whole slide. */
      figure?: boolean
    },
  ) => call<ArtifactRow[]>(`/sessions/${sessionId}/images`, body(payload)),
  /** A labelled figure written as a flowchart and drawn by the server with the .pptx export's shapes, kept as an image artifact. See `diagram.py`. */
  diagram: (
    sessionId: string,
    payload: {
      description: string
      figure: string
      model?: string
      language?: string
    },
  ) => call<{ source: string; caption: string; model: string; credits: number; artifact: ArtifactRow | null }>(
    `/sessions/${sessionId}/diagrams`,
    body(payload),
  ),
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
      /** The conversation's standing skills; `[]` switches them all off. */
      skillIds?: string[]
    },
  ) =>
    call<SessionRow>(`/sessions/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/sessions/${id}`, { method: 'DELETE' }),
  /** 좋아요 / 싫어요, or `null` to take it back. Addressed by message id. */
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
  addProjectUrl: (projectId: string, url: string) =>
    call<FileRow>(`/projects/${projectId}/knowledge/url`, body({ url })),
  downloadUrl: (id: string) => `${BASE_URL}/files/${id}/content`,
  /** Reads an uploaded `.hwpx` into a new report session; nothing generated or charged. Returns the session id. */
  openAsDocument: (id: string) =>
    call<{ id: string }>(`/files/${id}/open-as-document`, { method: 'POST' }),
  remove: (id: string) => call<void>(`/files/${id}`, { method: 'DELETE' }),
}

/** Downloads a stored file under its own name. Fetch + blob so the token travels in a header, not the URL. */
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
  /** Blanks and findings re-located against the current body. */
  marks: (id: string) => call<DocMarks>(`/artifacts/${id}/marks`),
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
  version: (id: string, version: number) =>
    call<ArtifactVersionDetailRow>(`/artifacts/${id}/versions/${version}`),
  /** Checks one slide's claims against the web. Costs searches and a model call. */
  factcheckSlide: (id: string, slideId: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/factcheck`, body({ slideId })),
  /** The same check on one report section. Same cost, same verdict shape. */
  factcheckSection: (id: string, sectionId: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/factcheck`, body({ sectionId })),
  /** Stores this browser's render of a mermaid diagram for the exporters (the API has no headless browser). Free, adds no version. */
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
  /** Puts a workspace picture into one block of a page; bytes are inlined server-side. Costs no model call. */
  addBlockImage: (id: string, index: number, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/blocks/image`, body({ index, artifactId, caption })),
  /** Appends a Markdown image line to one report section. */
  addSectionImage: (id: string, sectionId: string, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/sections/image`, body({ sectionId, artifactId, caption })),
  /** The same, for a slide of a JSON deck. Addressed by slide id, not position. */
  addSlideImage: (id: string, slideId: string, artifactId: string, caption: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/image`, body({ slideId, artifactId, caption })),
  /** Stores this browser's raster of a slide's own figure (`slide.diagram`) as the slide picture for the exporters. Free, adds no version. */
  storeSlideDiagram: (id: string, slideId: string, key: string, src: string) =>
    call<ArtifactRow>(`/artifacts/${id}/slides/diagram`, body({ slideId, key, src })),
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

interface ArtifactVersionDetailRow extends ArtifactVersionRow {
  data: Record<string, unknown> | null
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
interface ArtifactQuery {
  kind?: string
  projectId?: string
  q?: string
  limit?: number
  beforeAt?: string
  beforeId?: string
}

// Re-exported for callers that only import from this module.
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
interface DesignDraftRow {
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
  /** Reads a design out of a file or page. Costs a model call; stores nothing. */
  extract: (payload: { fileId?: string; url?: string }) =>
    call<DesignDraftRow>('/designs/extract', body(payload)),
  create: (payload: Partial<DesignRow> & { name: string }) =>
    call<DesignRow>('/designs', body(payload)),
  update: (id: string, patch: Partial<DesignRow>) =>
    call<DesignRow>(`/designs/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/designs/${id}`, { method: 'DELETE' }),
}

/** A rendering 서식. Read-only: the catalogue ships inside the API image. */
export interface DesignTemplateRow {
  id: string
  /** `deck` · `document` */
  kind: string
  /** The surface it is offered on — `slides` or `report`. */
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
  /** Rubric lines a critique scores against. Korean only. */
  checks: string[]
  /** Extension of the downloadable blank Office form, when present. */
  formFormat: string
  /** Whether `/design-templates/{id}/preview` has a miniature to show. */
  hasPreview: boolean
  /** Slides in a deck's sample, each at `/preview?slide=N`; 0 for any other kind. */
  slideCount: number
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

export const designTemplatesApi = {
  list: (surface?: SessionKind) =>
    call<DesignTemplateRow[]>(`/design-templates${surface ? `?surface=${surface}` : ''}`),
  /** The stylesheet the editor's shadow root draws the document in; a shadow root takes CSS, not a URL. */
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
  /** The shipped scripts and checks a skill's server fields may name. */
  serverCatalogue: () => call<SkillServerCatalogue>('/skills/server-catalogue'),
  /**
   * SKILL.md, .zip or .skill files read into skills. `dryRun` only previews (findings
   * included); otherwise every file must pass and each becomes a personal skill.
   */
  importFiles: async (files: File[], opts: { dryRun?: boolean; draft?: boolean } = {}) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    const query = new URLSearchParams()
    if (opts.dryRun) query.set('dryRun', 'true')
    if (opts.draft) query.set('draft', 'true')
    const res = await fetch(`${BASE_URL}/skills/import${query.size ? `?${query}` : ''}`, {
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
    return (await res.json()) as SkillImportResult
  },
}

export interface SkillImportFinding {
  /** `block` stops the upload; `warn` is shown and the upload may go on. */
  severity: 'block' | 'warn'
  code: string
  message: string
  file: string
  line: number | null
}

export interface SkillImportPreview {
  filename: string
  ok: boolean
  name: string
  description: string
  whenToUse: string
  kinds: string[]
  requiredTools: string[]
  version: string
  bodyChars: number
  estimatedTokens: number
  kept: string[]
  excluded: { path: string; reason: 'script' | 'binary' | 'unused' | 'too_long' }[]
  findings: SkillImportFinding[]
  /** What the server would run for it; null when nothing. */
  server?: SkillServer | null
  /** Its own Python scripts, kept for the sandbox. */
  scripts?: SkillScript[]
}

/** One shipped script a skill may name, with the shipped skill that uses it. */
export interface ServerRef {
  ref: string
  skill: string
  summary: string
}

export interface SkillServerCatalogue {
  postChecks: ServerRef[]
  entries: Record<'pre' | 'post', ServerRef[]>
  scripts: ServerRef[]
  copyKinds: { kind: string; label: string }[]
}

export interface SkillReview {
  skill: SkillRow
  ownerName: string
}

/** The administrators' queue of skills with scripts asking to be shared. */
export const adminSkillsApi = {
  list: (review = 'pending') => call<SkillReview[]>(`/admin/skills?review=${review}`),
  approve: (id: string) => call<SkillReview>(`/admin/skills/${id}/approve`, { method: 'POST' }),
  reject: (id: string, note: string) => call<SkillReview>(`/admin/skills/${id}/reject`, body({ note })),
}

export interface SkillImportResult {
  dryRun: boolean
  items: SkillImportPreview[]
  created: SkillRow[]
}

/** Downloads one of your skills as SKILL.md. */
export async function downloadSkill(id: string, name: string) {
  const res = await fetch(`${BASE_URL}/skills/${id}/export`, {
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
  anchor.download = `${name.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'skill'}-SKILL.md`
  anchor.click()
  URL.revokeObjectURL(url)
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
  /** Fixed scripts its package ships, run by `run_skill_script`. */
  scripts?: string[]
  /** What the server runs for it, naming shipped scripts only. */
  server?: SkillServer
  /** Its own scripts, run only in the sandbox; `source` for the owner. */
  scriptFiles?: SkillScript[]
  /** Sharing with scripts: '' · pending · approved · rejected. */
  review?: string
  reviewNote?: string
}

export interface StoreSkillRow extends SkillRow {
  ownerId: string
  ownerName: string
  official: boolean
  installed: boolean
}

interface ToolCatalogRow {
  name: string
  label: string
  available: boolean
  /** 「기본 도구」 or the connector's name. */
  group?: string
  title?: string
  description?: string
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
  /** Server-side copy of a shared agent, including its own copies of the shared skills. */
  install: (id: string) => call<AgentRow>(`/agents/${id}/install`, { method: 'POST' }),

  /** An agent's own documents, searched via `search_knowledge` rather than pushed into every turn. */
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
    /** A conversation's own files, copied onto the agent's shelf (「에이전트로 저장」). */
    fromFiles: (agentId: string, ids: string[]) =>
      call<FileRow[]>(`/agents/${agentId}/knowledge/from-files`, body({ ids })),
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
  surface: string | null
  renderTemplateId: string | null
  docFormat: string | null
  designSystemId: string | null
  /** That design's name; the viewer's own design of this name stands in for one they cannot see. */
  designName?: string
  /** How to use it, shown on the empty screen. */
  guide: string
  /** First messages offered as buttons there. */
  starters: string[]
  /** How a shared original may be taken. */
  shareMode: 'open' | 'sealed'
  /** The prompt is withheld: somebody else's sealed original, or a copy of one. */
  sealed: boolean
  temperature: number
  color: string
  enabled: boolean
  visibility: string
  installs: number
  catalogKey: string | null
  pipelineId?: string | null
  originId: string | null
  official: boolean
  installed: boolean
  runs: number
  /** True only when this caller has readable documents on this agent's shelf. */
  hasKnowledge: boolean
  updatedAt: string
  baseKey?: string | null
  retired?: boolean
  checkSkills?: string[] | null
  checkOptions?: Record<string, boolean> | null
  gapQuestions?: GapQuestion[] | null
  inheritedQuestions?: GapQuestion[]
  baseQuestions?: GapQuestion[]
  bundles?: BundleSelection[] | null
  process?: AgentProcess | null
}

/* ── connectors (MCP) ───────────────────────────────────────────────────
 * Credentials live server-side only; nothing here ever carries one.
 */

interface ConnectorToolRow {
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

interface RequiredEnvField {
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

/* ── pipeline runs and definitions ─────────────────────────────────────── */

/** A run as stored: every stage with its evidence. */
export interface RunRow extends PipelineRun {
  sessionId: string
  messageId: string | null
  artifactId: string | null
  artifactVersion: number | null
  createdAt: string
  updatedAt: string
}

export const runsApi = {
  get: (sessionId: string, runId: string) =>
    call<RunRow>(`/sessions/${sessionId}/runs/${runId}`),
  list: (sessionId: string, limit = 20) =>
    call<RunRow[]>(`/sessions/${sessionId}/runs?limit=${limit}`),
}

/** 「이 단계부터 다시」: the same events as a normal turn of that surface. */
export async function* streamRerun(
  sessionId: string,
  runId: string,
  payload: { fromStage: string; answers?: Record<string, string> },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  yield* postStream(`/sessions/${sessionId}/runs/${runId}/rerun`, payload, signal)
}

export const userPipelinesApi = {
  list: () => call<UserPipeline[]>('/user-pipelines'),
  store: () => call<UserPipeline[]>('/user-pipelines/store'),
  catalogue: () => call<PipelineCatalogue>('/user-pipelines/catalogue'),
  get: (id: string) => call<UserPipeline>(`/user-pipelines/${id}`),
  /** A new one: from an agent's shipped flow, from another pipeline, or from steps. */
  create: (payload: { name?: string; description?: string; stages?: PipelineStep[]; fromAgentId?: string; fromId?: string }) =>
    call<UserPipeline>('/user-pipelines', body(payload)),
  update: (id: string, payload: { name?: string; description?: string; stages: PipelineStep[] }) =>
    call<UserPipeline>(`/user-pipelines/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  remove: (id: string) => call<void>(`/user-pipelines/${id}`, { method: 'DELETE' }),
  validate: (stages: PipelineStep[], id?: string, agentId?: string) =>
    call<PipelineVerdict>('/user-pipelines/validate', body({ stages, id, agentId })),
  submit: (id: string) => call<UserPipeline>(`/user-pipelines/${id}/submit`, { method: 'POST' }),
  withdraw: (id: string) => call<UserPipeline>(`/user-pipelines/${id}/withdraw`, { method: 'POST' }),
  bind: (agentId: string, pipelineId: string | null) =>
    call<{ agentId: string; pipelineId: string | null }>(`/agents/${agentId}/user-pipeline`, {
      method: 'PUT',
      body: JSON.stringify({ pipelineId }),
    }),
  start: (id: string) =>
    call<{ sessionId: string; kind: 'report' | 'chat'; pipelineId: string }>(`/user-pipelines/${id}/start`, { method: 'POST' }),
  adminList: (review = 'pending') => call<UserPipeline[]>(`/admin/user-pipelines?review=${review}`),
  approve: (id: string) => call<UserPipeline>(`/admin/user-pipelines/${id}/approve`, { method: 'POST' }),
  reject: (id: string, note: string) => call<UserPipeline>(`/admin/user-pipelines/${id}/reject`, body({ note })),
}

/** An agent's 진행 과정 and the 묶음 it ticks. */
export const processApi = {
  bases: () => call<ProcessCatalogue>('/agent-bases'),
  put: (
    agentId: string,
    payload: { mode: ProcessMode; base?: string | null; bundles?: BundleSelection[]; stages?: PipelineStep[]; importId?: string },
  ) =>
    call<AgentProcess & { agent: Partial<AgentRow> }>(`/agents/${agentId}/process`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
}

/** A person's own 묶음. */
export const bundlesApi = {
  list: () => call<{ shipped: Bundle[]; mine: Bundle[]; shared: Bundle[] }>('/bundles'),
  create: (payload: { name: string; description?: string; stages: PipelineStep[] }) =>
    call<Bundle>('/bundles', body(payload)),
  update: (id: string, payload: { name: string; description?: string; stages: PipelineStep[] }) =>
    call<Bundle>(`/bundles/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  remove: (id: string) => call<void>(`/bundles/${id}`, { method: 'DELETE' }),
  share: (id: string, visibility: 'org' | 'private') => call<Bundle>(`/bundles/${id}/share`, body({ visibility })),
}

export const pipelinesApi = {
  list: () => call<{ name: string; formats: string[]; agents: string[]; flows: string[] }[]>('/pipelines'),
  get: (name: string) => call<PipelineDefinition>(`/pipelines/${encodeURIComponent(name)}`),
  forAgent: (agentId: string) => call<PipelineDefinition>(`/agents/${agentId}/pipeline`),
}

/* ── streaming ──────────────────────────────────────────────────────────*/

export type StreamEvent =
  | {
      type: 'step'; id: string; label: string; status: Step['status']; detail?: string
      outputs?: Step['outputs']; code?: string; codeAttempt?: number; stdout?: string; stderr?: string
      /** `pipeline` for stage records; tool steps carry none or their own. */
      category?: string
      /** The rich stage record; absent on older servers and on tool steps. */
      stage?: PipelineStage
    }
  /** A pipeline run announcing itself before its first stage record. */
  | {
      type: 'run'; runId: string; pipeline?: string; flow?: string
      parentId?: string | null; fromStage?: string | null
    }
  | { type: 'delta'; text: string }
  /** Text the agent took back — narration written before a tool ran, or an answer it then repeated. */
  | { type: 'retract'; text: string }
  | {
      type: 'skills_applied'
      skills: { id: string; name: string; catalogKey: string | null; estimatedTokens: number }[]
      estimatedTokens: number
    }
  | { type: 'section'; sectionId: string; heading: string; content: string; done: boolean }
  /** A slide, announced empty by the outline pass; resent whole rather than patched, since the layout can change. */
  | { type: 'slide'; slide: Slide; done: boolean }
  /** One block of an HTML artifact; announced empty by the outline pass, then resent whole once written. */
  | { type: 'block'; block: { title: string; layout: string; html: string }; done: boolean }
  /** The finished single file. Arrives once, after the last block. */
  | { type: 'page'; html: string; blocks: { title: string; layout: string }[]; templateId: string }
  | { type: 'title'; title: string }
  /** The design a report will be saved with, sent before its first section so the draft wears it. */
  | { type: 'design'; design: DesignTokens }
  /** The outline a document intends to write, offered before it writes it. */
  | { type: 'proposal'; plan: NonNullable<PendingPlan['plan']> }
  /** What it needs to know before it can plan at all. */
  | { type: 'needs'; questions: PendingQuestion[] }
  /** The reference shelf a report's sections cite from, sent once, up front. */
  | { type: 'sources'; sources: Source[] }
  /** Search terms and selection counts retained with a researched report. */
  | { type: 'research'; research: NonNullable<ReportArtifact['research']> }
  | {
      type: 'artifact'
      artifactId: string
      /** True when the model set out to make this rather than the server extracting a long fence; only then does the panel open. */
      deliberate?: boolean
    }
  | { type: 'usage'; inputTokens: number; outputTokens: number; credits: number; [counter: string]: number | string }
  | { type: 'error'; message: string; code?: string; reason?: string }
  | ({ type: 'privacy_route' } & PrivacyRouting)
  | { type: 'privacy_route'; action: 'mask_external'; source: 'tool_output'; count: number }
  /** A turn with no privacy routing still closes with what the server measured. */
  | { type: 'privacy_route'; lengthCheck?: LengthCheck; accuracy?: PrivacyRouting['accuracy'] }
  | ({ type: 'model_route' } & CostRouting)
  | ({ type: 'freshness_abstention' } & FreshnessAbstention)
  | ({ type: 'tool_result_answer' } & ToolResultAnswer)
  /** `messageId` is the stored answer's id — the one every later call must use. */
  | { type: 'done'; credits?: number; messageId?: string }

/** Chat, report and slides stream from one endpoint; reports emit a `section` event per section. */
export async function* streamSession(
  sessionId: string,
  payload: {
    content: string
    /** File ids from `filesApi.upload` — the server reads their extracted text. */
    attachments?: string[]
    /** `true` searches every turn, `false` never, `'auto'` when the words call for it. */
    webSearch?: WebSearchSetting
    model?: string
    activatedSkillIds?: string[]
    /** A rendering template. Sticky on the session; `''` clears it. */
    renderTemplateId?: string
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
    /** The failed question to run again in place, by message id; the server reuses that row. */
    retryOf?: string
  },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  yield* postStream(`/sessions/${sessionId}/messages`, payload, signal)
}

/**
 * Silence budget before a turn is called stalled. Generous on purpose: the
 * server heartbeats every 15 s during a healthy turn, so this long is a gone connection.
 */
const STALL_MS = 120_000

/** Rejects when the stream has produced nothing for {@link STALL_MS}. */
function withStallGuard<T>(work: Promise<T>, onStall: () => void): Promise<T> {
  let timer: ReturnType<typeof setTimeout>
  return Promise.race([
    work,
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        // Aborting is what frees the connection; rejecting alone leaves the request running.
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
  // Own controller so the stall guard can abort; the caller's signal is
  // forwarded, since the server tells 중단 from a dropped tab by it.
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
  // A refused turn answers with JSON, not a stream; surfaced as an ApiError.
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
    // The 409 body was consumed above; reading it twice loses the code.
    const resolved = detail ?? (res.status === 409 ? `http_${res.status}` : await readDetail(res))
    if (res.status === 401) throw new UnauthorizedError(resolved)
    throw new ApiError(res.status, resolved)
  }
  // A turn that stopped to ask (`_ask_before_writing`) answers in JSON, not a
  // stream; translated into events here so the runners need not know.
  if (res.headers.get('content-type')?.includes('application/json')) {
    const answered = (await res.json()) as {
      pending?: PendingPlan | null; message?: string; runId?: string; stage?: PipelineStage
    }
    const pending = answered.pending
    // What the turn said, before what it asks; otherwise the bubble stays empty with a spinner.
    if (answered.message) yield { type: 'delta', text: answered.message }
    // The waiting run and its card stage, as the stream would have said them.
    if (answered.runId && answered.stage) {
      yield { type: 'run', runId: answered.runId, flow: 'pre' }
      yield {
        type: 'step', category: 'pipeline', id: `stage:${answered.stage.name}`,
        label: answered.stage.label, status: 'done', stage: answered.stage,
      }
    }
    if (pending?.stage === 'clarify' && pending.questions) {
      yield { type: 'needs', questions: pending.questions }
    } else if (pending?.stage === 'outline' && pending.plan) {
      yield { type: 'proposal', plan: pending.plan }
    } else {
      // Said explicitly: falling out of the generator silently reads as a dropped connection.
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

/* ── issue reports (「문의·신고」) ───────────────────────────────────────── */

export type IssueKind = 'bug' | 'feature' | 'other'
export type IssueStatus = 'received' | 'checking' | 'resolved' | 'on_hold'

/** What the snapshot left out to stay under its size cap. */
export interface IssueCut {
  code: 'evidence' | 'versions' | 'steps' | 'content' | 'artifacts' | 'messages' | 'bodies'
  count?: number
}

export interface IssueRow {
  id: string
  kind: IssueKind
  status: IssueStatus
  body: string
  holdReason: string
  sessionId: string | null
  messageId: string | null
  artifactId: string | null
  sessionShared: boolean
  hasScreenshot: boolean
  snapshot: {
    title: string
    kind: string
    messages: number
    artifacts: number
    files: number
    bytes: number
    truncated: IssueCut[]
  } | null
  /** An administrator replied or changed the status since the reporter last looked. */
  unread: boolean
  assignee: { id: string; name: string } | null
  /** Administrator views only. */
  reporter: { id: string; name: string; email: string } | null
  replies: number | null
  createdAt: string
  updatedAt: string
  adminUpdatedAt: string | null
}

export interface IssueReply {
  id: string
  authorRole: 'admin' | 'user'
  authorName: string
  body: string
  /** Set when the entry records a status change. */
  status: IssueStatus | null
  createdAt: string
}

/** Recorded with every report; never message text. */
export interface IssueContext {
  client?: Record<string, string>
  apiVersion?: string
  session?: {
    id: string
    kind: string
    model: string
    routingMode: string
    agent: string | null
    pipeline: string | null
    renderTemplateId: string | null
    messageCount: number
    strictLocal: boolean
  }
  models?: string[]
  failures?: { messageId: string; failure: string }[]
  runs?: RunRow[]
  artifacts?: { id: string; kind: string; version: number; versionIds: string[] }[]
  pinned?: { messageId: string | null; artifactId: string | null }
}

export interface IssueDetail extends IssueRow {
  thread: IssueReply[]
  context?: IssueContext
  admins?: { id: string; name: string }[]
  transitions?: IssueStatus[]
}

export interface IssueSessionInfo {
  id: string
  title: string
  kind: string
  messageCount: number
  artifactCount: number
  fileCount: number
  runCount: number
  /** A turn ran on the strict-local (internal data) route: sharing asks once more. */
  strictLocal: boolean
  models: string[]
  agent: string | null
  pipeline: string | null
}

/** The read-only copy of a session, as an administrator opens it. */
export interface IssueSnapshot {
  session: {
    id: string
    title: string
    kind: string
    model: string
    agent: string | null
    project: string | null
    pipeline: string | null
    artifactId: string | null
    createdAt: string | null
    updatedAt: string | null
  }
  messages: MessageRow[]
  runs: RunRow[]
  artifacts: {
    id: string
    kind: string
    title: string
    version: number
    data: unknown
    updatedAt: string | null
    versions: { id: string; version: number; summary: string; createdAt: string | null; data?: unknown }[]
    blobCopied: boolean
  }[]
  files: { id: string; name: string; mime: string; size: number; createdAt: string | null; copied: boolean }[]
  pinned: { messageId: string | null; artifactId: string | null }
  truncated: IssueCut[]
}

export interface IssueCreate {
  kind: IssueKind
  body: string
  sessionId?: string
  shareSession: boolean
  confirmInternal?: boolean
  messageId?: string
  artifactId?: string
  screenshotFileId?: string
  client: Record<string, string>
}

export interface AdminIssueFilters {
  kind?: string
  status?: string
  since?: string
  until?: string
  user?: string
  q?: string
  assignee?: string
  page?: number
  size?: number
}

async function issueBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE_URL}${path}`, {
    credentials: 'include',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) {
    const detail = await readDetail(res)
    if (res.status === 401) throw new UnauthorizedError(detail)
    throw new ApiError(res.status, detail)
  }
  return res.blob()
}

export const issuesApi = {
  sessionInfo: (sessionId: string) => call<IssueSessionInfo>(`/issues/session-info/${sessionId}`),
  create: (payload: IssueCreate) => call<IssueRow>('/issues', body(payload)),
  list: () => call<IssueRow[]>('/issues'),
  unread: () => call<{ count: number }>('/issues/unread'),
  get: (id: string) => call<IssueDetail>(`/issues/${id}`),
  markRead: (id: string) => call<{ ok: boolean }>(`/issues/${id}/read`, { method: 'POST' }),
  reply: (id: string, text: string) => call<IssueReply>(`/issues/${id}/replies`, body({ body: text })),
  screenshot: (id: string) => issueBlob(`/issues/${id}/screenshot`),
  adminList: (filters: AdminIssueFilters) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return call<{
      items: IssueRow[]
      total: number
      page: number
      size: number
      counts: Record<IssueStatus, number>
      admins: { id: string; name: string }[]
    }>(`/admin/issues?${query}`)
  },
  adminGet: (id: string) => call<IssueDetail>(`/admin/issues/${id}`),
  adminUpdate: (id: string, patch: { status?: IssueStatus; holdReason?: string; assigneeId?: string }) =>
    call<IssueDetail>(`/admin/issues/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  adminReply: (id: string, text: string) => call<IssueReply>(`/admin/issues/${id}/replies`, body({ body: text })),
  /** Each open is written to the audit trail. */
  adminSnapshot: (id: string) => call<IssueSnapshot>(`/admin/issues/${id}/snapshot`),
  adminScreenshot: (id: string) => issueBlob(`/admin/issues/${id}/screenshot`),
  adminFile: (id: string, index: number, artifact = false) =>
    issueBlob(`/admin/issues/${id}/files/${index}${artifact ? '?artifact=true' : ''}`),
}

/* ── performance monitoring (admin) ───────────────────────────────────── */

export type PerfRange = '1h' | '24h' | '7d' | '30d'
export type PerfLevel = 'ok' | 'warn' | 'critical' | 'info'
/** `[epoch ms, mean, peak]` per bucket. */
export type PerfPoint = [number, number, number]

export interface PerfEvidence {
  source: string
  name: string
  subject: string
  windowMin: number
  threshold: number | null
  latest: number | null
  peak: number | null
  points: [number, number][]
}

export interface PerfVerdict {
  level: PerfLevel
  code: string
  title: string
  detail: string
  action: string
  evidence: PerfEvidence[]
}

export interface PerfChart {
  key: string
  title: string
  unit: string
  series: { name: string; axis: 'left' | 'right'; unit?: string; points: PerfPoint[] }[]
}

type N = number | null

export interface PerfOverview {
  generatedAt: string
  range: PerfRange
  stepSec: number
  status: 'ok' | 'warn' | 'critical'
  verdicts: PerfVerdict[]
  collector: {
    enabled: boolean
    lastSampleAt: string | null
    hostAvailable: boolean
    modelLogAt: string | null
    targets: string[]
  }
  host: {
    cpuPercent: N
    cpus: N
    memPercent: N
    memUsed: N
    memTotal: N
    load1: N
    netRx: N
    netTx: N
    cpuPeak: N
    memPeak: N
    disks: { label: string; usedPercent: number; free: N }[]
    devices: { device: string; busyPercent: number; read: N; write: N }[]
  }
  components: {
    name: string
    cpuPercent: N
    cpuCores: N
    cpuLimitPercent: N
    cpuCoresPeak: N
    memBytes: N
    memLimitPercent: N
    memPeak: N
    netRx: N
    netTx: N
    ioRead: N
    ioWrite: N
  }[]
  models: {
    model: string
    calls: number
    errors: number
    errorRate: number
    latencyP50: N
    latencyP95: N
    latencyP95Peak: N
    ttftP50: N
    outTokens: number
  }[]
  vllm: {
    model: string
    up: boolean
    running: N
    waiting: N
    kvCache: N
    ttftP95: N
    e2eP95: N
    genTps: N
    waitingPeak: N
    runningPeak: N
    kvPeak: N
    ttftP95Peak: N
    preemptions: number
    requests: number
  }[]
  gpus: {
    node: string
    up: boolean
    util: N
    memPercent: N
    temp: N
    power: N
    nodeMemPercent: N
    nodeCpuPercent: N
    utilPeak: N
  }[]
  replicas: { name: string; streams: number; turns: N; requests: N; errors: N; httpP95Ms: N; streamsPeak: N }[]
  pipeline: { stage: string; runs: number; p50: number; p95: number; max: number }[]
  charts: PerfChart[]
  thresholds: {
    waitingMax: number
    kvCacheMax: number
    ttftP95MaxSec: number
    apiCpuMax: number
    memMax: number
    diskWarn: number
    diskCritical: number
    errorRateMax: number
    gpuTempMax: number
    gpuNodeMemMax: number
    sustainMin: number
    apiCpuSustainMin: number
  }
}

export const perfApi = {
  overview: (range: PerfRange) => call<PerfOverview>(`/admin/perf/overview?range=${range}`),
}

/* ── search (⌘K, 대화 기록) ───────────────────────────────────────────────
 * The caller's own conversations and results, by title and by what they say. Answered from
 * the app's own database; nothing is sent to a model.
 */

/** A run of a title or excerpt; `hit` marks the query's words. */
export interface SearchRun {
  text: string
  hit: boolean
}

export interface SessionSearchHit {
  id: string
  kind: SessionKind
  title: string
  titleParts: SearchRun[]
  matchedIn: 'title' | 'message'
  /** A window of the newest message that holds every word; empty for a title match. */
  snippet: SearchRun[]
  messageId: string | null
  role: 'user' | 'assistant' | null
  projectId: string | null
  artifactId: string | null
  updatedAt: string
}

export interface ArtifactSearchHit {
  id: string
  kind: ArtifactKind
  title: string
  titleParts: SearchRun[]
  matchedIn: 'title' | 'text'
  snippet: SearchRun[]
  sessionId: string | null
  projectId: string | null
  updatedAt: string
}

export interface SearchResult {
  query: string
  terms: string[]
  sessions: SessionSearchHit[]
  artifacts: ArtifactSearchHit[]
  sessionsMore: boolean
  artifactsMore: boolean
  /** Conversations matching across every page; only when asked with `count`. */
  sessionsTotal: number | null
}

export type SearchScope = 'all' | 'sessions' | 'artifacts'

export const searchApi = {
  query: (
    q: string,
    {
      scope = 'all', limit = 8, offset = 0, count = false, signal,
    }: { scope?: SearchScope; limit?: number; offset?: number; count?: boolean; signal?: AbortSignal } = {},
  ) => {
    const params = new URLSearchParams({ q: q.slice(0, 200), scope, limit: String(limit), offset: String(offset) })
    if (count) params.set('count', 'true')
    return call<SearchResult>(`/search?${params}`, { signal })
  },
}

/** One page of 대화 기록, newest first; `page` is the one served (the last when asked past it). */
export interface SessionPage {
  items: SessionRow[]
  total: number
  page: number
  size: number
}

export const historyApi = {
  page: (page: number, size = 50, signal?: AbortSignal) =>
    call<SessionPage>(`/sessions/paged?page=${page}&size=${size}`, { signal }),
}
