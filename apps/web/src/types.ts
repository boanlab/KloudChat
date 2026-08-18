/**
 * KloudChat domain types.
 *
 * A user creates a `Session` of some `SessionKind`; a session produces an
 * `Artifact`. Kinds that take a while carry a `Job`, which is what shows
 * progress and failure.
 *
 * No proxy credential appears in this file: virtual keys are issued and used
 * server-side only.
 */

/* ── identity ───────────────────────────────────────────────────────── */

export type UserRole = 'admin' | 'user'
export type UserStatus = 'active' | 'pending' | 'suspended'

export interface User {
  id: string
  email: string
  name: string
  role: UserRole
  status: UserStatus
  /**
   * The credit allowance. An internal unit kept apart from provider prices, so
   * a price change does not move anyone's limit. `creditsUsed` returns to 0 at
   * `cycleResetsAt`; nothing rolls over.
   */
  monthlyCredits: number
  creditsUsed: number
  /** Null until an admin approves and opens the first cycle. */
  cycleResetsAt: string | null
  avatarColor: string
  /**
   * Last four characters of this user's LiteLLM key, or null. The key itself
   * never reaches the browser and no route returns it.
   */
  litellmKeyPreview: string | null
  litellmKeyIssuedAt: string | null
  /** Behaviour switches owned by the settings screen. Always present: the
   *  server fills in defaults for anything unchosen. */
  preferences: Preferences
  /** Empty means the whole catalogue. */
  allowedModels: string[]
  createdAt: string
  /** Null for an account that has never signed in. */
  lastActiveAt: string | null
}


/* ── models ─────────────────────────────────────────────────────────── */

export type Modality = 'chat' | 'image' | 'audio' | 'video'

export interface ModelInfo {
  id: string
  /** "Vendor · Model" — what every surface shows. A bare name does not say who
   *  is being billed. */
  label: string
  /** Model name without the vendor, for layouts that place the two separately. */
  name: string
  /** Company that built the model (Qwen, Anthropic, …) — not the routing slug. */
  vendor: string
  /** LiteLLM routing provider (`hosted_vllm`, `openrouter`, …). */
  provider: string
  /** Explicit proxy declaration. Missing metadata is unknown, never inferred
   * from a model id or provider name. */
  dataBoundary: 'self_hosted' | 'hybrid' | 'external' | 'unknown'
  strictLocal: boolean
  privacyOnly: boolean
  modality: Modality
  /** Video only: credits per second, keyed `<resolution>:<sound|silent>`. */
  creditPerSecond?: Record<string, number>
  /** Credits per generated picture; zero for anything that is not an image
   *  model. The ledger's own unit is per 1k output tokens. */
  creditPerImage?: number
  /** Flat credits per call, for models billed per clip rather than per token. */
  creditPerCall?: number
  /** Which of the five surfaces may select this model. */
  kinds: SessionKind[]
  /** Credits per unit — per 1k output tokens for chat, per asset for image/audio/video. */
  creditCost: number
  /**
   * Credits per 1k input tokens; 0 for non-conversational or self-hosted
   * models. Long context is where the money goes, so output alone understates
   * the price.
   */
  inputCreditCost: number
  contextWindow?: number
  supportsVision?: boolean
  supportsTools?: boolean
  /** Set when the model is not reachable through LiteLLM and uses an adapter. */
  adapter?: string
  description: string
}

/* ── sessions ───────────────────────────────────────────────────────── */

/**
 * `av` covers audio and video together: both are timeline media on the same job
 * card, and the surface produces an `audio` or `video` artifact by mode.
 */
export type SessionKind = 'chat' | 'report' | 'slides' | 'image' | 'av'
export type RoutingMode = 'manual' | 'auto'

export interface Session {
  id: string
  kind: SessionKind
  title: string
  projectId: string | null
  agentId: string | null
  model: string
  /** `auto` keeps `model` as the quality ceiling and may choose a cheaper
   *  model for eligible, low-complexity chat turns. */
  routingMode: RoutingMode
  createdAt: string
  updatedAt: string
  pinned: boolean
  messages: Message[]
  /**
   * Latest message, one line. List views carry this instead of `messages`: a
   * sidebar cannot fetch eighty transcripts to render subtitles.
   */
  preview: string | null
  messageCount: number
  /** Artifact this session is currently producing, if any. */
  artifactId: string | null
  /**
   * The rendering template this session writes into.
   *
   * Sticky: picked once, it shapes every turn until it is cleared, the way the
   * model choice does. Null means the surface's built-in track — markdown
   * sections for a report, JSON slides for a deck.
   */
  renderTemplateId: string | null
}

export interface Preferences {
  /** Off means the answer appears in one piece when the turn ends. */
  streamResponses: boolean
  /** Extract durable facts from finished turns into memory. */
  autoMemory: boolean
  /** The model · token · credit line under each answer. */
  showUsage: boolean
  privacyDefaultAction: PrivacyAction | 'ask'
}

export type PrivacyAction = 'route_strict_local' | 'mask_external' | 'send_raw_external'

export interface CostRouting {
  mode: 'auto'
  decision: 'routed' | 'kept_quality' | 'bypassed' | 'classifier_unavailable'
  reasonCode: string
  requestedModel: string
  selectedModel: string
  executedModel?: string
  classifierVersion: string
  complexity?: 'low' | 'high' | 'uncertain'
  confidence?: number
  classifierModel?: string
  classifierInputTokens?: number
  classifierOutputTokens?: number
  estimatedCreditsSaved?: number
}

export interface PrivacyRouting {
  requestedModels: string[]
  routedModels: string[]
  effectiveModels: string[]
  actualModels: string[]
  actualModel?: string
  action: PrivacyAction | 'strict_local' | 'none'
  dataBoundary: ModelInfo['dataBoundary'] | 'mixed'
  modelRoutes?: {
    routedModel: string
    actualModel: string | null
    dataBoundary: ModelInfo['dataBoundary']
  }[]
  detectorVersion?: string
  policyVersion?: string
  findingCounts?: { category: string; source: string; count: number }[]
  compareCollapsed?: boolean
  toolOutputMasked?: number
  toolOutputFindings?: { category: string; source: string; count: number }[]
  initialAction?: PrivacyAction | 'strict_local' | 'none'
  costRouting?: CostRouting
}

export type Role = 'user' | 'assistant' | 'system'

/**
 * A unit of visible work inside an assistant turn. Inline while streaming, then
 * collapsed once the turn ends.
 */
export interface Step {
  id: string
  type: 'thinking' | 'tool' | 'artifact'
  label: string
  status: 'running' | 'done' | 'error'
  detail?: string
  /** e.g. 3 of 5 sources read. Drives the inline counter. */
  progress?: { current: number; total: number }
  /** Structured metadata for the per-turn skill timeline entry. */
  skills?: {
    id: string
    name: string
    catalogKey: string | null
    estimatedTokens: number
  }[]
  estimatedTokens?: number
}

/** One model's answer inside a comparison turn. */
export interface Variant {
  model: string
  routedModel?: string
  actualModel?: string
  dataBoundary?: ModelInfo['dataBoundary']
  content: string
  status: 'streaming' | 'done' | 'error'
  usage?: { inputTokens: number; outputTokens: number; credits: number }
  /** Set on the variant the user kept; the conversation continues from it. */
  chosen?: boolean
}

export interface Message {
  id: string
  role: Role
  content: string
  /** Present instead of `content` when the turn was run as a model comparison. */
  variants?: Variant[]
  createdAt: string
  model?: string
  routing?: PrivacyRouting
  steps?: Step[]
  artifactIds?: string[]
  attachments?: { name: string; size: string; type: string }[]
  usage?: { inputTokens: number; outputTokens: number; credits: number }
  liked?: 'up' | 'down' | null
  /**
   * Why the turn ended badly. Separate from `content`: a turn can fail after
   * writing something, and that half an answer is worth keeping.
   */
  error?: string
}

/* ── jobs ───────────────────────────────────────────────────────────── */

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'

export interface Job {
  id: string
  sessionId: string
  kind: SessionKind
  status: JobStatus
  /** 0–100. Providers that report no progress get a coarse stage-based estimate. */
  progress: number
  stage: string
  /**
   * Charged on success only, so there is no refund path — a failed job never
   * deducted anything.
   */
  creditsUsed: number
  /** Shown before the run so the user knows what it will cost. */
  creditsEstimated: number
  error?: string
  createdAt: string
  finishedAt: string | null
  /** What was asked for, so a failed job's card can offer to run it again. */
  prompt: string
  model: string
  params: Record<string, unknown> | null
}

/* ── artifacts ──────────────────────────────────────────────────────── */

export type ArtifactKind =
  | 'report'
  | 'deck'
  | 'chart'
  | 'image'
  | 'audio'
  | 'video'
  | 'code'
  | 'html'

interface ArtifactBase {
  id: string
  title: string
  version: number
  createdAt: string
  updatedAt: string
  sessionId: string | null
  projectId: string | null
}

/** A citation the model attached to a claim, surfaced beside the prose. */
export interface Source {
  id: string
  /** Number shown inline as [1], [2] … */
  ordinal: number
  title: string
  author?: string
  publisher?: string
  year?: string
  url?: string
  /** Where it came from: web search, a connector, or an uploaded file. */
  origin: 'web' | 'connector' | 'file'
  originLabel: string
  quote?: string
}

/** Sections stream in one at a time; the panel renders each as it completes. */
export interface ReportSection {
  id: string
  heading: string
  level: 1 | 2
  status: 'pending' | 'streaming' | 'done'
  content: string
}

export interface ReportArtifact extends ArtifactBase {
  kind: 'report'
  sections: ReportSection[]
  sources: Source[]
  /** Citation style the export renders. */
  citationStyle: 'APA' | 'MLA' | 'Chicago' | 'IEEE'
  wordCount: number
}

/**
 * Tabular analysis output. A first-class artifact rather than an image the
 * model happens to draw, so the numbers stay checkable.
 */
export interface ChartArtifact extends ArtifactBase {
  kind: 'chart'
  chartType: 'bar' | 'line' | 'stacked'
  caption: string
  xLabel: string
  yLabel: string
  series: { name: string; color: string; points: { x: string; y: number }[] }[]
  /** The rows the chart was computed from, so numbers stay checkable. */
  table: { columns: string[]; rows: (string | number)[][] }
  sourceFile: string
}

/**
 * The outline the model emits, rendered to .pptx by the backend. The browser
 * only previews it.
 */
/** Per-claim verification result, shown on the slide it belongs to. */
export interface FactCheck {
  status: 'unchecked' | 'checking' | 'done'
  claims: {
    id: string
    text: string
    verdict: 'supported' | 'unsupported' | 'uncertain'
    note: string
    /**
     * The evidence behind the verdict. Mandatory for `supported` and
     * `unsupported`; with nothing to point at, the server downgrades the
     * verdict to `uncertain`.
     */
    sourceUrl?: string
  }[]
}

export interface Slide {
  id: string
  layout: 'title' | 'bullets' | 'two-column' | 'image' | 'quote' | 'chart'
  title: string
  bullets?: string[]
  body?: string
  notes?: string
  accent?: string
  factCheck?: FactCheck
}

export interface DeckArtifact extends ArtifactBase {
  kind: 'deck'
  theme: string
  slides: Slide[]
}

export interface ImageArtifact extends ArtifactBase {
  kind: 'image'
  /** The job that produced it — one job can emit several images. */
  jobId: string | null
  prompt: string
  /** The ratio that was asked for. A phrase in the prompt, not a parameter. */
  aspect: string
  /** The ratio that came back, measured off the bytes. Absent on older rows. */
  actualAspect?: string
  width?: number
  height?: number
  style: string
  seed: number
  model: string
  /** Object-store URL, once an image producer exists. */
  src: string
}

/**
 * Narration or music from the `av` surface. `waveform` is a coarse amplitude
 * envelope (0–1, ~64 buckets) the player draws without decoding the file.
 */
export interface AudioArtifact extends ArtifactBase {
  kind: 'audio'
  jobId: string | null
  prompt: string
  durationSec: number
  /** narration | music. Sound effects were offered and never served. */
  audioKind: 'narration' | 'music'
  /** What the model read, when it says. Empty for music. */
  transcript?: string
  model: string
  waveform: number[]
  src: string | null
}

export interface VideoArtifact extends ArtifactBase {
  kind: 'video'
  jobId: string | null
  prompt: string
  durationSec: number
  aspect: string
  model: string
  posterSrc: string
  src: string | null
}

export interface CodeArtifact extends ArtifactBase {
  kind: 'code' | 'html'
  language?: string
  content: string
  /** Set when this was written into a rendering template. */
  templateId?: string
  /** The plan behind the file — what is in it, without parsing the markup. */
  blocks?: { title: string; layout: string }[]
}

export type Artifact =
  | ReportArtifact
  | DeckArtifact
  | ChartArtifact
  | ImageArtifact
  | AudioArtifact
  | VideoArtifact
  | CodeArtifact

/* ── workspace ──────────────────────────────────────────────────────── */

export interface Project {
  id: string
  name: string
  description: string
  emoji: string
  instructions: string
  files: ProjectFile[]
  sessionIds: string[]
  skillIds: string[]
  /** The design system this project's output wears. Null is the default look. */
  designSystemId: string | null
  updatedAt: string
}

export interface ProjectFile {
  id: string
  name: string
  size: string
  type: string
  addedAt: string
  tokens: number
}

export interface Skill {
  id: string
  name: string
  slug: string
  description: string
  whenToUse: string
  /** The exact procedure sent when this skill is selected for a turn. */
  body: string
  /** Stable key for shipped skills; absent for user-authored procedures. */
  catalogKey: string | null
  /** Registry names that must be available after the agent allowlist. */
  requiredTools: string[]
  /** Approximate prompt cost shown before activation. */
  estimatedTokens: number
  source: 'built-in' | 'workspace' | 'personal'
  /** Which surfaces this skill applies to. */
  kinds: SessionKind[]
  enabled: boolean
  version: string
  files: string[]
  updatedAt: string
}

/* ── MCP connectors ─────────────────────────────────────────────────────
 * External systems over the Model Context Protocol. Servers are registered and
 * credentialed server-side; the browser sees which tools exist and whether they
 * are enabled.
 */

export type ConnectorStatus = 'connected' | 'disconnected' | 'needs_auth' | 'error'

export interface McpTool {
  name: string
  description: string
  /** Read-only tools can run unattended; write tools ask for confirmation. */
  readOnly: boolean
  enabled: boolean
}

export interface Connector {
  id: string
  name: string
  slug: string
  description: string
  category: string
  /** stdio servers run beside the API; http/sse ones are remote. */
  transport: 'stdio' | 'http' | 'sse'
  endpoint: string
  auth: 'none' | 'oauth' | 'api_key'
  status: ConnectorStatus
  installed: boolean
  enabled: boolean
  /** Surfaces where this connector's tools are offered. */
  kinds: SessionKind[]
  tools: McpTool[]
  /**
   * Credential names this connector holds — never their values. Set for
   * self-registered servers too, which have no catalogue entry to read from.
   */
  envKeys?: string[]
  official: boolean
  icon: string
  color: string
  lastSyncAt: string | null
  error?: string
}

/* ── governance ─────────────────────────────────────────────────────────
 * Audit and data-handling policy, backing the admin screens.
 */




export type MemoryType = 'user' | 'feedback' | 'project' | 'reference'

export interface MemoryEntry {
  id: string
  name: string
  description: string
  type: MemoryType
  body: string
  scope: 'global' | string
  links: string[]
  updatedAt: string
  pinned: boolean
}

export interface Agent {
  /** Who made it. Shared agents are read-only to everyone else. */
  ownerId: string
  ownerName: string
  id: string
  /** `org` agents appear in the shared store for everyone in the workspace. */
  visibility: 'private' | 'org'
  installs: number
  name: string
  slug: string
  description: string
  model: string
  systemPrompt: string
  /** null inherits; [] denies all; values form a hard allowlist. */
  tools: string[] | null
  /** null inherits turn selection; [] denies all selected skills. */
  skillIds: string[] | null
  kinds: SessionKind[]
  temperature: number
  color: string
  enabled: boolean
  runs: number
  /** Whether this caller can build the agent-only `search_knowledge` tool. */
  hasKnowledge: boolean
  updatedAt: string
}

export interface ToolCatalogEntry {
  name: string
  label: string
  available: boolean
}
