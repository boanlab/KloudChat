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
  /** Null while the mailed signup link is still out. */
  emailVerifiedAt?: string | null
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
  /** Image only: the ratios a picture from it can have. The composer offers no other. */
  aspects?: string[]
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
//: `auto` spends less, `auto_quality` spends more. One classifier decides
//: both — the cost lane acts on its `low`, the quality lane on its `high`.
export type RoutingMode = 'manual' | 'auto' | 'auto_quality'

/**
 * What a session produced, when that is all it has to show for itself.
 *
 * A picture or clip session whose turn predates message recording holds none,
 * so `preview` is null. Measurements rather than a sentence, so the sentence
 * can be written in the reader's language.
 */
export interface SessionMade {
  /** The noun the row prints. Speech and music are separate although both are
   *  `audio` artifacts: "내레이션 3개" and "음악 3곡" are not the same row. */
  kind: 'image' | 'video' | 'narration' | 'music'
  count: number
  /** Zero where unknown, and where the artifacts disagree — an unmeasured MP3,
   *  or two batches shot at two ratios. Nothing is printed for a zero. */
  seconds: number
  aspect: string
}

/** One thing a stopped generation needs answered. */
export interface PendingQuestion {
  id: string
  question: string
  /** Suggested answers. Never a closed set — every question takes prose too. */
  options: string[]
  /** The fact behind the question: which file, how much of it arrived. */
  detail: string
}

/** A generation waiting on the person who asked for it. */
export interface PendingPlan {
  /**
   * `clarify` is holding a question; `outline` is holding what it will write;
   * `figures` is holding the second question — whether to draw the pictures
   * the planner found a place for, and what that costs.
   */
  stage: 'clarify' | 'outline' | 'figures'
  /** Proposed pictures, by the index of the section each belongs to. */
  figures?: { section: number; caption: string; prompt: string }[]
  /** What saying yes costs, as shown on the card. Approximate on purpose. */
  figureCredits?: number
  /** The image model that would draw them, named so the card can say. */
  figureModel?: string
  /** The request it began from, with any answers already folded in. */
  request: string
  attachments: string[]
  answers: Record<string, string>
  questions?: PendingQuestion[]
  plan?: {
    title?: string
    visualStyle?: 'editorial' | 'poster' | 'minimal' | 'dark' | 'split' | 'warm' | 'mono'
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
  /** What this conversation made, for the rows `preview` cannot serve. Null
   *  wherever there is a transcript: the last thing said beats a count of it. */
  made: SessionMade | null
  /** Artifact this session is currently producing, if any. */
  artifactId: string | null
  /**
   * A generation that has stopped and is waiting on the person.
   *
   * The document surfaces plan before they write and ask before they plan when
   * the material cannot carry the request. Neither of those turns produces an
   * artifact, which is what keeps whatever the session already holds from
   * being replaced by a run nobody looked at.
   *
   * While this is set, typing is a note on what is waiting rather than a new
   * request — the back-and-forth these surfaces never had.
   */
  pending: PendingPlan | null
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
  /** 개인 맞춤 설정: what every conversation knows about the person, and how
   *  answers should be written. Empty strings when unset. */
  aboutMe?: string
  responseStyle?: string
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
  /** Names of the memories this turn was given. Never their bodies. */
  memories?: string[]
  /** How much of each file reached the model. */
  files?: {
    name: string
    state: 'included' | 'truncated' | 'omitted' | 'unreadable'
    keptChars: number
    totalChars: number
  }[]
  /** Memories the extractor wrote out of this turn. */
  memoriesWritten?: number
  /** How many memories exist, when only the most recent were loaded. */
  totalMemories?: number
  /** Which halves of 개인 맞춤 설정 shaped the turn: 나에 대해, 답변 방식. */
  personal?: string[]
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

/**
 * A 시작점 waiting on the next turn: what the chip says, what the composer
 * asks the person to bring, and the id the turn carries.
 *
 * Not the prompt — the framing is the server's to add.
 */
export interface StartingPoint {
  id: string
  title: string
  fills: string[]
  /**
   * The request as the card's form assembled it, when the card had one. Put
   * into the composer so the person reads the whole thing before sending
   * rather than trusting five blanks they filled behind a dialogue.
   */
  text?: string
  /** One worked example per blank, in `fills` order — the placeholder. */
  examples?: string[]
  /** What the job cannot run without: 'web' | 'file'. */
  needs?: string[]
  /** Workspace skills to switch on for the turn, by name. */
  skills?: string[]
  /**
   * How each blank is asked, in `fills` order. A closed list is a picker, a
   * paragraph is a textarea, anything else a line. Media 서식 carry these
   * from their arguments; a written starting point has plain lines.
   */
  blanks?: { name: string; options?: string[]; long?: boolean }[]
  /**
   * Media 서식 only: the sentence the blanks fill, `{name}` per blank. The
   * composer writes the request out of it; a blank left empty keeps its
   * example, so the sentence is always whole.
   */
  examplePrompt?: string
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
  /**
   * What was uploaded with this turn. `id` names the stored blob, so a reader
   * can take the file back out of the conversation months later; it is absent
   * only for the optimistic row drawn while the upload is still in flight.
   */
  attachments?: { id?: string; name: string; size: number | string; type: string }[]
  /**
   * `estimated` is set on a stopped turn: the proxy reports usage on its final
   * chunk, which a stopped stream never reaches, so the server counts what it
   * sent and what came back instead of writing 0 in · 0 out.
   */
  usage?: { inputTokens: number; outputTokens: number; credits: number; estimated?: boolean }
  liked?: 'up' | 'down' | null
    /**
     * The 시작점 this turn began from, by title rather than prompt: the
     * transcript keeps the person's own words, not the product's framing.
     */
  startedFrom?: { templateId: string; title: string }
  /**
   * Why the turn ended badly. Separate from `content`: a turn can fail after
   * writing something, and that half an answer is worth keeping.
   */
  error?: string
    /**
     * How the turn ended, as the server recorded it. `error` is this tab's live
     * account and wins while it is on screen; this is what a reload leaves.
     *
     * `no_answer` sits on the question — nothing spoke. `interrupted` sits on
     * the reply — some of it arrived. `stopped` is either, when 중단 was
     * pressed: the same shape as the other two, but the reader's own doing.
     */
  failure?: 'no_answer' | 'interrupted' | 'stopped'
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
  /**
   * True while this is the listing's copy, whose body was cut down to what a
   * card needs. Anything that renders or edits the whole document fetches it
   * by id first — `refreshArtifact` is what clears this.
   */
  partial?: boolean
  /**
   * True while this is the local draft a run is streaming into, before the
   * `artifact` event swaps in the saved document. It is the honest answer to
   * "is this still being written", which is what the controls need to know:
   * export would 404 on a document the server does not have yet, and an edit
   * would be overwritten by the next event of the run that is still going.
   *
   * A report answers that question from its sections' own `status`. A deck had
   * no equivalent and asked whether every slide had content instead — which is
   * the same answer almost always, and the wrong one exactly when a slide came
   * back empty: the whole deck stayed locked, including 텍스트 수정, which is
   * the one control that could have fixed it.
   */
  draft?: boolean
  /**
   * What the linter found when this was written. Stored on the artifact, so a
   * document that was fine when it was made does not start reporting problems
   * because the rules were tightened afterwards.
   */
  lint?: LintFinding[]
  /**
   * One reading by somebody who did not write it. Absent until asked for — it
   * costs a model call, unlike the linter beside it.
   */
  critique?: Critique
}

/**
 * A review, not a gate.
 *
 * The score is an opinion with a number on it; nothing is blocked by it, and
 * the findings are the part worth acting on. They carry the linter's shape so
 * the panel shows one list of things to look at rather than two.
 */
export interface Critique {
  score: number
  findings: LintFinding[]
  model: string
  at: string
}

/**
 * One thing worth looking at before this goes anywhere.
 *
 * `P0` means the document is wrong — a placeholder nobody replaced, a figure
 * nobody could have sourced. `P1` means it reads badly. Nothing is corrected
 * automatically: the check is free, and the rewrite is a decision.
 */
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
  /**
   * How `content` is stored.
   *
   * Absent or `markdown` is what the model writes and what every report held
   * before the document editor shipped. `html` is a section somebody has
   * formatted by hand — size, face, alignment and tables have no Markdown, so
   * storing those as Markdown means throwing them away on save. The server
   * sanitises an `html` body on the way in and converts it back for the
   * exporters; see `services/richtext.py`.
   */
  format?: 'markdown' | 'html'
  /**
   * Pictures of this section's mermaid diagrams, by the key their source
   * hashes to. Mermaid renders in JavaScript and the API has no headless
   * browser, so whoever opens the document draws them and posts them back —
   * which is how the `.docx` gets a figure where the source stands.
   */
  diagrams?: Record<string, string>
  /**
   * What the web said about the figures in this section, when somebody asked.
   *
   * The same shape a slide carries, because it is the same call — a claim does
   * not care what shape it was printed in. It arrived on the report later than
   * on the deck, which was backwards: a slide gets argued with in the room it
   * is shown in, and a report gets exported and mailed.
   */
  factCheck?: FactCheck
}

/**
 * The four values every renderer and exporter reads. Always complete on the
 * wire.
 *
 * Defined here rather than in `lib/api` because the artifact types need it and
 * `lib/api` already imports from this file — the other direction would be a
 * cycle. `lib/api` re-exports it, so its existing importers are unchanged.
 */
export interface DesignTokens {
  accent: string
  ink: string
  muted: string
  font: 'gothic' | 'serif'
  /** Composition, independent from colour: the same deck can wear a different visual rhythm. */
  visualStyle?: 'editorial' | 'poster' | 'minimal' | 'dark' | 'split' | 'warm' | 'mono'
  /**
   * The line at the foot of every slide and page saying whose this is, and the
   * mark beside it as a `data:` URI.
   *
   * Bytes rather than a link: a deck is downloaded, mailed and opened on a
   * machine that has never heard of this server, so a URL would be a broken
   * image on exactly the day it matters. Empty is what the product drew before
   * these existed.
   */
  footer?: string
  logo?: string
}

export interface ReportArtifact extends ArtifactBase {
  kind: 'report'
  sections: ReportSection[]
  sources: Source[]
  /** The reproducible search trail used to assemble this report's source shelf. */
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
  /**
   * The 서식 the page view wears. A view, not a fork — the sections above are
   * the document either way, and this only decides what it is drawn in.
   * Absent falls back to the plain report seed.
   */
  templateId?: string
  /** The project's design system, when it has one. Colours and the body face. */
  design?: DesignTokens | null
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
  /**
   * Which of the shapes `deck._LAYOUTS` offers. `image` used to be here and
   * was never a layout: a picture arrives on `image` below and the exporters
   * size it from what else the slide holds, so a picture-only slide already
   * gets the full width without anyone naming a layout for it. Nothing read
   * the value, and a member nothing reads is a shape somebody will one day
   * write into an artifact and then wonder why it renders as bullets.
   */
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
  title: string
  /**
   * A section divider's own number — `01.`, `02.` — over its title.
   *
   * Only `section` uses it. A divider that names the part and nothing else
   * leaves the reader counting backwards through the deck to place it, which
   * is the one question a divider exists to answer.
   */
  number?: string
  bullets?: string[]
  /**
   * A table, first row the head. The commonest slide in a working deck and the
   * last one this type could describe: the `.pptx` and `.pdf` writers had both
   * drawn `rows` for a long time, the model was never asked for one, and this
   * type had nowhere to put it — so a comparison came out as six bullets the
   * reader had to rebuild the table from.
   */
  rows?: string[][]
  /**
   * Figures worth setting large, as `[값, 이름]` — the slide whose point is a
   * number rather than a sentence. Held apart from `rows` because a table is
   * for reading values against each other and this is for remembering one:
   * drawn as a table they would be read at the same weight as anything else,
   * which is the thing this layout exists to avoid.
   */
  metrics?: [string, string][]
  /**
   * The three shapes that are a left thing and a right thing.
   *
   * One data shape, three designs, because that is what they are: `bands` is a
   * name beside a sentence — 미션 · 배경 · 추진전략, the row-label opening every
   * Korean 사업 발표 has and the one thing a bullet cannot say, having nowhere
   * to put the name of what it is. `tiles` is a letter or a number over a
   * caption. `timeline` is a date beside what happened.
   */
  bands?: [string, string][]
  tiles?: [string, string][]
  timeline?: [string, string][]
  /** `[단계, 한 줄]` — a procedure across the slide, numbered by position. */
  steps?: [string, string][]
  /** `[이름, 한두 줄]` — peers side by side as titled boxes. */
  cards?: [string, string][]
  /**
   * A bar or line chart drawn from real numbers. Every series carries as many
   * values as there are categories — a short one is not a chart with a gap in
   * it but a chart whose bars stand under the wrong labels, so the writer
   * trims both to the length they agree on before this is stored.
   */
  chart?: {
    kind: 'bar' | 'line'
    unit?: string
    categories: string[]
    series: { name: string; values: number[] }[]
  }
  body?: string
  notes?: string
  accent?: string
  factCheck?: FactCheck
  /**
   * How big this slide's words are, as a multiple of the 서식's own size.
   *
   * One slide, not the deck: the reason to reach for it is a slide with three
   * words on it or one with a paragraph that will not fit, and both are local
   * problems. Absent means the 서식 decides, which is the usual case and the
   * one the checker's limits are written against.
   *
   * `deck_export` multiplies by the same number, so the file matches the
   * screen.
   */
  textScale?: number
  /**
   * Inline formatting authored in the slide editor. Keys identify the visible
   * text slot (`title`, `body`, `bullets.0`, `rows.1.2`, `metrics.0.1`). Values
   * are sanitised inline HTML; the plain-text fields above remain canonical so
   * search, rewriting and older clients continue to work.
   */
  richText?: Record<string, string>
  /**
   * A picture made on the image surface, embedded rather than linked — the
   * `src` is a `data:` URI, which is what makes the deck one file that prints
   * and exports with the picture in it.
   */
  image?: {
    src: string
    caption?: string
    /** `cover` fills the picture box by cropping its edges; absent keeps all of it. */
    fit?: 'contain' | 'cover'
    /** Which side of the content column holds the picture. */
    position?: 'left' | 'right'
    /** Width of a picture that shares the slide with text. */
    size?: 'small' | 'medium' | 'large'
  }
}

export interface DeckArtifact extends ArtifactBase {
  kind: 'deck'
  theme: string
  slides: Slide[]
  /**
   * The deck 서식 this was written under, when one was. The stage draws the
   * slides in the face the 서식 chose (`design.visualStyle`), which can be
   * switched like any other; the id stays so the export builds on the 서식's
   * PowerPoint half.
   */
  templateId?: string
  /** The design system this deck wears, copied on when it was made. */
  design?: DesignTokens | null
  /** Review notes belong to a slide, but not to its visible canvas or PPTX. */
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
  labels?: string
  /** What the model was actually sent — the planned prompt, readable and
   *  editable, sent again as it stands with `raw`. */
  composedPrompt?: string
  /** `matplotlib` when the picture was drawn from code in the sandbox; the
   *  code is then what `composedPrompt` holds. */
  engine?: 'matplotlib'
  seed: number
  model: string
  /** Object-store URL, once an image producer exists. */
  src: string
  /**
   * 도식일 때. The mermaid the picture was drawn from — the artifact proper;
   * the PNG is one rendering of it — and the caption the writer gave it.
   */
  figure?: string
  source?: string
  caption?: string
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
  /**
   * Surface → rendering template: the shape work started here comes out in.
   * A surface with no entry uses the built-in track.
   */
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
  /** `org` puts it in the store for everyone in the workspace to copy. */
  visibility: 'private' | 'org'
  installs: number
  /** The shared skill this one was copied from, if it was copied. */
  originId: string | null
  version: string
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
  /** Stable identity for an agent the workspace ships with. */
  catalogKey: string | null
  /** The shared agent this one was copied from, if it was copied. */
  originId: string | null
  /** Published by an administrator. Meaningful on store rows. */
  official: boolean
  /** You already hold a copy of this shared agent. */
  installed: boolean
  name: string
  slug: string
  description: string
  model: string
  systemPrompt: string
  /** How to use it — shown on the empty screen. */
  guide: string
  /** Conversation starters offered there as buttons. */
  starters: string[]
  /** `sealed`: others may take it, but the prompt stays with its author. */
  shareMode: 'open' | 'sealed'
  /** No prompt here to read or edit — it is read from the original at run time. */
  sealed: boolean
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

/**
 * A skill in the store: somebody else's, and not yet yours.
 *
 * Separate from the skills list because that one is what the composer offers
 * for a turn, and a skill only ever runs out of its owner's account. A shared
 * row mixed into it would be a picker entry that fails at the moment it is
 * used.
 */
export interface StoreSkill extends Skill {
  ownerId: string
  ownerName: string
  /** Published by an administrator — what the workspace ships with. */
  official: boolean
  /** This account already holds a copy, so 가져오기 has nothing to do. */
  installed: boolean
}

export interface ToolCatalogEntry {
  name: string
  label: string
  available: boolean
}
