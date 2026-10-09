/**
 * KloudChat domain types: a `Session` of some `SessionKind` produces an `Artifact`; slow kinds carry a `Job`.
 * No proxy credential appears here: virtual keys are issued and used server-side only.
 */

import type { PatternName } from '@/components/slides/patterns'

/* ── identity ───────────────────────────────────────────────────────── */

export type UserRole = 'admin' | 'user'
export type UserStatus = 'active' | 'pending' | 'suspended'

export interface User {
  id: string
  email: string
  name: string
  role: UserRole
  status: UserStatus
  /** Credits are an internal unit, not provider prices. `creditsUsed` resets to 0 at `cycleResetsAt`; nothing rolls over. */
  monthlyCredits: number
  creditsUsed: number
  /** Null until an admin approves and opens the first cycle. */
  cycleResetsAt: string | null
  avatarColor: string
  /** Last four characters of the LiteLLM key, or null. The key itself never reaches the browser. */
  litellmKeyPreview: string | null
  litellmKeyIssuedAt: string | null
  /** Always present: the server fills in defaults. */
  preferences: Preferences
  /** Empty means the whole catalogue. */
  allowedModels: string[]
  createdAt: string
  /** Null for an account that has never signed in. */
  lastActiveAt: string | null
  /** Null while the mailed signup link is still out. */
  emailVerifiedAt?: string | null
}

/* ── models ─────────────────────────────────────────────────────────── */

type Modality = 'chat' | 'image' | 'audio' | 'video'

export interface ModelInfo {
  id: string
  /** "Vendor · Model". */
  label: string
  /** Model name without the vendor, for layouts that place the two separately. */
  name: string
  /** Company that built the model (Qwen, Anthropic, …) — not the routing slug. */
  vendor: string
  /** LiteLLM routing provider (`hosted_vllm`, `openrouter`, …). */
  provider: string
  /** From proxy metadata only; missing means `unknown`, never inferred from the id. */
  dataBoundary: 'self_hosted' | 'hybrid' | 'external' | 'unknown'
  strictLocal: boolean
  privacyOnly: boolean
  modality: Modality
  /** Video only: credits per second, keyed `<resolution>:<sound|silent>`. */
  creditPerSecond?: Record<string, number>
  /** Credits per generated picture; zero for non-image models. */
  creditPerImage?: number
  /** Image only: the ratios a picture from it can have. The composer offers no other. */
  aspects?: string[]
  /** Flat credits per call, for models billed per clip rather than per token. */
  creditPerCall?: number
  /** Which of the five surfaces may select this model. */
  kinds: SessionKind[]
  /** Credits per unit — per 1k output tokens for chat, per asset for image/audio/video. */
  creditCost: number
  /** Credits per 1k input tokens; 0 for non-conversational or self-hosted models. */
  inputCreditCost: number
  contextWindow?: number
  /** Trusted catalogue metadata only; absent means the training cutoff is unknown. */
  knowledgeCutoff?: string | null
  supportsVision?: boolean
  supportsTools?: boolean
  /** Set when the model is not reachable through LiteLLM and uses an adapter. */
  adapter?: string
  description: string
}

/* ── sessions ───────────────────────────────────────────────────────── */

/** `av` covers audio and video: one surface, producing an `audio` or `video` artifact by mode. */
export type SessionKind = 'chat' | 'report' | 'slides' | 'image' | 'av'
/** `auto` routes low-complexity turns cheaper; `auto_quality` routes high-complexity turns up. */
export type RoutingMode = 'manual' | 'auto' | 'auto_quality'

/** What a session produced, as measurements so the row can be phrased in the reader's language. */
export interface SessionMade {
  kind: 'image' | 'video' | 'narration' | 'music'
  count: number
  /** Zero where unknown or where the artifacts disagree; zero is not printed. */
  seconds: number
  aspect: string
}

/** One thing a stopped generation needs answered. */
export interface PendingQuestion {
  id: string
  question: string
  /** Suggested answers; free text is always accepted too. */
  options: string[]
  detail: string
  /** Gap questions: the fact key, its name, why it matters and what stays if unanswered. */
  fact?: string
  label?: string
  why?: string
  placeholder?: string
  freeText?: boolean
}

/** A generation waiting on the person who asked for it. */
export interface PendingPlan {
  /** `clarify` holds a question, `outline` the plan, `figures` the offer to draw the planned pictures. */
  stage: 'clarify' | 'outline' | 'figures'
  /** Proposed pictures, by section index. */
  figures?: { section: number; caption: string; prompt: string }[]
  /** Approximate cost of drawing them. */
  figureCredits?: number
  figureModel?: string
  /** The request with any answers already folded in. */
  request: string
  attachments: string[]
  answers: Record<string, string>
  /** The waiting pipeline run this card belongs to. */
  runId?: string
  questions?: PendingQuestion[]
  plan?: {
    title?: string
    visualStyle?: 'editorial' | 'poster' | 'minimal' | 'dark' | 'split' | 'warm' | 'mono' | 'pastel' | 'forest' | 'slate' | 'paper'
    /** Whether the deck is meant to support a speaker or stand alone when shared. */
    density?: 'speaker' | 'reading'
    /** Slides and template blocks carry a layout; report sections are titles. */
    slides?: { title: string; layout: string }[]
    blocks?: { title: string; layout: string }[]
    sections?: string[]
  }
}

export interface Session {
  id: string
  kind: SessionKind
  title: string
  projectId: string | null
  agentId: string | null
  /** The user pipeline this conversation runs (「이 워크플로로 시작」); its turns stay here. */
  pipelineId?: string | null
  model: string
  /** Under `auto`, `model` is the quality ceiling. */
  routingMode: RoutingMode
  createdAt: string
  updatedAt: string
  pinned: boolean
  messages: Message[]
  /** Latest message, one line; list views carry this instead of `messages`. */
  preview: string | null
  messageCount: number
  /** Null wherever there is a transcript. */
  made: SessionMade | null
  /** Artifact this session is currently producing, if any. */
  artifactId: string | null
  /** A generation stopped and waiting on the person; while set, typing answers it rather than starting a new turn. */
  pending: PendingPlan | null
  /** Skills switched on for the whole conversation; picked once, kept for later turns. */
  skillIds: string[]
  /** Sticky rendering template; null is the surface's built-in track. */
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
  /** 개인 맞춤 설정; empty strings when unset. */
  aboutMe?: string
  responseStyle?: string
  /** The model picked on each surface, kept with the account across browsers. */
  modelByKind?: Partial<Record<SessionKind, string>>
  avModelByMode?: Partial<Record<'audio' | 'video', string>>
}

export type PrivacyAction = 'route_strict_local' | 'mask_external' | 'send_raw_external'

export interface CostRouting {
  mode: 'auto' | 'auto_quality'
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
  toolOutputMasked?: number
  toolOutputFindings?: { category: string; source: string; count: number }[]
  initialAction?: PrivacyAction | 'strict_local' | 'none'
  costRouting?: CostRouting
  accuracy?: {
    policy: 'grounded-best-effort-v1'
    knowledgeCutoff: string | null
    cutoffSource: 'model_catalogue' | 'unknown'
  }
  lengthCheck?: LengthCheck
}

/** A cover letter's 「[제출용 답안 n]」 blocks as the server measured them (text-fit). */
export interface LengthCheck {
  rows: {
    number: number
    unit: 'chars_with_spaces' | 'chars_no_spaces' | 'bytes_utf8' | 'bytes_ko2'
    value: number
    limit: number | null
    min: number | null
    status: 'ok' | 'over' | 'under'
    newline?: number
    rewritten?: boolean
    assumed?: string
  }[]
  /** Numbers, titles or blind-hiring words the person's material does not carry. */
  flags: string[]
  /** The judge's 「same experience told the same way」 candidates, for the person to check. */
  repeats?: string[]
}

/** A server policy reply found in stored transcripts; current turns answer through a model with caveats. */
export interface FreshnessAbstention {
  answerOrigin: 'server_policy'
  actualModel: null
  freshness: {
    status: 'unverified'
    reason: 'verification_unavailable' | 'lookup_failed_or_empty'
  }
}

/** An answer written by a tool rather than a model; routing may still have run before it. */
export interface ToolResultAnswer {
  answerOrigin: 'tool_result'
  toolName: 'calculate'
  reasonCode: 'division_by_zero'
  actualModel: null
}

export type MessageRouting = PrivacyRouting | FreshnessAbstention | ToolResultAnswer

export type Role = 'user' | 'assistant' | 'system'

/* ── pipeline runs ──────────────────────────────────────────────────── */

export type StageKind = 'compute' | 'model' | 'check' | 'io'
export type StageStatus = 'done' | 'skipped' | 'stopped' | 'failed' | 'waiting' | 'cancelled'

/** What a stage computed, found, changed or ran; present on live records and the full run. */
export interface StageEvidence {
  computed?: { name: string; value: string }[]
  computedBlock?: string
  findings?: { before?: string[]; after?: string[] }
  changes?: { sectionId?: string; heading?: string; old: string; new: string }[]
  code?: { code: string; stdout?: string; stderr?: string }[]
  questions?: PendingQuestion[]
}

/** One stage of a pipeline run, as it ended. */
export interface PipelineStage {
  /** runId:stage:attempt. */
  id: string
  runId?: string
  name: string
  label: string
  kind: StageKind
  status: StageStatus
  reason?: string
  summary?: string
  startedAt?: string
  finishedAt?: string
  ms?: number
  attempt?: number
  rerunnable?: boolean
  evidence?: StageEvidence
}

/** A pipeline run a message settled (`routing.run`), or the one streaming now. */
export interface PipelineRun {
  id: string
  pipeline?: string
  flow?: string
  status: 'running' | 'done' | 'stopped' | 'failed' | 'waiting' | 'cancelled'
  parentId?: string | null
  fromStage?: string | null
  stages: PipelineStage[]
}

/** A highlighted span in a document: a blank, a value the grounding check blanked, a flagged sentence. */
export interface DocMarkSpan {
  kind: 'placeholder' | 'ungrounded-replaced' | 'check-finding'
  start?: number
  end?: number
  text: string
  original?: string
  reason?: string
  stage?: string
  code?: string
}

export interface DocMarks {
  artifactId?: string
  version?: number
  runId?: string
  sections: { sectionId: string; heading?: string; spans: DocMarkSpan[] }[]
}

/* ── user pipelines ─────────────────────────────────────────────────── */

/** What a step of a user pipeline is. */
export type PipelineStepKind = 'agent' | 'skill' | 'check' | 'review' | 'human' | 'export'

/** One step of a user pipeline, as stored. */
export interface PipelineStep {
  id: string
  kind: PipelineStepKind
  ref: string
  settings: {
    instruction?: string
    catalogKey?: string
    model?: string
    repair?: boolean
    focus?: string
    formats?: string[]
  }
  /** Korean name, from the server. */
  title?: string
  /** An agent step: `document` or `answer`. */
  output?: string
}

/** One problem with a pipeline's order; `step` is 1-based, 0 for the whole pipeline. */
export interface PipelineProblem {
  step: number
  code: string
  message: string
}

export interface PipelineVerdict {
  ok: boolean
  surface: '' | 'report' | 'chat'
  problems: PipelineProblem[]
  warnings: PipelineProblem[]
  titles?: string[]
}

/** A pipeline a person composed; shared through approval. */
export interface UserPipeline {
  id: string
  name: string
  description: string
  base: string | null
  originId: string | null
  stages: PipelineStep[]
  visibility: 'private' | 'org'
  review: '' | 'pending' | 'approved' | 'rejected'
  reviewNote: string
  reviewedAt: string | null
  version: number
  installs: number
  ownerId: string
  ownerName: string
  surface?: '' | 'report' | 'chat'
  problems?: PipelineProblem[]
  warnings?: PipelineProblem[]
  boundAgents: { id: string; name: string }[]
  createdAt: string | null
  updatedAt: string | null
  /** Store rows only. */
  installed?: boolean
  mine?: boolean
}

/** What 「+ 단계 추가」 offers besides agents. */
export interface PipelineCatalogue {
  maxSteps: number
  skills: { ref: string; name: string; description: string; summary: string }[]
  checks: { ref: string; name: string; description: string; needs: string[] }[]
  human: { ref: string; name: string; description: string }[]
  exportFormats: string[]
  reviewModels: { id: string; label: string }[]
}

/** A pipeline definition, read-only (editing is phase 3). */
export interface PipelineDefinition {
  name: string
  formats?: string[]
  agents?: string[]
  options?: Record<string, unknown>
  checks?: string[]
  flows: Record<string, { name: string; label: string; kind: StageKind; checks?: string[] }[]>
  lists?: Record<string, string[]>
  /** What each step of the drawn chain does, in plain words (keyed by `ChainStep`). */
  summary?: Record<string, string>
  /** People's names for the check and pre-pass ids above. */
  checkNames?: Record<string, string>
}

/** A unit of visible work inside an assistant turn. */
export interface Step {
  id: string
  /** `pipeline`: a stage record without the rich `stage` object (older servers, or a fallback). */
  type: 'thinking' | 'tool' | 'artifact' | 'pipeline'
  label: string
  status: 'running' | 'done' | 'error'
  detail?: string
  progress?: { current: number; total: number }
  skills?: {
    id: string
    name: string
    catalogKey: string | null
    estimatedTokens: number
  }[]
  /** Names of the memories this turn was given. Never their bodies. */
  memories?: string[]
  files?: {
    name: string
    state: 'included' | 'truncated' | 'omitted' | 'unreadable'
    keptChars: number
    totalChars: number
  }[]
  memoriesWritten?: number
  /** Files a sandbox tool made and stored for the user, downloadable by id. */
  outputs?: { id: string; name: string; size: number }[]
  /** The code a sandbox tool ran last (bounded, masked); absent on older steps. */
  code?: string
  /** Which attempt wrote `code`, when the tool retried. */
  codeAttempt?: number
  /** Heads of what that run printed. */
  stdout?: string
  stderr?: string
  /** Total memories, when only the most recent were loaded. */
  totalMemories?: number
  /** Which halves of 개인 맞춤 설정 shaped the turn. */
  personal?: string[]
  estimatedTokens?: number
  /** Earlier messages the model's window could not hold, replaced by a summary. */
  summarisedTurns?: number
  /** Whether a summary was actually written for them. */
  summarised?: boolean
}

/** An answer a rerun replaced; kept so the reader can page back to it. */
export interface EarlierAnswer {
  id: string
  content: string
  model?: string | null
  usage?: Message['usage'] | null
  createdAt?: string | null
}

export interface Message {
  id: string
  /** The id came from a stored transcript, not a local optimistic turn. */
  persisted?: boolean
  role: Role
  content: string
  /** Earlier answers to the same question, oldest first; the conversation continues from this one. */
  superseded?: EarlierAnswer[]
  createdAt: string
  model?: string | null
  routing?: MessageRouting
  steps?: Step[]
  /** The pipeline run this answer settled, from `routing.run` or the live stream. */
  run?: PipelineRun
  artifactIds?: string[]
  /** The version of `artifactIds[0]` this turn wrote; absent on media turns and live rows. */
  artifactVersion?: number
  /** `id` names the stored blob; absent only on the optimistic row while the upload is in flight. */
  attachments?: { id?: string; name: string; size: number | string; type: string }[]
  /** `estimated` on a stopped turn: the server counts tokens itself since the proxy's final chunk never arrives. */
  /** Also `outlineInputTokens`, `model:<id>:inputTokens` … when a planner or reviewer ran (`lib/usage.ts`). */
  usage?: {
    inputTokens: number
    outputTokens: number
    credits: number
    estimated?: boolean
    [counter: string]: number | boolean | undefined
  }
  liked?: 'up' | 'down' | null
  /** Legacy: the retired 시작점 an old turn began from. */
  startedFrom?: { templateId: string; title: string }
  /** Separate from `content`: a turn can fail after writing something. */
  error?: string
  /** Server-recorded end state; `error` wins while this tab is live. `no_answer` sits on the question, `interrupted` on the partial reply. */
  failure?: 'no_answer' | 'interrupted' | 'stopped'
}

/* ── jobs ───────────────────────────────────────────────────────────── */

type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'

export interface Job {
  id: string
  sessionId: string
  kind: SessionKind
  status: JobStatus
  /** 0–100. Providers that report no progress get a coarse stage-based estimate. */
  progress: number
  stage: string
  /** Charged on success only; a failed job never deducted anything. */
  creditsUsed: number
  creditsEstimated: number
  error?: string
  createdAt: string
  finishedAt: string | null
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
  /** Listing copy with a trimmed body; `refreshArtifact` fetches the whole document and clears this. */
  partial?: boolean
  /** Local draft a run is still streaming into; the server does not hold it yet. */
  draft?: boolean
  /** Linter findings as of when this was written. */
  lint?: LintFinding[]
  /** Absent until requested; costs a model call. */
  critique?: Critique
}

/** A model review; nothing is gated on the score. */
export interface Critique {
  score: number
  findings: LintFinding[]
  model: string
  at: string
}

/** `P0`: the document is wrong. `P1`: it reads badly. Nothing is corrected automatically. */
export interface LintFinding {
  severity: 'P0' | 'P1'
  /** `placeholder` · `invented-metric` · `filler` · `empty` · … */
  rule: string
  message: string
  /** The heading it was found under, or empty for the whole document. */
  where: string
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
  /** Absent means `markdown`. `html` is a hand-formatted section; the server sanitises it (`services/richtext.py`). */
  format?: 'markdown' | 'html'
  /** Rendered mermaid diagrams keyed by source hash; the browser renders and posts them, the exporters read them. */
  diagrams?: Record<string, string>
  factCheck?: FactCheck
}

/** Design values every renderer and exporter reads. Always complete on the wire. */
export interface DesignTokens {
  accent: string
  ink: string
  muted: string
  font: 'gothic' | 'serif'
  visualStyle?: 'editorial' | 'poster' | 'minimal' | 'dark' | 'split' | 'warm' | 'mono' | 'pastel' | 'forest' | 'slate' | 'paper'
  /** Footer line on every slide and page. */
  footer?: string
  /** `data:` URI, so exported files carry the mark with them. */
  logo?: string
}

export interface ReportArtifact extends ArtifactBase {
  kind: 'report'
  sections: ReportSection[]
  sources: Source[]
  /** Search trail behind the source shelf. */
  research?: {
    enabled: boolean
    searched: boolean
    queries: string[]
    selected: number
    excluded: number
    webSelected?: number
    projectSelected?: number
    projectExcluded?: number
  }
  /** Citation style the export renders. */
  citationStyle: 'APA' | 'MLA' | 'Chicago' | 'IEEE'
  wordCount: number
  pageSettings?: {
    header?: string
    footer?: string
    pageNumbers?: 'none' | 'page' | 'page-total'
    firstPageHeader?: boolean
    margins?: { top: number; right: number; bottom: number; left: number }
  }
  reviewComments?: {
    id: string
    sectionId: string
    quote: string
    body: string
    status: 'open' | 'resolved'
    createdAt: string
  }[]
  /** 서식 the page view is drawn in; absent is the plain report seed. */
  templateId?: string
  design?: DesignTokens | null
  /** Head of a document by purpose (`services/doc_formats.py`); absent is a plain title. */
  titleBlock?: ReportTitleBlock | null
  /** Blanks and check findings stored at save; `GET /artifacts/{id}/marks` re-locates them live. */
  marks?: DocMarks | null
}

/** A document's head by purpose: cover page, field table, memo rows, press or paper header. */
export interface ReportTitleBlock {
  format: string
  label: string
  head: 'cover' | 'header' | 'memo' | 'press' | 'paper' | 'draft'
  numbering: 'decimal' | 'roman' | 'korean' | 'official' | 'none'
  /** `[label, value]`; an empty value is a blank the person fills in, shown as its name in brackets (「[작성자]」). */
  fields: [string, string][]
  subtitle?: string
  abstract?: string
  keywords?: string[]
  /** `gaejo`: lists drawn as □ / ○ / - / ㆍ and ※ lines as notes (개조식). */
  lists?: 'gaejo'
  /** The renderers end the body with 「끝.」. */
  closing?: boolean
}

export interface ChartArtifact extends ArtifactBase {
  kind: 'chart'
  chartType: 'bar' | 'line' | 'stacked'
  caption: string
  xLabel: string
  yLabel: string
  series: { name: string; color: string; points: { x: string; y: number }[] }[]
  /** The rows the chart was computed from. */
  table: { columns: string[]; rows: (string | number)[][] }
  sourceFile: string
}

/** Per-claim verification result. */
export interface FactCheck {
  status: 'unchecked' | 'checking' | 'done'
  claims: {
    id: string
    text: string
    verdict: 'supported' | 'unsupported' | 'uncertain'
    note: string
    /** Required for `supported`/`unsupported`; without it the server downgrades to `uncertain`. */
    sourceUrl?: string
  }[]
}

export interface Slide {
  id: string
  /** Layout supported by `deck._LAYOUTS`. */
  layout:
    | 'title'
    | 'section'
    | 'agenda'
    | 'bullets'
    | 'two-column'
    | 'quote'
    | 'statement'
    | 'chart'
    | 'table'
    | 'metrics'
    | 'big-number'
    | 'bands'
    | 'tiles'
    | 'timeline'
    | 'steps'
    | 'cards'
    | 'closing'
    | PatternName
  title: string
  /** `section` only: `01.`, `02.` over the title. */
  number?: string
  bullets?: string[]
  /** A table, first row the head. */
  rows?: string[][]
  /** `[값, 이름]`. */
  metrics?: [string, string][]
  /** `[이름, 문장]` label-beside-text shapes. */
  bands?: [string, string][]
  /** `[글자/숫자, 캡션]`. */
  tiles?: [string, string][]
  /** `[날짜, 사건]`. */
  timeline?: [string, string][]
  /** `[단계, 한 줄]`, numbered by position. */
  steps?: [string, string][]
  /** `[이름, 한두 줄]`, titled boxes side by side. */
  cards?: [string, string][]
  /** Pattern slides of shape `pairs` (see `slide_patterns.py`): `[left, right]`. */
  items?: [string, string][]
  /** Pattern slides of shape `columns`: named lists side by side. */
  columns?: { title: string; items: string[] }[]
  /** Every series carries exactly as many values as there are categories. */
  chart?: {
    kind: 'bar' | 'line' | 'pie' | 'donut' | 'hbar' | 'stacked'
    unit?: string
    categories: string[]
    series: { name: string; values: number[] }[]
  }
  body?: string
  notes?: string
  accent?: string
  factCheck?: FactCheck
  /** Text size as a multiple of the 서식's own; `deck_export` applies the same factor. */
  textScale?: number
  /** Sanitised inline HTML keyed by text slot (`title`, `bullets.0`, `rows.1.2`, …); the plain fields stay canonical. */
  richText?: Record<string, string>
  /** `src` is a `data:` URI so the deck stays one self-contained file. */
  image?: {
    src: string
    caption?: string
    fit?: 'contain' | 'cover'
    position?: 'left' | 'right'
    /** `full`: the picture alone under the title. */
    size?: 'small' | 'medium' | 'large' | 'full'
    /** The browser's raster of `diagram`, stored for the exporters; a placed picture clears it. */
    diagram?: boolean
    /** 'pptx': drawn on the server from the deck renderer's shapes; shown as is. */
    renderer?: string
    key?: string
  }
  /** A structure, flow, comparison or concept figure the deck drew for itself as mermaid.
   *  The panel renders it live; its raster travels in `image` for the exporters. */
  diagram?: {
    figure: 'method' | 'flow' | 'compare' | 'concept'
    description: string
    source: string
    caption: string
    /** `report_export.diagram_key` of `source`; the raster is stored under it. */
    key: string
  }
}

export interface DeckArtifact extends ArtifactBase {
  kind: 'deck'
  theme: string
  slides: Slide[]
  /** Deck 서식 this was written under; the export builds on its PowerPoint half. */
  templateId?: string
  /** Copied on when the deck was made. */
  design?: DesignTokens | null
  reviewComments?: {
    id: string
    slideId: string
    body: string
    status: 'open' | 'resolved'
    createdAt: string
  }[]
}

export interface ImageArtifact extends ArtifactBase {
  kind: 'image'
  jobId: string | null
  prompt: string
  /** Ratio asked for. */
  aspect: string
  /** Ratio measured off the bytes; absent on older rows. */
  actualAspect?: string
  width?: number
  height?: number
  style: string
  labels?: string
  /** What the model was actually sent; re-sent as-is with `raw`. */
  composedPrompt?: string
  /** `matplotlib`: drawn from sandbox code, which `composedPrompt` then holds. */
  engine?: 'matplotlib'
  seed: number
  model: string
  src: string
  /** 도식일 때: the mermaid source the picture was rendered from. */
  figure?: string
  source?: string
  caption?: string
}

/** `waveform` is a coarse amplitude envelope (0–1, ~64 buckets). */
export interface AudioArtifact extends ArtifactBase {
  kind: 'audio'
  jobId: string | null
  prompt: string
  durationSec: number
  audioKind: 'narration' | 'music'
  /** Empty for music. */
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
  templateId?: string
  /** Outline of the file, without parsing the markup. */
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
  /** Surface → rendering template id; a surface with no entry uses the built-in track. */
  renderTemplates: Record<string, string>
  updatedAt: string
}

export interface ProjectFile {
  id: string
  name: string
  size: string
  type: string
  addedAt: string
  tokens: number
  sourceUrl?: string | null
  preview: string
  error?: string | null
}

export interface Skill {
  id: string
  name: string
  slug: string
  description: string
  whenToUse: string
  body: string
  /** Stable key for shipped skills; null for user-authored ones. */
  catalogKey: string | null
  requiredTools: string[]
  /** Approximate prompt cost. */
  estimatedTokens: number
  source: 'built-in' | 'workspace' | 'personal'
  kinds: SessionKind[]
  enabled: boolean
  /** `org` lists it in the store for the workspace to copy. */
  visibility: 'private' | 'org'
  installs: number
  originId: string | null
  version: string
  updatedAt: string
  /** Fixed scripts its package ships; empty for a plain skill. */
  scripts?: string[]
}

/* ── MCP connectors ─────────────────────────────────────────────────── */

export type ConnectorStatus = 'connected' | 'disconnected' | 'needs_auth' | 'error'

interface McpTool {
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
  kinds: SessionKind[]
  tools: McpTool[]
  /** Credential names only, never values. */
  envKeys?: string[]
  official: boolean
  icon: string
  color: string
  lastSyncAt: string | null
  error?: string
}

/* ── memory & agents ─────────────────────────────────────────────────── */

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
  /** Shared agents are read-only to everyone but the owner. */
  ownerId: string
  ownerName: string
  id: string
  visibility: 'private' | 'org'
  installs: number
  /** Stable key for shipped agents; null for user-authored ones. */
  catalogKey: string | null
  /** The owner's user pipeline this agent's turns run; null runs its own flow. */
  pipelineId?: string | null
  originId: string | null
  /** Published by an administrator; meaningful on store rows. */
  official: boolean
  /** The caller already holds a copy; store rows only. */
  installed: boolean
  name: string
  slug: string
  description: string
  model: string
  systemPrompt: string
  guide: string
  starters: string[]
  /** `sealed`: copies run the author's prompt without being able to read it. */
  shareMode: 'open' | 'sealed'
  /** Prompt is absent here and read from the original at run time. */
  sealed: boolean
  /** null inherits; [] denies all; values form a hard allowlist. */
  tools: string[] | null
  /** null inherits turn selection; [] denies all selected skills. */
  skillIds: string[] | null
  kinds: SessionKind[]
  /** Surface a new conversation opens on; null takes the first of `kinds`. */
  surface: SessionKind | null
  /** 서식 for sessions on that 서식's surface; wins over the project's default. */
  renderTemplateId: string | null
  /** `doc_formats` id for report sessions whose 서식 fixes none. */
  docFormat: string | null
  /** Wins over the project's design. */
  designSystemId: string | null
  /** That design's name, for a viewer who cannot see the row. */
  designName?: string
  temperature: number
  color: string
  enabled: boolean
  runs: number
  hasKnowledge: boolean
  updatedAt: string
}

/** A store row: somebody else's skill, kept out of the composer's own list. */
export interface StoreSkill extends Skill {
  ownerId: string
  ownerName: string
  official: boolean
  installed: boolean
}

export interface ToolCatalogEntry {
  name: string
  label: string
  available: boolean
  /** 「기본 도구」 or the connector's name; older servers send none. */
  group?: string
  /** Short name a person reads; the raw `name` is technical detail. */
  title?: string
  /** One line on what it does. */
  description?: string
}
