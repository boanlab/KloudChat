/** Single backend seam; the browser never reaches LiteLLM and keys stay server-side. */

import type {
  AgentDraft,
  AgentDraftDecided,
  CatalogEntryAdmin,
  ProcessSettings,
  TemplateCatalogue,
  UserTemplate,
  AgentProcess,
  ArtifactKind,
  Bundle,
  BundleSelection,
  ProcessMode,
  CostRouting,
  DesignTokens,
  DocMarks,
  PipelineCatalogue,
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

import { objectParticle, topicParticle } from './formCheck'
import { currentLang, currentLocale, translate } from './i18n'

export const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** In memory only; refresh token is an httpOnly cookie, recovered via `auth.refresh()`. */
let accessToken: string | null = null
/** `auto`: decided from the message. */
export type WebSearchSetting = boolean | 'auto'

export const setAccessToken = (t: string | null) => {
  accessToken = t
}
/** Carries the backend's `detail` code so callers can branch without parsing prose. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** Seconds the server asked to wait (`Retry-After` on a 429), when it said. */
  readonly retryAfter?: number

  constructor(status: number, detail: string, retryAfter?: number) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.retryAfter = retryAfter
  }
}

/** Machine code shape; reader sentences have spaces or are Korean. */
const MACHINE_CODE = /^[a-z][a-z0-9_]*$/

export function isSentence(detail: string | null | undefined): detail is string {
  return Boolean(detail) && !MACHINE_CODE.test(detail as string)
}

export function errorCode(err: unknown): string {
  if (err instanceof ApiError && err.detail && MACHINE_CODE.test(err.detail)) {
    return err.detail
  }
  return ''
}

/** Screen text: a 4xx sentence `detail`, else `fallback`; 5xx bodies never shown. */
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

/** One entry of FastAPI's 422 `detail` array. */
interface FieldProblem {
  type?: string
  loc?: (string | number)[]
  msg?: string
  ctx?: { min_length?: number; max_length?: number; ge?: number; le?: number }
}

/** What a field is called on screen, by the wire name the server refused. */
const FIELD_NAMES: Record<string, string> = {
  content: '질문',
  password: '비밀번호',
  newPassword: '비밀번호',
  currentPassword: '현재 비밀번호',
  email: '이메일',
  name: '이름',
  title: '제목',
  description: '설명',
  url: '주소',
  monthlyCredits: '월 크레딧',
}

/** Korean sentence for a refused form; the validator's English never shown. */
export function validationMessage(problems: FieldProblem[]): string {
  const first = problems[0] ?? {}
  const wire = String([...(first.loc ?? [])].reverse().find((part) => typeof part === 'string' && part !== 'body') ?? '')
  const field = FIELD_NAMES[wire]
  const lang = currentLang()
  const max = first.ctx?.max_length
  const min = first.ctx?.min_length
  const number = (n: number) => n.toLocaleString(currentLocale())
  if (wire === 'content' && first.type === 'string_too_long' && max) {
    return translate(lang, '질문은 {n}자까지 보낼 수 있습니다. 긴 자료는 「파일·사진 첨부」로 올리세요.')
      .replace('{n}', number(max))
  }
  if (field === '비밀번호' && first.type === 'string_too_short' && min) {
    return translate(lang, '비밀번호는 {n}자 이상이어야 합니다.').replace('{n}', number(min))
  }
  /** `template` with the field's name, its particle (Korean only) and `n` filled in. */
  const say = (template: string, particle: (word: string) => string, n = 0) =>
    translate(lang, template)
      .replace('{field}', translate(lang, field ?? ''))
      .replace(/\{[을은]\}/, lang === 'ko' && field ? particle(field) : '')
      .replace('{n}', number(n))
  if (field && first.type === 'string_too_short' && min === 1) return say('{field}{을} 입력하세요.', objectParticle)
  if (field && first.type === 'string_too_short' && min) return say('{field}{은} {n}자 이상이어야 합니다.', topicParticle, min)
  if (field && first.type === 'string_too_long' && max) return say('{field}{은} {n}자까지 쓸 수 있습니다.', topicParticle, max)
  if (wire === 'email' || (first.type === 'value_error' && /email/i.test(first.msg ?? ''))) {
    return translate(lang, '이메일 주소 형식이 올바르지 않습니다.')
  }
  if (field) {
    return translate(lang, '{field} 값을 확인하세요.').replace('{field}', translate(lang, field))
  }
  return translate(lang, '입력한 값을 확인하세요.')
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    // FastAPI: string `detail` for raises, array for validation.
    if (typeof body?.detail === 'string') return body.detail
    if (Array.isArray(body?.detail)) return validationMessage(body.detail as FieldProblem[])
    // A refused order of steps (`invalid_pipeline`): its first problem, already a sentence.
    const problem = body?.detail?.problems?.[0]?.message
    if (typeof problem === 'string' && problem) return problem
    // A refusal with its own shape (`{code, ...}`): its machine code.
    if (typeof body?.detail?.code === 'string') return body.detail.code
  } catch {
    /* non-JSON error body */
  }
  return `http_${res.status}`
}

/** Framed on another site: bot calls go out accountless, keeping the frame's visitor cookie. */
let botEmbedded = false
export function setBotEmbedded(on: boolean) {
  botEmbedded = on
}

function who(path: string): Record<string, string> {
  if (botEmbedded && path.startsWith('/b/')) return { 'X-KloudChat-Embed': '1' }
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...who(path),
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

/* ── auth: argon2id, short-lived JWT, rotating refresh cookie; signup pending until approved ── */

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
  /** `undefined` (204) when the browser holds no refresh cookie: signed out. */
  refresh: () => call<AuthSession | undefined>('/auth/refresh', { method: 'POST' }),
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
  /** False when the proxy did not answer; adapter models still listed. */
  litellmAvailable: boolean
  /** Empty when absent from the catalogue. */
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
  /** LiteLLM `/model/info` plus adapter models. */
  list: () => call<ModelCatalogue>('/models'),
  /** Admin-only. Drops the server's 30-second cache. */
  refresh: () => call<ModelCatalogue>('/models/refresh', { method: 'POST' }),
}

/* ── admin ──────────────────────────────────────────────────────────────*/

export interface SystemSettings {
  litellm: {
    baseUrl: string
    /** `backend`: derived from the gateway address. */
    baseUrlSource: 'database' | 'backend' | 'environment'
    masterKeySet: boolean
    /** Last four characters. The key itself is never sent. */
    masterKeyPreview: string
    masterKeySource: 'database' | 'environment'
  }
  /** Empty host: mail and password reset off. */
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
    passwordResetEnabled: boolean
  }
  status: 'ok' | 'unavailable'
  brand: { name: string; logo: string }
  /** Where 관리자에게 문의 goes; `admin` means the first administrator's own. */
  contact: { email: string; source: 'database' | 'admin' }
  /** Chat always included. */
  enabledKinds: string[]
  /** Empty `domains`: any; `verifyEmail`: mailed link first. */
  signup: {
    mode: 'open' | 'approval' | 'closed'
    modeSource: 'database' | 'environment'
    domains: string[]
    verifyEmail: boolean
    /** `verifyEmail` with a mail server behind it. */
    verificationActive: boolean
  }
  /** One gateway address fans out into the features. */
  tools: {
    /** Empty: features set individually or by the environment. */
    backendBaseUrl: string
    features: {
      key: 'search' | 'fetch' | 'exec' | 'research' | 'stt' | 'index' | 'ocr'
      label: string
      url: string
      /** `backend`: derived from the gateway address. */
      source: 'database' | 'backend' | 'environment'
    }[]
  }
  /** Stored, and as in force given the reachable readers. */
  ocr: {
    enabled: boolean
    mode: 'both' | 'engine' | 'vision'
    /** Stored choice; empty means the first of `visionChoices`. */
    visionModel: string
    maxPages: number
    modeInForce: 'both' | 'engine' | 'vision' | null
    visionModelInForce: string
    engineConfigured: boolean
    /** Strict-local models that declare vision: the only ones a page may go to. */
    visionChoices: { id: string; label: string }[]
  }
  /** As in force; an empty string is a choice (follow the default or session model). Absent from older servers. */
  modelRoles?: {
    chat: string
    report: string
    slides: string
    image: string
    audio: string
    video: string
    title: string
    code: string
    codeFallback: string
    selfHostedThinking: boolean
    externalThinking: boolean
    defaultMonthlyCredits: number
    /** `helper`: chat models this instance runs (self-hosted, or `local/` with a strict-local twin). */
    choices: Record<
      'chat' | 'report' | 'slides' | 'image' | 'audio' | 'video' | 'helper',
      { id: string; label: string; strictLocal: boolean }[]
    >
  }
  /** Served so quoting screens cannot drift. */
  credits: {
    /** Credits to one US dollar. */
    perUsd: number
    /** How far above the KloudChat allowance the LiteLLM budget is set. */
    budgetHeadroom: number
  }
  /** Served by the proxy, withheld from the picker for want of a price. */
  unpricedModels: { id: string; provider: string }[]
}

/** Composer microphone; not stored. */
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
  /** Public: the browser cannot read admin settings. */
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
      /** Idle minutes before sign-out; 0 off. */
      idleTimeoutMinutes: number
      /** What the signup form should say before somebody is refused. */
      signup: { mode: 'open' | 'approval' | 'closed'; domains: string[]; emailVerification: boolean }
      /** Absent from older servers. */
      password?: { minLength: number }
      /** Wrong passwords in a row that lock an address, and for how long. */
      login?: { maxFailures: number; lockoutMinutes: number }
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
  /** `otherCredits`: spend no single model owns (multi-model charges). */
  totals: { credits: number; requests: number; searches: number; otherCredits: number }
  cycle: { allowance: number; used: number; remaining: number }
  daily: { date: string; credits: number; requests: number; searches: number }[]
  byModel: { model: string; credits: number; requests: number; units?: number; unit?: string }[]
  bySurface: { kind: string; credits: number; requests: number }[]
  /** Proxy-aggregated key spend; shown beside the total, not added. */
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
  /** Own spend; `/admin/usage` covers everyone. */
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
    toolsOcrUrl?: string
    ocrEnabled?: string
    ocrMode?: string
    ocrVisionModel?: string
    ocrMaxPages?: string
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
    modelChat?: string
    modelReport?: string
    modelSlides?: string
    modelImage?: string
    modelAudio?: string
    modelVideo?: string
    modelTitle?: string
    modelCode?: string
    modelCodeFallback?: string
    thinkingSelfHosted?: string
    thinkingExternal?: string
    defaultMonthlyCredits?: string
  }) => call<SystemSettings>('/admin/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  testSettings: () =>
    call<{ ok: boolean; models?: number; detail?: string }>('/admin/settings/test', {
      method: 'POST',
    }),
  /** Multipart; bypasses call()'s JSON path. */
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
  testTool: (feature: string) =>
    call<{ ok: boolean; detail?: string }>(`/admin/settings/test-tool/${feature}`, {
      method: 'POST',
    }),
  /** Real message: a connection check passes with a sender the relay refuses. */
  testSmtp: (to?: string) =>
    call<{ ok: boolean; detail?: string }>('/admin/settings/smtp-test', body({ to: to ?? null })),
  users: () => call<User[]>('/admin/users'),
  /** Approval and allowance together: an active account without credits reads as a bug. */
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
  /** Fresh LiteLLM key; also the first for an account created while the proxy was down. */
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

/* ── admin: usage & audit, from stored rows; no seeded fallback ── */

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
  /** `other`: spend with no session, so the bars sum to the total. */
  bySurface: { kind: SessionKind | 'other' | 'bot'; credits: number; requests: number }[]
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
  /** `changes`: `{field: [old, new]}`; secrets masked. */
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

/** Fetched as a blob: a plain link cannot carry the access token. */
export async function downloadArtifact(
  id: string,
  format: 'docx' | 'pdf' | 'hwpx' | 'pptx' | 'md' | 'html' | 'source',
  title: string,
) {
  const res = await fetchArtifactExport(id, format)

  let filename = `${title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'report'}.${format === 'source' ? 'txt' : format}`
  if (format === 'source') {
    // Extension from the server, never sniffed.
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

/** `src` for media elements: token in the query string, since they cannot send a header. */
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
      artifact: {
        title: string
        artifactKind: string
        data: unknown
        updatedAt: string
      } | null
      updatedAt: string
    }

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

/* ── sessions: one resource for all five surfaces, by `kind` ── */

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
  /** Retired 시작점 an old turn began from, title as it read then. */
  startedFrom: { templateId: string; title: string } | null
  /** Null until rated. */
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
  agentDraft: (sessionId: string, distill = true) =>
    call<AgentDraft>(`/sessions/${sessionId}/agent-draft${distill ? '' : '?distill=false'}`),
  /** 「에이전트로 저장」's 지시 alone: what the conversation settled, asked after the rest. */
  agentDraftDecided: (sessionId: string) =>
    call<AgentDraftDecided>(`/sessions/${sessionId}/agent-draft/decided`),
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
  /** `all` resolved server-side, so a conversation from another tab is not spared. */
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
      description?: string
      /** The style chip the picture should be drawn with. */
      style?: string
    }>(
      `/sessions/${sessionId}/figure-suggestion`,
      body(payload),
    ),
  /** Synchronous: upstream answers with a PNG. */
  images: (
    sessionId: string,
    payload: {
      prompt: string
      model?: string
      aspect: string
      style: string
      labels?: 'auto' | 'ko' | 'en' | 'none'
      count: number
      /** Send the prompt as typed, skipping the planner. */
      raw?: boolean
      /** Asked for from inside a document: comes back as a figure, not a picture of a whole slide. */
      figure?: boolean
    },
  ) => call<ArtifactRow[]>(`/sessions/${sessionId}/images`, body(payload)),
  /** Flowchart drawn server-side with the .pptx shapes; stored as an image artifact. */
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

/* ── workspace: owned by the caller; anything else 404 ── */

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
  /** Scanned pages being read: null when none were needed. */
  ocrState?: 'queued' | 'running' | 'done' | 'failed' | null
  /** Pages to read (past the limit included), pages read, pages whose two readings differ. */
  ocrTotal?: number
  ocrDone?: number
  ocrReview?: number
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
  /** One file, for a screen following its scanned pages being read. */
  get: (id: string) => call<FileRow>(`/files/${id}`),
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
  /** Theme the project wears; null: default look. */
  designSystemId: string | null
  /** Surface → template; sent whole, a missing key means the built-in track. */
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
  /** One gallery page, newest first, bodies cut to card size (`partial`); `get` for the whole. */
  list: (params?: ArtifactQuery) => call<ArtifactRow[]>(`/artifacts${artifactParams(params)}`),
  /** How many of each kind exist, which a page of rows cannot say. */
  counts: (q?: string) =>
    call<{ counts: Record<string, number>; total: number }>(
      `/artifacts/counts${q?.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`,
    ),
  get: (id: string) => call<ArtifactRow>(`/artifacts/${id}`),
  create: (payload: { kind: string; title?: string; data?: unknown; sessionId?: string | null }) =>
    call<ArtifactRow>('/artifacts', body(payload)),
  /** `expectedVersion`: conditional write; 409 if someone else got there first. */
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
  /** This browser's diagram render, stored for the exporters. Free, no version. */
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
  /** This browser's raster of `slide.diagram`, stored for the exporters. Free, no version. */
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

/** One gallery page; the cursor is the last row returned. */
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
  /** Shown in the pickers; false for a shipped theme kept for what already wears it. */
  listed?: boolean
  updatedAt: string
}

/** A theme read out of a document: a proposal, not a row. */
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

/** A rendering template; read-only, shipped in the API image. */
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
  /** A person's template (`ut-<id>`): the caller's own when `mine`. */
  user?: boolean
  mine?: boolean
  /** Shown in pickers; false for a shipped template still chosen by id but not offered. */
  listed?: boolean
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
  /** Stylesheet for the editor's shadow root (CSS, not a URL). */
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
  /** SKILL.md, .zip or .skill import; `dryRun` previews, else every file must pass. */
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
  /** Shown in the lists people pick from; false for a shipped skill kept only as a part. */
  listed?: boolean
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
  // `keepalive`: a delete flushed as the page closes still reaches the server.
  remove: (id: string) => call<void>(`/memory/${id}`, { method: 'DELETE', keepalive: true }),
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
  /** 담당 부서·연락처, said as written when the material does not answer. */
  contact?: string
  /** The owner's published bot of this agent. */
  publication?: { id: string; slug: string; status: string } | null
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
  processSettings?: Partial<ProcessSettings> | null
  inheritedSettings?: ProcessSettings
}

/* ── connectors (MCP): credentials stay server-side ── */

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
  /** 기본 제공: can be switched off, not removed. */
  byDefault?: boolean
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
  /** 기본 제공: everyone has it installed. */
  byDefault?: boolean
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
  /** Also the recovery point: the server resumes polling, so a reload picks up a clip in flight. */
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

/* ── pipeline runs ─────────────────────────────────────────────────────── */

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
  validate: (
    stages: PipelineStep[],
    id?: string,
    agentId?: string,
    self?: { surface: string; name: string },
    /** The form's 빠진 정보 질문 as they stand: a 확인 질문 step is judged with them. */
    questions?: GapQuestion[],
  ) =>
    call<PipelineVerdict>(
      '/user-pipelines/validate',
      body({ stages, id, agentId, selfSurface: self?.surface, selfName: self?.name, questions }),
    ),
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
    /** Outline as edited on the card; titles and order theirs, layouts the planner's. */
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

/** Silence budget before a stall; the server heartbeats every 15 s. */
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
  // Own controller for the stall guard; caller's signal forwarded (the server tells 중단 from a dropped tab by it).
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
        ...who(path),
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
          detail = validationMessage(payload.detail as FieldProblem[])
        }
      } catch (error) {
        if (error instanceof PrivacyDecisionError) throw error
      }
    }
    // The 409 body was consumed above; reading it twice loses the code.
    const resolved = detail ?? (res.status === 409 ? `http_${res.status}` : await readDetail(res))
    if (res.status === 401) throw new UnauthorizedError(resolved)
    const wait = Number(res.headers.get('Retry-After'))
    throw new ApiError(res.status, resolved, res.status === 429 && wait > 0 ? wait : undefined)
  }
  // A turn that stopped to ask answers in JSON; translated into events here.
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

/* ── published agents (공개 챗봇) ──────────────────────────────────────── */

export type PublicationStatus = 'draft' | 'pending' | 'approved' | 'paused' | 'rejected' | 'revoked'
export type ShowProcess = 'none' | 'summary' | 'full'

export interface BotBranding {
  greeting: string
  suggestedPrompts: string[]
  placeholder: string
}

/** What a bot runs under; the owner may only lower. Per-address limits: accountless visitors only. */
export interface BotLimits {
  perVisitorPerMinute: number
  perVisitorPerDay: number
  perBotPerMinute: number
  perIpPerMinute: number
  perIpPerDay: number
  turnsPerDay: number
  tokensPerDay: number
  maxInputChars: number
  turnsPerConversation: number
  concurrency: number
  /** A key's calls (API, MCP): per minute and per day. */
  perKeyPerMinute: number
  perKeyPerDay: number
  /** The owner's own daily credit cap; required before the bot opens outside. */
  creditsPerDay?: number
}

/** Who reaches a bot: members (`internal`), whoever holds the link, or everyone (listed). */
export type BotVisibility = 'internal' | 'link' | 'external'
/** A request for a wider audience, or for a new version on one: '' when none. */
export type ReviewState = '' | 'pending' | 'rejected'

export interface PublicationVersion {
  id: string
  number: number
  model: string
  note: string
  summary: { agentName?: string; knowledge?: { name: string; chars: number }[]; tools?: string[]; checks?: string[]; contact?: boolean }
  active: boolean
  /** Waiting for an administrator before it answers people without an account. */
  pending?: boolean
  approvedAt?: string | null
  createdAt: string
}

/** A visitor as the owner may know them: never who they are. */
export interface BotVisitor {
  id: string
  label: string
  member: boolean
  blocked: boolean
  blockedAt: string | null
  /** A key of the owner's (API, MCP) rather than a person. */
  byKey?: boolean
}

/** Where a bot reaches beyond its own page, each opened by an administrator. */
export type BotChannel = 'embed' | 'api' | 'mcp'
export interface BotChannels {
  embed?: { origins: string[] }
  api?: Record<string, never>
  mcp?: Record<string, never>
}

/** A key for the API and MCP channels; `key` only in the response that made it. */
export interface BotKey {
  id: string
  name: string
  preview: string
  createdAt: string
  lastUsedAt: string | null
  key?: string
}

/** When an administrator read the bot's conversations, and why. */
export interface BotAdminRead {
  at: string
  kind: 'list' | 'read'
  reason: string
  /** Reads of the same kind and reason within a minute, shown as one line. */
  count?: number
}

export interface Publication {
  id: string
  slug: string
  title: string
  description: string
  agentId: string
  agentName: string
  ownerName: string
  visibility: BotVisibility
  status: PublicationStatus
  requestedVisibility: '' | BotVisibility
  reviewState: ReviewState
  /** The administrator's words on the last decision; a refusal's reason. */
  reviewNote: string
  reviewedAt: string | null
  pendingVersionId: string | null
  /** Channels open now, and the whole set asked for while a review is pending. */
  channels: BotChannels
  requestedChannels: BotChannels | null
  /** Something happened since the owner last opened this page. */
  needsLook: boolean
  /** Why it is paused: `owner`, `admin`, `credits`, `account`, `model`. */
  pauseReason: string
  activeVersionId: string | null
  branding: BotBranding
  showProcess: ShowProcess
  showSources: boolean
  retentionDays: number
  conversationView: 'off' | 'on'
  consentVersion: number
  limits: BotLimits
  defaults: BotLimits
  createdAt: string
  updatedAt: string
  publishedAt: string | null
  versions?: PublicationVersion[]
  turnsToday?: number
}

export interface PublishPreflight {
  ok: boolean
  /** `no_local_model`, `not_chat`, `sealed`, `own_process`, `credits`, `account`, `retired`, `not_owner`. */
  problems: string[]
  model: { id: string; label: string } | null
  tools: string[]
  checks: string[]
  /** What a bot leaves out: `memory`, `personal`, `files`, `connectors`, `web`, `documents`, `tools`, `skills`, `questions`, `own_checks`. */
  disabled: string[]
  knowledge: { name: string; chars: number; findings: { category: string; count: number }[] }[]
  creditsRemaining: number
  existingId: string | null
  defaults: BotLimits
}

export interface PublicationSettings {
  title?: string
  description?: string
  branding?: BotBranding
  limits?: Partial<BotLimits>
  showProcess?: ShowProcess
  showSources?: boolean
  retentionDays?: number
  conversationView?: 'off' | 'on'
}

export interface PublicationStats {
  days: number
  daily: { date: string; turns: number; answers: number; conversations: number; visitors: number; tokens: number; unanswered: number; up: number; down: number; shared: number; failed: number }[]
  totals: { turns: number; answers: number; conversations: number; visitors: number; tokens: number; unanswered: number; up: number; down: number; shared: number; failed: number; visitorsAll: number }
  topics: { word: string; count: number }[]
  unanswered: { text: string; at: string }[]
  /** Questions by where they came from. */
  channels?: Record<'web' | BotChannel, number>
}

export interface BotConversationRow {
  id: string
  title: string
  sharedAt: string | null
  viewable?: boolean
  updatedAt: string
  createdAt?: string
  turns?: number
  /** The owner's view only. */
  visitor?: BotVisitor | null
}

export interface BotTranscriptMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  rating: 'up' | 'down' | null
  createdAt: string
  steps?: Step[] | null
  failure?: string | null
  model?: string | null
}

export interface BotListItem {
  slug: string
  title: string
  description: string
  ownerName: string
  mine: boolean
  /** The owner's own bot: the dashboard's id. */
  id: string | null
  status: PublicationStatus
  visibility: BotVisibility
  needsLook?: boolean
  color: string
  publishedAt: string | null
}

/** A bot open to everyone, as the public list shows it: no owner names. */
export interface PublicBotItem {
  slug: string
  title: string
  description: string
  color: string
  publishedAt: string | null
}

export interface BotPage {
  slug: string
  title: string
  description: string
  /** Empty for a visitor without an account. */
  ownerName: string
  mine: boolean
  signedIn: boolean
  visibility: BotVisibility
  /** Opened in a frame on another site. */
  embedded?: boolean
  /** The owner blocked this visitor. */
  blocked: boolean
  status: PublicationStatus
  pauseReason: string
  branding: BotBranding
  showSources: boolean
  showProcess: ShowProcess
  conversationView: 'off' | 'on'
  retentionDays: number
  consentVersion: number
  consented: boolean
  color: string
  maxInputChars: number
}

/** What a visitor's turn streams: the answer and, as the owner chose, its working. */
export type BotStreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'retract'; text: string }
  | { type: 'step'; id?: string; label?: string; status?: string; category?: string; detail?: string }
  | { type: 'usage'; inputTokens: number; outputTokens: number }
  | { type: 'model'; models: string[] }
  | { type: 'title'; title: string }
  | { type: 'error'; code?: string; message?: string }
  | { type: 'done'; messageId?: string }

export const publicationsApi = {
  list: () => call<Publication[]>('/publications'),
  preflight: (agentId: string) => call<PublishPreflight>('/publications/preflight', body({ agentId })),
  publish: (agentId: string, settings: PublicationSettings & { note?: string; visibility?: BotVisibility }) =>
    call<Publication>('/publications', body({ agentId, visibility: 'internal', ...settings })),
  /** Widening is asked of an administrator; narrowing applies at once. */
  setScope: (id: string, visibility: BotVisibility, creditsPerDay?: number) =>
    call<Publication>(`/publications/${id}/scope`, body({ visibility, creditsPerDay })),
  withdrawRequest: (id: string) => call<Publication>(`/publications/${id}/review`, { method: 'DELETE' }),
  attention: () => call<{ bots: number; reviews: number }>('/publications/attention'),
  block: (id: string, sessionId: string) =>
    call<BotVisitor>(`/publications/${id}/conversations/${sessionId}/block`, { method: 'POST' }),
  blocked: (id: string) => call<BotVisitor[]>(`/publications/${id}/visitors`),
  unblock: (id: string, visitorId: string) =>
    call<BotVisitor>(`/publications/${id}/visitors/${visitorId}/block`, { method: 'DELETE' }),
  get: (id: string) => call<Publication>(`/publications/${id}`),
  update: (id: string, patch: PublicationSettings & { status?: 'approved' | 'paused' }) =>
    call<Publication>(`/publications/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  remove: (id: string) => call<void>(`/publications/${id}`, { method: 'DELETE' }),
  newVersion: (id: string, note: string) => call<Publication>(`/publications/${id}/versions`, body({ note })),
  activate: (id: string, versionId: string) =>
    call<Publication>(`/publications/${id}/versions/${versionId}/activate`, { method: 'POST' }),
  stats: (id: string, days = 30) => call<PublicationStats>(`/publications/${id}/stats?days=${days}`),
  conversations: (id: string) => call<BotConversationRow[]>(`/publications/${id}/conversations`),
  conversation: (id: string, sessionId: string) =>
    call<BotConversationRow & { messages: BotTranscriptMessage[] }>(`/publications/${id}/conversations/${sessionId}`),
  /** The channels wanted, as a whole: closing applies at once, opening is asked of an administrator. */
  setChannels: (id: string, wanted: { embed?: { on: boolean; origins: string[] }; api?: boolean; mcp?: boolean }) =>
    call<Publication>(`/publications/${id}/channels`, body(wanted)),
  keys: (id: string) => call<BotKey[]>(`/publications/${id}/keys`),
  createKey: (id: string, name: string) => call<BotKey>(`/publications/${id}/keys`, body({ name })),
  revokeKey: (id: string, keyId: string) => call<void>(`/publications/${id}/keys/${keyId}`, { method: 'DELETE' }),
  adminReads: (id: string) => call<BotAdminRead[]>(`/publications/${id}/admin-reads`),
}

export const botsApi = {
  list: () => call<BotListItem[]>('/bots'),
  /** Bots open to everyone; needs no account. */
  publicList: () => call<PublicBotItem[]>('/public/bots'),
  page: (slug: string) => call<BotPage>(`/b/${encodeURIComponent(slug)}`),
  consent: (slug: string, version: number) =>
    call<void>(`/b/${encodeURIComponent(slug)}/consent`, body({ version })),
  conversations: (slug: string) => call<BotConversationRow[]>(`/b/${encodeURIComponent(slug)}/conversations`),
  start: (slug: string) => call<BotConversationRow>(`/b/${encodeURIComponent(slug)}/conversations`, { method: 'POST' }),
  conversation: (slug: string, id: string) =>
    call<BotConversationRow & { messages: BotTranscriptMessage[] }>(`/b/${encodeURIComponent(slug)}/conversations/${id}`),
  remove: (slug: string, id: string) =>
    call<void>(`/b/${encodeURIComponent(slug)}/conversations/${id}`, { method: 'DELETE' }),
  stop: (slug: string, id: string) =>
    call<void>(`/b/${encodeURIComponent(slug)}/conversations/${id}/stop`, { method: 'POST' }),
  rate: (slug: string, messageId: string, rating: 'up' | 'down' | null) =>
    call<{ id: string; rating: string | null }>(`/b/${encodeURIComponent(slug)}/messages/${messageId}/rating`, {
      method: 'PATCH',
      body: JSON.stringify({ rating }),
    }),
  share: (slug: string, id: string) =>
    call<{ id: string; sharedAt: string | null }>(`/b/${encodeURIComponent(slug)}/conversations/${id}/share`, { method: 'POST' }),
  unshare: (slug: string, id: string) =>
    call<{ id: string; sharedAt: string | null }>(`/b/${encodeURIComponent(slug)}/conversations/${id}/share`, { method: 'DELETE' }),
}

/** An administrator's row: a bot with its owner and how many conversations it holds. */
export interface AdminPublication extends Publication {
  ownerEmail: string
  conversations: number
}

/** What an administrator decides on before a bot answers people without an account. */
export interface PublicationReview {
  publication: Publication
  owner: { name: string; email: string }
  version: PublicationVersion | null
  agent: {
    name: string
    description: string
    instructions: string
    contact: string
    guide: string
    starters: string[]
    tools: string[]
    checks: string[]
    /** The checks by the names people know (「근거 없는 답 보류」). */
    checkNames?: string[]
    model: string
  } | null
  /** What the open review is about. */
  request?: 'visibility' | 'channels' | 'version' | null
  /** What the version waiting changes against the one answering now. */
  changes?: { field: 'instructions' | 'contact' | 'model' | 'tools' | 'checks' | 'knowledge'; before: unknown; after: unknown }[]
  knowledge: { name: string; chars: number; findings: { category: string; count: number }[]; preview?: string }[]
  history: { action: string; at: string; actorId: string | null; metadata: Record<string, unknown> }[]
}

export const adminPublicationsApi = {
  list: (pendingOnly = false) => call<AdminPublication[]>(`/admin/publications${pendingOnly ? '?review=pending' : ''}`),
  review: (id: string) => call<PublicationReview>(`/admin/publications/${id}/review`),
  decide: (id: string, decision: 'approve' | 'reject', reason: string) =>
    call<Publication>(`/admin/publications/${id}/review`, body({ decision, reason })),
  withdraw: (id: string, reason: string) => call<Publication>(`/admin/publications/${id}/withdraw`, body({ reason })),
  setStatus: (id: string, status: 'approved' | 'paused' | 'revoked', reason: string) =>
    call<Publication>(`/admin/publications/${id}/status`, body({ status, reason })),
  /** Every conversation of the bot, for an abuse review; the reason is recorded and the owner sees it. */
  conversations: (id: string, reason: string) =>
    call<BotConversationRow[]>(`/admin/publications/${id}/conversations?reason=${encodeURIComponent(reason)}`),
  conversation: (id: string, sessionId: string, reason: string) =>
    call<BotConversationRow & { messages: BotTranscriptMessage[] }>(
      `/admin/publications/${id}/conversations/${sessionId}?reason=${encodeURIComponent(reason)}`,
    ),
}

/** A visitor's question, streamed as the bot answers. */
export async function* streamBot(
  slug: string,
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<BotStreamEvent> {
  const events = postStream(`/b/${encodeURIComponent(slug)}/conversations/${conversationId}/messages`, { content }, signal)
  for await (const event of events) yield event as unknown as BotStreamEvent
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

/* ── search (⌘K, 대화 기록): own conversations and artifacts, from the database; no model ── */

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

/** The editor's payload for a person's template. */
export type UserTemplateIn = Omit<UserTemplate, 'id' | 'formName' | 'hasForm' | 'mine' | 'ownerName' | 'updatedAt'>

const templateBody = (t: UserTemplateIn) => JSON.stringify({ ...t, process: t.process as Partial<ProcessSettings> })

/** A person's own templates (`/user-templates`). */
export const userTemplatesApi = {
  list: () => call<{ mine: UserTemplate[]; shared: UserTemplate[] }>('/user-templates'),
  catalogue: () => call<TemplateCatalogue>('/user-templates/catalogue'),
  /** A shipped document template as the editor's fields (snake_case, as the server stores). */
  start: (templateId: string) => call<Record<string, unknown>>(`/user-templates/start/${encodeURIComponent(templateId)}`),
  get: (id: string) => call<UserTemplate>(`/user-templates/${encodeURIComponent(id)}`),
  create: (t: UserTemplateIn) => call<UserTemplate>('/user-templates', { method: 'POST', body: templateBody(t) }),
  update: (id: string, t: UserTemplateIn) =>
    call<UserTemplate>(`/user-templates/${encodeURIComponent(id)}`, { method: 'PUT', body: templateBody(t) }),
  remove: (id: string) => call<void>(`/user-templates/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadForm: async (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE_URL}/user-templates/${encodeURIComponent(id)}/form`, {
      method: 'POST',
      credentials: 'include',
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body: form,
    })
    if (!res.ok) {
      const detail = await readDetail(res)
      if (res.status === 401) throw new UnauthorizedError(detail)
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as UserTemplate
  },
  removeForm: (id: string) => call<UserTemplate>(`/user-templates/${encodeURIComponent(id)}/form`, { method: 'DELETE' }),
}

/** The connector catalogue administrators keep (관리 › 커넥터 카탈로그). */
export type CatalogEntryIn = Omit<CatalogEntryAdmin, 'id' | 'updatedAt'>
export interface CatalogAdmin {
  entries: CatalogEntryAdmin[]
  kinds: string[]
  transports: string[]
  features: string[]
  placeholders: string[]
}
export const connectorCatalogApi = {
  list: () => call<CatalogAdmin>('/admin/connector-catalog'),
  add: (entry: CatalogEntryIn) => call<{ entries: CatalogEntryAdmin[] }>('/admin/connector-catalog', body(entry)),
  edit: (id: string, entry: CatalogEntryIn) =>
    call<{ entries: CatalogEntryAdmin[] }>(`/admin/connector-catalog/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(entry) }),
  remove: (id: string) => call<{ entries: CatalogEntryAdmin[] }>(`/admin/connector-catalog/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
