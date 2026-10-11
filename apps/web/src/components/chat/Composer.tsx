import {
  ArrowUp,
  AtSign,
  Boxes,
  Check,
  ChevronLeft,
  ChevronRight,
  Gauge,
  Globe,
  LayoutGrid,
  Mic,
  Paperclip,
  Pencil,
  Plug,
  Loader2,
  Plus,
  Sparkles,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import type { FileRow, PrivacyDecision, WebSearchSetting } from '@/lib/api'
import { ApiError, transcriptionsApi } from '@/lib/api'
import { startWavRecording, type WavRecorder } from '@/lib/wavRecorder'
import { DesignGalleryModal, offersTemplates } from '@/components/chat/DesignGallery'
import { errorCode, errorMessage, PrivacyDecisionError, templateText } from '@/lib/api'
import { refusalSentence, startFailure } from '@/lib/failures'
import { agentDocumentNames, agentHandoffSurface, handoffSurface } from '@/lib/documentRequest'
import { DICTATION_EVENT, isMac } from '@/lib/shortcuts'
import { currentLang } from '@/lib/i18n'
import { readStoredDraft, writeStoredDraft } from '@/lib/drafts'
import { FINDING_LABEL } from '@/lib/privacy'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useNavigate } from 'react-router-dom'
import { agentStartKind, distinctAgents, kindMeta } from '@/lib/kinds'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, MenuSeparator, Modal } from '@/components/ui'
import { cn } from '@/lib/utils'
import { ChatSendRecoveryError, FIGURE_STYLES, effectiveModelId, useStore } from '@/store/useStore'
import type { ModelInfo, PrivacyAction, SessionKind, Skill } from '@/types'
import { servedAspect, servedAspects } from '@/lib/aspects'
import { ModelPicker } from './ModelPicker'
import { useFileDrop, usePasteFiles } from '@/lib/useFileDrop'
import { useT } from '@/lib/useT'
import { isListed } from '@/lib/listed'
import { isReading, ocrNote, useOcrProgress } from '@/lib/ocr'
import { ATTACH_ACCEPT, attachRefusal } from '@/lib/attachable'

/** Shown when a send timed out at the gateway: the turn may be running on the server. */
const GATEWAY_NOTICE =
  '응답이 늦어 연결이 끊겼습니다. 답이 진행 중이면 이 대화에 이어서 표시됩니다.'

/** The prompt box grows to this height, then scrolls. */
const COMPOSER_MAX_PX = 280

const placeholders: Record<SessionKind, string> = {
  chat: '무엇이든 물어보세요',
  report: '보고서 주제와 넣고 싶은 절을 적으세요',
  slides: '발표 주제와 시간을 적으세요',
  image: '만들고 싶은 이미지를 설명하세요',
  av: '만들고 싶은 영상이나 오디오를 설명하세요',
}

// Mirrors `imagegen.STYLE_CHOICES`.
// 구조도 · 흐름도 · 개념도 draw a figure with the deck export's shapes (`FIGURE_STYLES`), never the image model.
const STYLES = ['자동', '구조도', '흐름도', '개념도', '인포그래픽', '차트', '사진', '일러스트', '미니멀', '3D 렌더', '수채화', '없음']
const LABELS: { id: 'auto' | 'ko' | 'en' | 'none'; label: string }[] = [
  { id: 'auto', label: '자동' },
  { id: 'ko', label: '한국어' },
  { id: 'en', label: '영어' },
  { id: 'none', label: '없음' },
]
const VIDEO_DURATIONS = [4, 6, 8, 10]
const AUDIO_DURATIONS = [15, 30, 60, 120]
// Nothing serves sound effects.
const AUDIO_KINDS = ['narration', 'music'] as const
const AUDIO_KIND_LABEL: Record<(typeof AUDIO_KINDS)[number], string> = {
  narration: '내레이션',
  music: '음악',
}

/** Voices the gateway accepts; anything else falls back to `alloy`. */
const VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as const

type PendingPrivacy = {
  decision: PrivacyDecision
  sessionId: string | null
  text: string
  attachments: FileRow[]
  activatedSkillIds: string[]
  webSearch: WebSearchSetting
  restoreToken: number
}

const SOURCE_LABEL: Record<string, string> = {
  current_input: '현재 요청',
  conversation_history: '대화 기록',
  attachments: '첨부 파일',
  user_instructions: '개인 맞춤 설정',
  project_instructions: '프로젝트 지침',
  project_knowledge: '프로젝트 자료',
  memory: '메모리',
  agent: '에이전트 지침',
  skills: '스킬',
  tool_definitions: '도구 정의',
}

/** Compact chip-style selector used by the image and a/v option bars. */
function OptionGroup<T extends string | number>({
  label,
  value,
  options,
  onChange,
  format,
}: {
  label: string
  value: T
  options: readonly T[]
  onChange: (v: T) => void
  format?: (v: T) => string
}) {
  return (
    <Dropdown
      trigger={({ open }) => (
        <button
          className={cn(
            'flex h-8 items-center gap-1.5 rounded-control border border-line px-2.5 text-sm transition-colors',
            open ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
          )}
        >
          <span className="text-faint">{label}</span>
          <span className="font-medium">{format ? format(value) : value}</span>
        </button>
      )}
    >
      {options.map((o) => (
        <MenuItem key={String(o)} onClick={() => onChange(o)} checked={o === value}>
          {format ? format(o) : String(o)}
        </MenuItem>
      ))}
    </Dropdown>
  )
}

function ImageOptions({ model }: { model: ModelInfo | undefined }) {
  const t = useT()
  const { imageOptions, setImageOptions } = useStore()
  // Only the ratios this model can draw; the stored preference is kept.
  const offered = servedAspects(model)
  return (
    <>
      <OptionGroup
        label={t('비율')}
        value={servedAspect(imageOptions.aspect, model)}
        options={offered}
        onChange={(v) => setImageOptions({ aspect: v })}
      />
      <OptionGroup
        label={t('스타일')}
        value={imageOptions.style}
        options={STYLES}
        onChange={(v) => setImageOptions({ style: v })}
        format={t}
      />
      <OptionGroup
        label={t('글자')}
        value={imageOptions.labels}
        options={LABELS.map((row) => row.id)}
        onChange={(v) => setImageOptions({ labels: v })}
        format={(v) => t(LABELS.find((row) => row.id === v)?.label ?? v)}
      />
      <OptionGroup
        label={t('장수')}
        value={imageOptions.count}
        options={[1, 2, 4]}
        onChange={(v) => setImageOptions({ count: v })}
        format={(v) => t('{n}장').replace('{n}', String(v))}
      />
    </>
  )
}

/** Nearest (resolution, sound) shape this model prices, or null; resolution outranks sound. */
function servedVideoShape(
  rates: Record<string, number>,
  resolution: '720p' | '1080p',
  withAudio: boolean,
): { resolution: '720p' | '1080p'; withAudio: boolean } | null {
  const keys = Object.keys(rates)
  if (keys.length === 0) return null
  const wanted = `${resolution}:${withAudio ? 'sound' : 'silent'}`
  const key =
    keys.find((candidate) => candidate === wanted) ??
    keys.find((candidate) => candidate.startsWith(`${resolution}:`)) ??
    keys.find((candidate) => candidate.endsWith(withAudio ? ':sound' : ':silent')) ??
    [...keys].sort((left, right) => rates[left] - rates[right])[0]
  const [served, sound] = key.split(':')
  return { resolution: served as '720p' | '1080p', withAudio: sound === 'sound' }
}

/** Video and audio option chips; `mode` decides which apply. */
function AvOptions() {
  const t = useT()
  const { avOptions, setAvOptions, models, modelByKind } = useStore()
  const audio = avOptions.mode === 'audio'
  const avModel = models.find((m) => m.id === modelByKind.av)
  const hasVideoModel = models.some((m) => m.kinds.includes('av') && m.modality === 'video')
  const shapedFor = useRef<string | null>(null)
  // When the model changes, snap the chips to a shape it prices; a chip
  // turned afterwards stands.
  useEffect(() => {
    if (audio || !avModel) return
    if (avModel.modality !== 'video') {
      if (hasVideoModel) setAvOptions({ mode: 'video' })
      return
    }
    if (shapedFor.current === avModel.id) return
    shapedFor.current = avModel.id
    const shape = servedVideoShape(
      avModel.creditPerSecond ?? {},
      avOptions.resolution,
      avOptions.withAudio,
    )
    if (!shape) return
    if (shape.resolution === avOptions.resolution && shape.withAudio === avOptions.withAudio) {
      return
    }
    setAvOptions(shape)
  }, [audio, avModel, hasVideoModel, avOptions.resolution, avOptions.withAudio, setAvOptions])
  return (
    <>
      <OptionGroup
        label={t('종류')}
        value={avOptions.mode}
        options={['video', 'audio'] as const}
        onChange={(v) => setAvOptions({ mode: v, durationSec: v === 'audio' ? 30 : 4 })}
        format={(v) => (v === 'audio' ? t('오디오') : t('영상'))}
      />
      {audio ? (
        <>
          <OptionGroup
            label={t('유형')}
            value={avOptions.audioKind}
            options={AUDIO_KINDS}
            onChange={(v) => setAvOptions({ audioKind: v })}
            format={(v) => t(AUDIO_KIND_LABEL[v])}
          />
          {avOptions.audioKind === 'narration' && (
            <OptionGroup
              label={t('목소리')}
              value={avOptions.voice}
              options={VOICES}
              onChange={(v) => setAvOptions({ voice: v })}
            />
          )}
        </>
      ) : (
        <>
          <OptionGroup
            label={t('비율')}
            value={avOptions.aspect}
            options={['16:9', '9:16', '1:1']}
            onChange={(v) => setAvOptions({ aspect: v })}
          />
          <OptionGroup
            label={t('해상도')}
            value={avOptions.resolution}
            options={['720p', '1080p'] as const}
            onChange={(v) => setAvOptions({ resolution: v })}
          />
          <OptionGroup
            label={t('소리')}
            value={avOptions.withAudio ? t('있음') : t('없음')}
            options={[t('없음'), t('있음')]}
            onChange={(v) => setAvOptions({ withAudio: v === t('있음') })}
          />
        </>
      )}
      <OptionGroup
        label={t('길이')}
        value={avOptions.durationSec}
        options={audio ? AUDIO_DURATIONS : VIDEO_DURATIONS}
        onChange={(v) => setAvOptions({ durationSec: v })}
        format={(v) => t('{n}초').replace('{n}', String(v))}
      />
    </>
  )
}

/** The three positions (자동·항상·끔); `auto` is sent as `'auto'`, the others as booleans. */
type WebSearchMode = 'auto' | 'on' | 'off'

/** What the 「+」 menu shows: its list, or one tool's own choices in its place. */
type PlusView = 'main' | 'skills' | 'web' | 'connectors' | 'agents'

/** A row of the 「+」 menu; `submenu` rows open their tool's choices in place. */
function ToolItem({
  icon,
  children,
  hint,
  label,
  submenu,
  back,
  disabled,
  onClick,
}: {
  icon: ReactNode
  children: ReactNode
  hint?: ReactNode
  label?: string
  submenu?: boolean
  /** The 뒤로 row, which focus skips when a submenu opens. */
  back?: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="menuitem"
      aria-label={label}
      aria-haspopup={submenu ? 'menu' : undefined}
      data-back={back ? '' : undefined}
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-control px-2.5 py-1.5 text-left text-base text-fg transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-55 phone:py-2"
    >
      <span className="shrink-0 text-muted">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <span className="max-w-[45%] shrink-0 truncate text-sm text-faint">{hint}</span>}
      {submenu && <ChevronRight size={14} className="shrink-0 text-faint" />}
    </button>
  )
}

// Composer state carried across the remount that creating a session causes.
// Read once by the new composer and cleared.
type ComposerSnapshot = {
  sessionId: string
  value: string
  attachments: FileRow[]
  activatedSkillIds: string[]
  webSearchMode: WebSearchMode
}
let carriedComposer: ComposerSnapshot | null = null
// Editing a past question must not replace the ordinary draft of its conversation.
const editBackups = new Map<string, ComposerSnapshot>()

// Unsent text keyed by session id, or `new:<kind>` on the home screen. Held in
// memory and mirrored to this browser's storage under the account, so a reload
// (or a crash) does not lose a half-typed question; storage may be unavailable
// (private window, blocked site data), and then memory alone serves.
const drafts = new Map<string, string>()
const draftKeyFor = (sessionId: string | null, kind: SessionKind) => sessionId ?? `new:${kind}`
const draftStore = {
  /** Memory first; one storage read per key, misses cached as '' so list renders stay cheap. */
  get(key: string): string | undefined {
    if (drafts.has(key)) return drafts.get(key) || undefined
    const saved = readStoredDraft(useStore.getState().user?.id, key) ?? ''
    drafts.set(key, saved)
    return saved || undefined
  },
  set(key: string, value: string) {
    drafts.set(key, value)
    writeStoredDraft(useStore.getState().user?.id, key, value)
  },
  /** Drops the memory copy; storage is keyed by account, so another account never sees it. */
  clear() {
    drafts.clear()
  },
}

// These module caches survive remounts, but must never survive an account change.
useStore.subscribe((state, previous) => {
  if (state.accountEpoch === previous.accountEpoch && state.user?.id === previous.user?.id) return
  carriedComposer = null
  editBackups.clear()
  draftStore.clear()
})

const FILL_EVENT = 'kloudchat:fill-composer'
const ATTACH_EVENT = 'kloudchat:attach-to-session'

/**
 * A file whose upload finished after its composer moved to another conversation: it joins the
 * state carried into that conversation's composer, or that composer once it is on screen.
 */
function attachLate(sessionId: string, row: FileRow) {
  if (carriedComposer?.sessionId === sessionId) {
    carriedComposer = { ...carriedComposer, attachments: [...carriedComposer.attachments, row] }
    return
  }
  window.dispatchEvent(new CustomEvent(ATTACH_EVENT, { detail: { sessionId, row } }))
}

/** Puts `text` in the composer in place of what is there, without sending it. */
export function fillComposer(text: string) {
  window.dispatchEvent(new CustomEvent(FILL_EVENT, { detail: text }))
}

/** Whether a session has unsent text. */
export function hasUnsentDraft(sessionId: string) {
  return !!draftStore.get(sessionId)?.trim()
}

export function Composer({
  sessionId,
  kind,
  projectId,
  autoFocus,
}: {
  sessionId: string | null
  kind: SessionKind
  projectId?: string | null
  autoFocus?: boolean
}) {
  const t = useT()
  const isMedia = kind === 'image' || kind === 'av'
  const canWebSearch = kind === 'chat' || kind === 'report' || kind === 'slides'
  const messageEdit = useStore((s) => s.messageEdit)
  const accountEpoch = useStore((s) => s.accountEpoch)
  const clearMessageEdit = useStore((s) => s.clearMessageEdit)
  const setMessageEditBusy = useStore((s) => s.setMessageEditBusy)
  const forkBeforeMessage = useStore((s) => s.forkBeforeMessage)
  const editing = kind === 'chat' && messageEdit?.sessionId === sessionId ? messageEdit : null
  const editContext = useRef<{
    sessionId: string; messageId: string; sourceAttachmentIds: string[]
    forkId?: string; files?: FileRow[]; attachmentIdMap?: Record<string, string>
  } | null>(null)
  const editSending = useRef(false)
  const composing = useRef(false)
  const [editPending, setEditPending] = useState(false)
  const draftKey = draftKeyFor(sessionId, kind)
  const [value, setValue] = useState(() => draftStore.get(draftKey) ?? '')
  const liveValue = useRef(value)
  liveValue.current = value
  const draftKeyRef = useRef(draftKey)
  useEffect(() => {
    if (draftKeyRef.current !== draftKey) {
      // The session changed under a mounted composer; load its own draft.
      draftKeyRef.current = draftKey
      const own = draftStore.get(draftKey) ?? ''
      liveValue.current = own
      setValue(own)
      return
    }
    if (!editing && editContext.current?.sessionId !== draftKey) draftStore.set(draftKey, value)
  }, [draftKey, value, editing])
  const restoreSequence = useRef(0)
  const activeRestoreToken = useRef<number | null>(null)

  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  // A gallery sentence is appended to the box, never written over it, and
  // arrives selected so one keystroke removes it.
  // Selection applied after React commits the value.
  const pendingSelection = useRef<[number, number] | null>(null)
  useEffect(() => {
    if (!draft) return
    activeRestoreToken.current = null
    const kept = liveValue.current.replace(/\s*$/, '')
    const next = kept ? `${kept}\n\n${draft}` : draft
    liveValue.current = next
    setValue(next)
    setDraft('')
    ref.current?.focus()
    // Into an empty box the caret goes to the end. Applied in the layout
    // effect: the controlled textarea's next commit collapses an earlier selection.
    pendingSelection.current = [kept ? next.length - draft.length : next.length, next.length]
  }, [draft, setDraft])
  useEffect(() => {
    const fill = (event: Event) => {
      const text = (event as CustomEvent<string>).detail
      if (typeof text !== 'string' || editing) return
      activeRestoreToken.current = null
      liveValue.current = text
      draftStore.set(draftKey, text)
      setValue(text)
      ref.current?.focus()
      pendingSelection.current = [text.length, text.length]
    }
    window.addEventListener(FILL_EVENT, fill)
    return () => window.removeEventListener(FILL_EVENT, fill)
  }, [draftKey, editing])
  useLayoutEffect(() => {
    const range = pendingSelection.current
    if (!range) return
    pendingSelection.current = null
    ref.current?.setSelectionRange(range[0], range[1])
  }, [value])
  /** Uploaded files; the turn sends their ids. */
  const [attachments, setAttachments] = useState<FileRow[]>([])
  const liveAttachments = useRef(attachments)
  const liveSessionId = useRef(sessionId)
  liveSessionId.current = sessionId
  const composerMounted = useRef(true)
  useEffect(() => {
    composerMounted.current = true
    return () => { composerMounted.current = false }
  }, [])
  useEffect(() => {
    const late = (event: Event) => {
      const { sessionId: target, row } = (event as CustomEvent<{ sessionId: string; row: FileRow }>).detail
      if (target !== liveSessionId.current) return
      setAttachments((current) => {
        if (current.some((file) => file.id === row.id)) return current
        const next = [...current, row]
        liveAttachments.current = next
        return next
      })
    }
    window.addEventListener(ATTACH_EVENT, late)
    return () => window.removeEventListener(ATTACH_EVENT, late)
  }, [])
  /** The conversation an agent pick is opening; files picked meanwhile wait for it. */
  const agentStart = useRef<Promise<string> | null>(null)
  // A scanned attachment is read in the background; its chip follows the pages.
  useOcrProgress(attachments, (row) =>
    setAttachments((current) => {
      const next = current.map((item) => (item.id === row.id ? row : item))
      liveAttachments.current = next
      return next
    }),
  )
  liveAttachments.current = attachments
  const [pendingPrivacy, setPendingPrivacy] = useState<PendingPrivacy | null>(null)
  const [reusableSessionId, setReusableSessionId] = useState<string | null>(null)
  const [privacyRetrying, setPrivacyRetrying] = useState(false)
  const [modelSelectionPending, setModelSelectionPending] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  // A form a picked template brought with it; taken once and cleared.
  const pendingAttachment = useStore((s) => s.pendingAttachment)
  const pendingTemplate = useStore((s) => s.pendingTemplate)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const livePendingTemplate = useRef(pendingTemplate)
  livePendingTemplate.current = pendingTemplate
  const designTemplates = useStore((s) => s.designTemplates)
  const [galleryOpen, setGalleryOpen] = useState(false)
  // A phone opens 「+」 as a bottom sheet.
  const phone = useMediaQuery('(pointer: coarse) and (max-width: 64rem), (max-width: 40rem)')
  const [plusOpen, setPlusOpen] = useState(false)
  const [plusView, setPlusView] = useState<PlusView>('main')
  const [skillQuery, setSkillQuery] = useState('')
  const plusViewRef = useRef<HTMLDivElement>(null)
  // A submenu replaces the list it was opened from; focus moves into it.
  useEffect(() => {
    if (!plusOpen) return
    const frame = requestAnimationFrame(() => {
      const root = plusViewRef.current
      const target = plusView === 'skills'
        ? root?.querySelector<HTMLElement>('input')
        : root?.querySelector<HTMLElement>('button:not(:disabled):not([data-back])')
      target?.focus()
    })
    return () => cancelAnimationFrame(frame)
    // Only a change of view moves focus; opening is the menu's own business.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plusView])
  const setSessionTemplate = useStore((s) => s.setSessionTemplate)
  const handOff = useStore((s) => s.handOff)
  const setPendingAttachment = useStore((s) => s.setPendingAttachment)
  const composerRestore = useStore((s) => s.composerRestore)
  const setComposerRestore = useStore((s) => s.setComposerRestore)
  useEffect(() => {
    if (!pendingAttachment) return
    // Media surfaces take no attachments; drop it rather than leave it for the next chat turn.
    if (isMedia) {
      setPendingAttachment(null)
      return
    }
    activeRestoreToken.current = null
    setAttachments((current) => {
      const next = current.some((f) => f.id === pendingAttachment.id)
        ? current
        : [...current, pendingAttachment]
      liveAttachments.current = next
      return next
    })
    setPendingAttachment(null)
  }, [isMedia, pendingAttachment, setPendingAttachment])
  /** Refused-turn restoration without overwriting newer input. */
  useEffect(() => {
    if (!composerRestore || composerRestore.sessionId !== sessionId) return
    setComposerRestore(null)
    if (composerRestore.error) setChatError(composerRestore.error)
    // Skills are not "newer input": they stay on after a send, so their presence
    // says nothing about whether the person has started typing something else.
    if (liveValue.current || liveAttachments.current.length > 0) {
      return
    }
    activeRestoreToken.current = null
    // Skills picked while the request was out are kept alongside the restored ones.
    const skills = [
      ...liveActivatedSkillIds.current,
      ...composerRestore.activatedSkillIds.filter((id) => !liveActivatedSkillIds.current.includes(id)),
    ]
    liveValue.current = composerRestore.value
    liveAttachments.current = composerRestore.attachments
    liveActivatedSkillIds.current = skills
    setValue(composerRestore.value)
    setAttachments(composerRestore.attachments)
    setActivatedSkillIds(skills)
    requestAnimationFrame(() => ref.current?.focus())
  }, [composerRestore, sessionId, setComposerRestore])

  const [uploading, setUploading] = useState(false)
  /** Names of files whose upload failed, shown until the next upload attempt. */
  const [uploadFailed, setUploadFailed] = useState<string[]>([])
  const fileInput = useRef<HTMLInputElement>(null)
  // Dictation goes to the deployment's own Whisper, never to the browser
  // vendor's recognizer; the recording is not kept.
  const dictationEnabled = useStore((s) => s.dictationEnabled)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const recorder = useRef<WavRecorder | null>(null)
  const canRecord =
    dictationEnabled && typeof AudioContext !== 'undefined' && Boolean(navigator.mediaDevices)
  const stopRecording = async (): Promise<string> => {
    const active = recorder.current
    if (!active) return ''
    recorder.current = null
    setRecording(false)
    setTranscribing(true)
    try {
      const blob = await active.stop()
      // 16 kHz 16-bit mono = 32,000 bytes/s; under 0.3 s is a key tap, not speech.
      if (blob.size <= 44 + 32_000 * 0.3) return ''
      // The last answer primes Whisper's vocabulary; no language is pinned.
      const lastAnswer = [...(session?.messages ?? [])]
        .reverse()
        .find((m) => m.role === 'assistant' && m.content?.trim())
      const { text } = await transcriptionsApi.transcribe(blob, 'speech.wav', {
        prompt: lastAnswer?.content.replace(/\s+/g, ' ').slice(0, 300),
      })
      if (text) {
        setValue((v) => (v.trim() ? `${v.replace(/\s+$/, '')} ${text}` : text))
        requestAnimationFrame(() => ref.current?.focus())
      }
      return text ?? ''
    } catch (err) {
      setChatError(errorMessage(err, t('받아쓰지 못했습니다.')))
      return ''
    } finally {
      setTranscribing(false)
    }
  }
  const startRecording = async () => {
    setChatError(null)
    try {
      recorder.current = await startWavRecording()
      setRecording(true)
    } catch {
      setChatError(t('마이크를 쓸 수 없습니다. 브라우저의 마이크 권한을 확인해 주세요.'))
    }
  }
  // Push to talk, when switched on in 환경설정: hold space in the empty box, release to have
  // the words put in the box (never sent). Off, space is just a space.
  // `pushToTalk` marks a held-key recording.
  const spaceToTalk = useStore((s) => s.user?.preferences.spaceToTalk === true)
  const pushToTalk = useRef(false)
  const holdToTalk = (e: { key: string; repeat: boolean; preventDefault: () => void }) => {
    if (e.key !== ' ' || !spaceToTalk || !canRecord || busy || transcribing) return false
    if (liveValue.current.trim()) return false
    e.preventDefault()
    if (e.repeat || recorder.current) return true
    pushToTalk.current = true
    void startRecording()
    return true
  }
  const releaseToTalk = (e: { key: string; preventDefault: () => void }) => {
    if (e.key !== ' ' || !pushToTalk.current) return false
    e.preventDefault()
    pushToTalk.current = false
    // Transcribed into the box; the person reads it and sends it.
    void stopRecording()
    return true
  }
  // Cmd/Ctrl+Shift+M toggles the mic; unlike push-to-talk it waits for send.
  useEffect(() => {
    if (!canRecord) return
    const toggle = () => {
      if (transcribing) return
      pushToTalk.current = false
      void (recorder.current ? stopRecording() : startRecording())
    }
    window.addEventListener(DICTATION_EVENT, toggle)
    return () => window.removeEventListener(DICTATION_EVENT, toggle)
  })
  // 「자동」 by default: the web tools are offered and the server searches when the words
  // call for it; 「켬」 searches every turn, 「끔」 offers nothing. `searchBlocked` still
  // overrides per turn.
  const [webSearchMode, setWebSearchMode] = useState<WebSearchMode>('auto')
  const webSearch = webSearchMode !== 'off'
  const webSearchModeLabels: Record<WebSearchMode, string> = { auto: t('자동'), on: t('항상'), off: t('끔') }
  const webSearchModeLabel = webSearchModeLabels[webSearchMode]
  const [activatedSkillIds, setActivatedSkillIds] = useState<string[]>([])
  const liveActivatedSkillIds = useRef(activatedSkillIds)
  liveActivatedSkillIds.current = activatedSkillIds
  const liveWebSearchMode = useRef(webSearchMode)
  liveWebSearchMode.current = webSearchMode
  // Built from live refs: `onSession` callbacks fire after the arming render.
  const heldComposer = (id: string) => ({
    sessionId: id,
    value: liveValue.current,
    attachments: liveAttachments.current,
    activatedSkillIds: liveActivatedSkillIds.current,
    webSearchMode: liveWebSearchMode.current,
  })
  const restoreComposer = (held: ComposerSnapshot) => {
    liveValue.current = held.value
    liveAttachments.current = held.attachments
    liveActivatedSkillIds.current = held.activatedSkillIds
    setValue(held.value)
    setAttachments(held.attachments)
    setActivatedSkillIds(held.activatedSkillIds)
    setWebSearchMode(held.webSearchMode)
    draftStore.set(held.sessionId, held.value)
  }
  // Per-turn state resets when the surface or session changes; the typed sentence stays.
  const initializedScope = useRef<{ sessionId: string | null; kind: SessionKind } | null>(null)
  const initializedAccount = useRef(accountEpoch)
  useEffect(() => {
    if (initializedAccount.current !== accountEpoch) {
      initializedAccount.current = accountEpoch
      editContext.current = null
      editSending.current = false
      setEditPending(false)
      setPendingPrivacy(null)
      setReusableSessionId(null)
      activeRestoreToken.current = null
      liveValue.current = ''
      setValue('')
      initializedScope.current = null
    }
    // StrictMode replays setup; a consumed handoff must not become a reset.
    const previous = initializedScope.current
    if (previous?.sessionId === sessionId && previous.kind === kind) return
    initializedScope.current = { sessionId, kind }
    const backup = sessionId ? editBackups.get(sessionId) : undefined
    if (backup && messageEdit?.sessionId !== sessionId) {
      editBackups.delete(sessionId!)
      restoreComposer(backup)
      return
    }
    if (sessionId && carriedComposer?.sessionId === sessionId) {
      // Only non-empty fields are put back: on a send the composer was cleared
      // before the session existed, and a refusal may already have restored
      // the real draft through the store.
      const held = carriedComposer
      carriedComposer = null
      if (held.value) {
        liveValue.current = held.value
        setValue(held.value)
      }
      if (held.attachments.length) {
        liveAttachments.current = held.attachments
        setAttachments(held.attachments)
      }
      if (held.activatedSkillIds.length) {
        liveActivatedSkillIds.current = held.activatedSkillIds
        setActivatedSkillIds(held.activatedSkillIds)
      }
      setWebSearchMode(held.webSearchMode)
      return
    }
    // The conversation's standing skills come back with it; a new one starts clean.
    // Read once here, not subscribed: a later change to them is hydrated below.
    const standing = sessionId
      ? useStore.getState().sessions.find((c) => c.id === sessionId)?.skillIds ?? []
      : []
    liveActivatedSkillIds.current = standing
    setActivatedSkillIds(standing)
    liveAttachments.current = []
    setAttachments([])
    setWebSearchMode('auto')
  }, [sessionId, kind, messageEdit?.sessionId, accountEpoch])
  // The session row may arrive after the composer mounted (a fresh load of /s/:id):
  // its standing skills fill an empty picker once, never overriding a live choice.
  const standingSkillIds = useStore((s) =>
    sessionId ? s.sessions.find((c) => c.id === sessionId)?.skillIds : undefined,
  )
  useEffect(() => {
    if (!standingSkillIds?.length || liveActivatedSkillIds.current.length || editing) return
    liveActivatedSkillIds.current = standingSkillIds
    setActivatedSkillIds(standingSkillIds)
  }, [standingSkillIds, editing])
  const ref = useRef<HTMLTextAreaElement>(null)
  const navigate = useNavigate()
  const {
    send,
    stopStreaming,
    running,
    setNotice,
    skills,
    availableTools,
    sessions,
    projects,
    agents,
    connectors,
    toggleConnector,
    models,
    modelByKind,
    imageOptions,
    jobs,
    uploadFile,
    newSession,
    setSessionSkills,
    setSessionRoutingMode,
    generateImages,
    generateAudio,
    generateVideo,
    avOptions,
    mediaError,
    clearMediaError,
    user,
  } = useStore()

  const project = projects.find((p) => p.id === projectId)
  const effectiveSessionId = sessionId ?? reusableSessionId
  const session = sessions.find((candidate) => candidate.id === effectiveSessionId)
  // A generation awaiting an answer or approval (document surfaces only).
  const pending = session?.pending ?? null
  const sessionAgent = agents.find((agent) => agent.id === session?.agentId)
  // The picked template, else the one the session already wears. Derived:
  // the server is what makes the choice sticky.
  const shownTemplate =
    (pendingTemplate?.surface === kind ? pendingTemplate : null) ??
    designTemplates.find((row) => row.id === session?.renderTemplateId) ??
    null
  // Same gate as the gallery button.
  const hasTemplates =
    offersTemplates(kind) && designTemplates.some((row) => row.surface === kind)
  // The empty screen, with its own gallery button, is gone.
  const started = (session?.messages.length ?? 0) > 0
  const model = models.find(
    (candidate) => candidate.id === effectiveModelId(session, kind, agents, modelByKind),
  )
  // A strict-local model gets no network tool; the control follows the turn, not the stored preference.
  const searchBlocked = Boolean(model?.strictLocal)
  const effectiveWebSearch = webSearch && !searchBlocked
  // What this turn sends: the mode, unless something blocks search altogether.
  const sentWebSearch: WebSearchSetting = !effectiveWebSearch
    ? false
    : webSearchMode === 'auto'
      ? 'auto'
      : true
  // 자동 is the default and needs no chip; 항상 and 끔 do, and so does a model that blocks search.
  const webSearchChip = canWebSearch && (searchBlocked ? webSearch : webSearchMode !== 'auto')
  const agentSkillAllowlist = sessionAgent?.skillIds
  const agentToolAllowlist = sessionAgent?.tools
  const recommended = new Set(project?.skillIds ?? [])
  const usableSkills = skills
    .filter(
      (skill) =>
        skill.enabled &&
        // A hidden part (`listed: false`) is not offered; one already on the turn stays.
        (isListed(skill) || activatedSkillIds.includes(skill.id)) &&
        (skill.kinds.length === 0 || skill.kinds.includes(kind)) &&
        (agentSkillAllowlist === undefined ||
          agentSkillAllowlist === null ||
          agentSkillAllowlist.includes(skill.id)),
    )
    .sort((left, right) => Number(recommended.has(right.id)) - Number(recommended.has(left.id)))

  const skillUnavailableReason = (skill: Skill): string | null => {
    if (skill.requiredTools.length === 0) return null
    if (kind !== 'chat') {
      return t('이 화면에서는 도구가 필요한 스킬을 실행할 수 없습니다.')
    }
    if (!model?.supportsTools) {
      return t('선택한 모델이 도구 호출을 지원하지 않습니다.')
    }
    if (agentToolAllowlist !== undefined && agentToolAllowlist !== null) {
      const denied = skill.requiredTools.filter((name) => !agentToolAllowlist.includes(name))
      if (denied.length > 0) {
        return t('에이전트가 필수 도구를 허용하지 않습니다: {tools}').replace(
          '{tools}',
          denied.join(', '),
        )
      }
    }
    if (skill.requiredTools.includes('web_search') && !effectiveWebSearch) {
      return searchBlocked
        ? t('strict-local 모델은 웹 검색 도구를 쓸 수 없습니다.')
        : t('먼저 웹 검색을 켜야 합니다.')
    }
    const unavailable = skill.requiredTools.filter((name) => {
      if (name === 'search_knowledge') return !sessionAgent?.hasKnowledge
      return !availableTools.some((tool) => tool.name === name && tool.available)
    })
    return unavailable.length > 0
      ? t('필수 도구를 사용할 수 없습니다: {tools}').replace(
          '{tools}',
          unavailable.join(', '),
        )
      : null
  }
  const activeSkills = activatedSkillIds
    .map((id) => usableSkills.find((skill) => skill.id === id))
    .filter(
      (skill): skill is Skill =>
        skill !== undefined && skillUnavailableReason(skill) === null,
    )
    .slice(0, 3)
  const autoActive =
    kind === 'chat' && (session?.routingMode === 'auto' || session?.routingMode === 'auto_quality')
  const autoBypassPreview =
    autoActive &&
    Boolean(
      project ||
        sessionAgent ||
        attachments.length > 0 ||
        (effectiveWebSearch && webSearchMode === 'on') ||
        activeSkills.length > 0,
    )
  // Empty `kinds` means every surface.
  // A person's own copy and the catalogue original it came from show once (of those switched on).
  const pickableAgents = distinctAgents(agents.filter((a) => a.enabled), user?.id)
  const usableAgents = pickableAgents.filter(
    (a) => a.enabled && (a.kinds.length === 0 || a.kinds.includes(kind)),
  )
  // 「+」 › 에이전트 lists every agent the person can start: those of this screen first, then
  // the rest, each opening on its own screen.
  // A picture or sound screen keeps to its own.
  const menuAgents = isMedia
    ? usableAgents
    : [...usableAgents, ...pickableAgents.filter((a) => a.enabled && !usableAgents.includes(a))]
  const usableConnectors = connectors.filter(
    (c) => c.installed && (c.kinds.length === 0 || c.kinds.includes(kind)),
  )
  const activeConnectors = usableConnectors.filter((c) => c.enabled && c.status === 'connected')
  // Per picture, per second by (resolution, sound), or per call; never
  // `creditCost`, which is per 1k output tokens.
  const videoRate =
    model?.creditPerSecond?.[`${avOptions.resolution}:${avOptions.withAudio ? 'sound' : 'silent'}`]
  // The submit endpoint refuses shapes the model does not price (Sora has no silent price).
  const unsupportedVideo =
    kind === 'av' && avOptions.mode === 'video' && videoRate === undefined
  const estimate = isMedia
    ? kind === 'image'
      ? (model?.creditPerImage ?? 0) * imageOptions.count
      : avOptions.mode === 'video'
        ? (videoRate ?? 0) * avOptions.durationSec
        : (model?.creditPerCall ?? model?.creditCost ?? 0)
    : 0
  const jobRunning = jobs.some(
    (j) => j.sessionId === sessionId && (j.status === 'running' || j.status === 'queued'),
  )
  // This session's own turn only.
  const streaming = !!sessionId && !!running[sessionId]
  // The gateway notice stands only until the turn it was about shows up: a running
  // turn, or an answer growing in this conversation, means the request went through.
  const lastAnswerLength = useStore((s) => {
    const last = s.sessions.find((c) => c.id === sessionId)?.messages.at(-1)
    return last?.role === 'assistant' ? last.content.length + (last.steps?.length ?? 0) : 0
  })
  useEffect(() => {
    if (chatError === t(GATEWAY_NOTICE) && (streaming || lastAnswerLength > 0)) setChatError(null)
  }, [chatError, streaming, lastAnswerLength, t])
  const busy = isMedia ? jobRunning : streaming
  // Before the model list has answered, a send would go out on a guessed model; it waits.
  const modelsSettled = useStore((s) => s.modelsSettled)
  const modelCount = useStore((s) => s.models.length)
  const awaitingModels = !modelsSettled && modelCount === 0
  const readingAttachment = attachments.find(isReading)
  /** Enter pressed while a file uploads or the models load: sent as soon as that ends. */
  const [queuedSend, setQueuedSend] = useState(false)
  /** A new conversation's first turn: the text stays in the box until the server takes it. */
  const [startPending, setStartPending] = useState(false)
  const holdingStart = useRef(false)
  /** The server took the first turn: the box empties before its state is carried over. */
  const releaseStart = () => {
    if (!holdingStart.current) return
    holdingStart.current = false
    liveValue.current = ''
    liveAttachments.current = []
    draftStore.set(draftKeyFor(null, kind), '')
    setValue('')
    setAttachments([])
  }

  useEffect(() => {
    if (!editing) {
      if (messageEdit && messageEdit.sessionId !== sessionId) clearMessageEdit()
      if (editContext.current?.sessionId !== sessionId) editContext.current = null
      return
    }
    if (editContext.current?.sessionId === sessionId && editContext.current.messageId === editing.messageId) return
    if (busy || uploading || pendingPrivacy || privacyRetrying || recording || transcribing || modelSelectionPending || editSending.current) {
      clearMessageEdit()
      return
    }
    const target = session?.messages.find((message) => message.id === editing.messageId && message.role === 'user')
    if (!target || !sessionId) {
      clearMessageEdit()
      return
    }
    if (!editBackups.has(sessionId)) editBackups.set(sessionId, heldComposer(sessionId))
    editContext.current = { sessionId, messageId: target.id,
      sourceAttachmentIds: (target.attachments ?? []).flatMap((file) => file.id ? [file.id] : []) }
    activeRestoreToken.current = null
    setReusableSessionId(null)
    setChatError(null)
    liveValue.current = target.content
    setValue(target.content)
    const files: FileRow[] = (target.attachments ?? []).filter((file) => !!file.id).map((file) => ({
      id: file.id!, name: file.name, size: typeof file.size === 'number' ? file.size : 0,
      mime: file.type || 'application/octet-stream', tokens: 0, projectId: projectId ?? null,
      sessionId, preview: '', error: null, createdAt: target.createdAt,
    }))
    liveAttachments.current = files
    setAttachments(files)
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.scrollIntoView({ block: 'nearest' }) })
    // A message edit is an explicit one-time injection, not an ordinary draft update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messageEdit, sessionId])

  const cancelEditing = () => {
    if (editSending.current || !editContext.current) return
    const original = editBackups.get(editContext.current.sessionId)
    editBackups.delete(editContext.current.sessionId)
    editContext.current = null
    clearMessageEdit()
    setPendingPrivacy(null)
    setReusableSessionId(null)
    setChatError(null)
    activeRestoreToken.current = null
    if (original) restoreComposer(original)
    ref.current?.focus()
  }

  const deliverChat = async (
    targetSessionId: string | null,
    text: string,
    files: FileRow[],
    search: WebSearchSetting,
    skillIds: string[],
    action?: PrivacyAction,
    decisionToken?: string,
    restoreToken?: number,
  ) => {
    setChatError(null)
    const resolvedSessionId = targetSessionId ?? reusableSessionId
    const editScope = editContext.current?.forkId === resolvedSessionId ? editContext.current : null
    const origin = useStore.getState().activeSessionId
    const originPath = window.location.pathname
    const owner = useStore.getState().user?.id
    const epoch = useStore.getState().accountEpoch
    let acceptedHere = false
    const ownsView = () => {
      const state = useStore.getState()
      if (state.user?.id !== owner || state.accountEpoch !== epoch) return false
      if (!editScope) return true
      // Router effects may lag behind an already committed browser navigation.
      if (window.location.pathname !== originPath &&
        !(acceptedHere && window.location.pathname === `/s/${resolvedSessionId}`)) return false
      return (state.activeSessionId === origin && editContext.current === editScope) ||
        (acceptedHere && !editContext.current &&
          (state.activeSessionId === resolvedSessionId || state.activeSessionId === origin))
    }
    let attemptedSessionId = resolvedSessionId
    try {
      const acceptedSessionId = await send(resolvedSessionId, 'chat', text, {
        projectId,
        webSearch: search,
        attachments: files.map((file) => file.id),
        attachmentNames: files.map((file) => file.name),
        activatedSkillIds: skillIds,
        privacyAction: action,
        privacyDecisionToken: decisionToken,
        reconcileBeforeRetry: Boolean(editScope),
        onSession: (id) => {
          if (!ownsView()) return
          acceptedHere = true
          attemptedSessionId = id
          releaseStart()
          carriedComposer = heldComposer(id)
          const branched = editContext.current?.forkId === id
          if (branched) {
            editContext.current = null
            clearMessageEdit()
          }
          // A branch from an edit is pushed, so Back returns to the conversation it
          // came from; a brand-new conversation replaces its /new/:kind address.
          navigate(`/s/${id}`, branched ? undefined : { replace: true })
        },
      })
      if (!ownsView()) return
      if (!sessionId) navigate(`/s/${acceptedSessionId}`, { replace: true })
      activeRestoreToken.current = null
      setReusableSessionId(null)
      setPendingPrivacy(null)
    } catch (error) {
      if (!ownsView()) return
      if (error instanceof ChatSendRecoveryError) {
        setNotice(error.recovery === 'stored'
          ? t('새 대화에 전송 기록이 있습니다. 저장된 답변을 확인한 뒤 다시 시도하세요.')
          : t('전송 상태를 확인하지 못했습니다. 새 대화를 새로고침해 확인한 뒤 다시 시도하세요.'))
        return
      }
      if (error instanceof PrivacyDecisionError) {
        // The decision dialog holds the turn now; the box is free again.
        releaseStart()
        const decisionSessionId = error.sessionId ?? resolvedSessionId
        setReusableSessionId(decisionSessionId)
        setPendingPrivacy({
          decision: error.decision,
          sessionId: decisionSessionId,
          text,
          attachments: files,
          activatedSkillIds: skillIds,
          webSearch: search,
          restoreToken: restoreToken ?? ++restoreSequence.current,
        })
        return
      }
      setReusableSessionId((current) => current ?? attemptedSessionId)
      if (editScope) throw error
      // A gateway timeout is not a refusal: the request may have reached the server and
      // its turn may be running. The draft is not handed back (sending it again would
      // ask twice); the conversation is reloaded so a running turn shows.
      if (error instanceof ApiError && [502, 503, 504].includes(error.status) && attemptedSessionId) {
        setComposerRestore({
          sessionId: attemptedSessionId,
          value: '',
          attachments: [],
          activatedSkillIds: skillIds,
          error: t(GATEWAY_NOTICE),
        })
        void useStore.getState().openSession(attemptedSessionId)
        return
      }
      // Branch on the code: `errorMessage` swallows machine strings.
      const notice =
        errorCode(error) === 'auto_quality_model_required'
          ? t('Auto에 사용할 품질 모델을 다시 선택하세요. 초안과 첨부 파일은 그대로 보관했습니다.')
          : (refusalSentence(errorCode(error), t) ??
            errorMessage(error, t('요청을 전송하지 못했습니다. 잠시 후 다시 시도하세요.')))
      // Through the store: the composer that sent a session-creating turn is
      // unmounted by the time a refusal lands.
      setComposerRestore({
        sessionId: attemptedSessionId,
        value: text,
        attachments: files,
        activatedSkillIds: skillIds,
        error: notice,
      })
      throw error
    }
  }

  const dismissPrivacyDecision = () => {
    if (!pendingPrivacy || privacyRetrying) return
    // Only the submission that still owns the cleared composer may restore it;
    // a newer edit or upload revokes that.
    if (activeRestoreToken.current === pendingPrivacy.restoreToken) {
      activeRestoreToken.current = null
      liveValue.current = pendingPrivacy.text
      liveAttachments.current = pendingPrivacy.attachments
      liveActivatedSkillIds.current = pendingPrivacy.activatedSkillIds
      setValue(pendingPrivacy.text)
      setAttachments(pendingPrivacy.attachments)
      setActivatedSkillIds(pendingPrivacy.activatedSkillIds)
    }
    setReusableSessionId(pendingPrivacy.sessionId)
    setPendingPrivacy(null)
  }

  // Grows with its text up to the cap; a scrollbar only once the text is past it. Measured again
  // when the box changes width (panel, rotation) or the web font arrives, which re-wrap the text.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let width = el.clientWidth
    const fit = () => {
      el.style.height = 'auto'
      const full = el.scrollHeight
      el.style.height = `${Math.min(full, COMPOSER_MAX_PX)}px`
      el.style.overflowY = full > COMPOSER_MAX_PX ? 'auto' : 'hidden'
    }
    fit()
    const resized = new ResizeObserver(() => {
      if (el.clientWidth === width) return
      width = el.clientWidth
      fit()
    })
    resized.observe(el)
    let live = true
    void document.fonts?.ready.then(() => { if (live) fit() })
    return () => {
      live = false
      resized.disconnect()
    }
  }, [value])

  /** Shared picker, drop, and paste upload path. */
  const addFiles = async (offered: File[]) => {
    if (!offered.length || isMedia || editPending) return
    const editScope = editContext.current
    // Refused before uploading: an empty file or a program has nothing to read.
    const refused = offered.flatMap((file) => {
      const reason = attachRefusal(file, t)
      return reason ? [`${file.name} — ${reason}`] : []
    })
    const picked = offered.filter((file) => !attachRefusal(file, t))
    setUploadFailed(refused)
    if (!picked.length) return
    setUploading(true)
    try {
      // A file picked while an agent's conversation is being opened belongs to that conversation.
      const starting = agentStart.current
      const uploadSession = starting ? (await starting.catch(() => null)) ?? sessionId : sessionId
      for (const file of picked) {
        const row = await uploadFile(file, {
          projectId: editing ? undefined : projectId ?? undefined,
          sessionId: editing ? undefined : uploadSession ?? undefined,
        }).catch(() => {
          // Said where the chips are: a file that never arrived must not look attached.
          setUploadFailed((current) => [...current, file.name])
          return null
        })
        if (!row || editContext.current !== editScope) continue
        if (editScope && useStore.getState().activeSessionId !== uploadSession) continue
        if (uploadSession && (!composerMounted.current || liveSessionId.current !== uploadSession)) {
          // The composer moved on to the new conversation (or was replaced by its own);
          // the file follows the conversation it was uploaded for.
          attachLate(uploadSession, row)
          continue
        }
        if (!composerMounted.current) continue
        setAttachments((current) => {
          activeRestoreToken.current = null
          const next = [...current, row]
          liveAttachments.current = next
          return next
        })
      }
    } finally {
      if (composerMounted.current) setUploading(false)
    }
  }

  // Media surfaces take no attachments.
  const { over: dragging, handlers: dropHandlers } = useFileDrop(
    (files) => void addFiles(files),
    !isMedia && !editPending,
  )
  const onPasteFiles = usePasteFiles((files) => void addFiles(files))

  const submit = (spoken?: string) => {
    const text = (spoken ?? value).trim()
    if (text && !spoken && !editing && !busy && (uploading || awaitingModels)) {
      setQueuedSend(true)
      return
    }
    setQueuedSend(false)
    if (startPending || !text || busy || composing.current || modelSelectionPending || unsupportedVideo || uploading || editSending.current || pendingPrivacy) return
    if (editing && editContext.current) {
      if (sessionId && useStore.getState().running[sessionId]) return
      const context = editContext.current
      const origin = sessionId
      const originPath = window.location.pathname
      const owner = useStore.getState().user?.id
      const epoch = useStore.getState().accountEpoch
      const ownsAccount = () => useStore.getState().user?.id === owner && useStore.getState().accountEpoch === epoch
      const ownsEdit = () => ownsAccount() && editContext.current === context &&
        useStore.getState().activeSessionId === origin && window.location.pathname === originPath
      const search = sentWebSearch
      const skillIds = activeSkills.map((skill) => skill.id)
      const selectedFiles = attachments
      editSending.current = true
      setEditPending(true)
      setMessageEditBusy(true)
      setChatError(null)
      void (async () => {
        try {
          if (!context.forkId) {
            const fork = await forkBeforeMessage(context.sessionId, context.messageId)
            context.forkId = fork.sessionId
            context.files = fork.attachments
            context.attachmentIdMap = fork.attachmentIdMap
          }
          // Navigating away while the fork request was pending must not send from another view.
          if (!ownsEdit()) return
          const files = selectedFiles.map((file) => {
            if (!context.sourceAttachmentIds.includes(file.id)) return file
            const clonedId = context.attachmentIdMap?.[file.id]
            const cloned = context.files?.find((candidate) => candidate.id === clonedId)
            if (!cloned) throw new Error(t('첨부 파일을 복제하지 못했습니다. 다시 시도하세요.'))
            return cloned
          })
          context.files = [...(context.files ?? []), ...files.filter((file) => !context.files?.some((existing) => existing.id === file.id))]
          const restoreToken = ++restoreSequence.current
          activeRestoreToken.current = restoreToken
          liveValue.current = ''
          liveAttachments.current = []
          liveActivatedSkillIds.current = []
          setValue('')
          setAttachments([])
          setActivatedSkillIds([])
          await deliverChat(context.forkId, text, files, search, skillIds, undefined, undefined, restoreToken)
        } catch (error) {
          if (!ownsEdit()) return
          liveValue.current = text
          liveAttachments.current = selectedFiles.map((file) => {
            const clonedId = context.attachmentIdMap?.[file.id]
            return context.files?.find((candidate) => candidate.id === clonedId) ?? file
          })
          liveActivatedSkillIds.current = skillIds
          setValue(text)
          setAttachments(liveAttachments.current)
          setActivatedSkillIds(skillIds)
          setChatError(errorMessage(error, t('수정한 메시지를 전송하지 못했습니다. 다시 시도하세요.')))
        } finally {
          if (ownsAccount()) {
            editSending.current = false
            setEditPending(false)
            setMessageEditBusy(false)
          }
        }
      })()
      return
    }
    clearMediaError()
    // A file the server could not read adds nothing to the turn; its chip goes with the send.
    const readableAttachments = attachments.filter((f) => !f.error)
    const attachmentIds = readableAttachments.map((f) => f.id)
    const attachmentLabels = readableAttachments.map((f) => f.name)
    const sentAttachments = readableAttachments
    const sentSkillIds = activeSkills.map((skill) => skill.id)
    setChatError(null)
    const restoreToken = ++restoreSequence.current
    activeRestoreToken.current = kind === 'chat' ? restoreToken : null
    // In a conversation the turn shows at once under the transcript, so the box empties now.
    // A new conversation has nothing to show it in yet: the text stays (read-only) until the
    // server takes the turn, so a slow start or a refusal never loses what was typed.
    // Skills stay on: picked once, they apply to the conversation's later turns too.
    const startSettled = () => {
      holdingStart.current = false
      setStartPending(false)
    }
    if (sessionId) {
      liveValue.current = ''
      liveAttachments.current = []
      setValue('')
      setAttachments([])
    } else {
      holdingStart.current = true
      setStartPending(true)
    }
    if (kind === 'av' && avOptions.mode === 'video') {
      // Video is a job; the card tracks it.
      void generateVideo(sessionId, text, {
        projectId,
        onSession: (id) => {
          releaseStart()
          navigate(`/s/${id}`, { replace: true })
        },
      }).finally(startSettled)
      return
    }
    if (kind === 'av' && avOptions.mode === 'audio') {
      // Audio returns inside its call; only video becomes a job.
      void generateAudio(sessionId, text, {
        projectId,
        onSession: (id) => {
          releaseStart()
          navigate(`/s/${id}`, { replace: true })
        },
      }).finally(startSettled)
      return
    }
    if (kind === 'image') {
      // Not streamed: `generateImages` writes both halves from one call.
      void generateImages(sessionId, text, {
        projectId,
        onSession: (id) => {
          releaseStart()
          navigate(`/s/${id}`, { replace: true })
        },
      }).finally(startSettled)
      return
    }
    // A document request typed into chat starts a new conversation on the
    // report or slides surface. An agent chat moves
    // only to a surface the agent names, and keeps the agent and the conversation.
    // A conversation that runs a user pipeline answers here: the pipeline is what it is for.
    const movable = kind === 'chat' && !session?.pipelineId && !sessionAgent?.pipelineId
    const detected = movable ? handoffSurface(text) : null
    const agentHandoff =
      movable && sessionId && sessionAgent
        ? agentHandoffSurface(text, sessionAgent.kinds, agentDocumentNames(sessionAgent, designTemplates))
        : null
    if (agentHandoff && sessionId) {
      void handOff(sessionId, agentHandoff, text, {
        attachments: sentAttachments,
        webSearch: sentWebSearch,
        onSession: (id) => navigate(`/s/${id}`),
      }).catch((error: unknown) => {
        setComposerRestore({
          sessionId,
          value: text,
          attachments: sentAttachments,
          activatedSkillIds: sentSkillIds,
          error: errorMessage(error, t('문서로 넘기지 못했습니다. 다시 시도하세요.')),
        })
      })
      return
    }
    const handoff = detected && !session?.agentId ? detected : null
    if (handoff) {
      let landedHandoffSessionId = sessionId
      void send(null, handoff, text, {
        projectId,
        webSearch: sentWebSearch,
        attachments: attachmentIds,
        attachmentNames: attachmentLabels,
        onSession: (id) => {
          landedHandoffSessionId = id
          releaseStart()
          carriedComposer = heldComposer(id)
          navigate(`/s/${id}`, { replace: !sessionId })
        },
      }).finally(startSettled).catch((error: unknown) => {
        const freshnessRefusal = errorCode(error) === 'freshness_verification_unavailable'
        setComposerRestore({
          sessionId: freshnessRefusal ? landedHandoffSessionId : sessionId,
          value: text,
          attachments: sentAttachments,
          activatedSkillIds: sentSkillIds,
          error: freshnessRefusal ? refusalSentence(errorCode(error), t) ?? '' : '',
        })
      })
      return
    }
    if (kind === 'chat') {
      void deliverChat(
        sessionId ?? reusableSessionId,
        text,
        sentAttachments,
        sentWebSearch,
        sentSkillIds,
        undefined,
        undefined,
        restoreToken,
      ).finally(startSettled).catch(() => undefined)
      return
    }
    // The pick is spent on this turn; the session row is the record from here.
    const sentTemplate = pendingTemplate?.surface === kind ? pendingTemplate : null
    setPendingTemplate(null)
    // Reassigned by `onSession` when this turn creates the session.
    let landedSessionId = sessionId
    void send(sessionId, kind, text, {
      projectId,
      webSearch: sentWebSearch,
      attachments: attachmentIds,
      attachmentNames: attachmentLabels,
      activatedSkillIds: sentSkillIds,
      renderTemplateId: shownTemplate ? shownTemplate.id : undefined,
      // Sending from /new/:kind creates a session; the URL has to follow it.
      onSession: (id) => {
        landedSessionId = id
        releaseStart()
        carriedComposer = heldComposer(id)
        navigate(`/s/${id}`, { replace: true })
      },
    })
      .finally(startSettled)
      .catch((error: unknown) => {
        // A refusal happens before the server stores the turn; restore the
        // draft through the store (see `deliverChat`).
        setComposerRestore({
          sessionId: landedSessionId,
          value: text,
          attachments: sentAttachments,
          activatedSkillIds: sentSkillIds,
          error:
            errorCode(error) === 'freshness_verification_unavailable'
              ? refusalSentence(errorCode(error), t) ?? ''
              : '',
        })
        // The session row was rolled back with the refused turn; hand the
        // pick back unless a newer one was chosen meanwhile.
        if (sentTemplate && !livePendingTemplate.current) setPendingTemplate(sentTemplate)
      })
  }

  // A queued Enter goes out once the upload or the model list is done.
  useEffect(() => {
    if (!queuedSend || uploading || awaitingModels) return
    submit()
    // `submit` reads the latest render; only the gates decide when to fire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queuedSend, uploading, awaitingModels])
  // Another conversation or surface: nothing held from the last one applies.
  useEffect(() => {
    holdingStart.current = false
    setStartPending(false)
    setQueuedSend(false)
  }, [sessionId, kind])

  const toggleSkill = (id: string, selected: boolean) => {
    activeRestoreToken.current = null
    const next = selected
      ? activeSkills.filter((skill) => skill.id !== id).map((skill) => skill.id)
      : [...activeSkills.map((skill) => skill.id), id]
    liveActivatedSkillIds.current = next
    setActivatedSkillIds(next)
    // In a conversation the change is the conversation's, kept on the server —
    // except while a message is being edited, where cancel must undo it.
    if (sessionId && kind === 'chat' && !editing) void setSessionSkills(sessionId, next)
  }
  // Everything but dictation, the model and send sits behind 「+」 (a bottom sheet on a phone);
  // what is switched on shows as a chip above the input.
  const showTemplates = hasTemplates && (started || !sessionId) && !editing
  const showSkills = !isMedia && usableSkills.length > 0
  // On a phone the microphone moves into 「+」 unless it is recording.
  const micInMenu = phone && canRecord && !recording && !transcribing
  const hasTools =
    !isMedia || showTemplates || showSkills || canWebSearch || usableConnectors.length > 0 ||
    menuAgents.length > 0 || micInMenu
  const closePlus = () => {
    setPlusOpen(false)
    setPlusView('main')
    setSkillQuery('')
  }
  const openPlusView = (view: PlusView) => {
    setPlusView(view)
    setSkillQuery('')
  }
  const shownSkills = skillQuery.trim()
    ? usableSkills.filter((s) => s.name.toLowerCase().includes(skillQuery.trim().toLowerCase()))
    : usableSkills
  const webSearchHelp: Record<WebSearchMode, string> = {
    auto: t('최신 사실이나 날씨를 물으면 찾아봅니다'),
    on: t('매번 웹에서 찾아봅니다'),
    off: t('웹을 찾아보지 않습니다'),
  }
  const back = (title: string) => (
    <>
      <ToolItem back icon={<ChevronLeft size={15} />} onClick={() => openPlusView('main')}>
        {t('뒤로')}
      </ToolItem>
      <MenuSeparator />
      <MenuLabel>{title}</MenuLabel>
    </>
  )
  const plusMenu = hasTools && (
    <Dropdown
      open={plusOpen}
      onOpenChange={(open) => (open ? setPlusOpen(true) : closePlus())}
      sheet={phone}
      label={t('도구')}
      className={plusView === 'main' ? 'min-w-64' : 'min-w-72'}
      trigger={({ open }) => (
        <button
          type="button"
          aria-label={t('도구')}
          title={t('파일·템플릿·스킬·웹 검색·커넥터·에이전트')}
          className={cn(
            'grid size-9 shrink-0 place-items-center rounded-full transition-colors hover:bg-elevated hover:text-fg',
            open ? 'bg-elevated text-fg' : 'text-muted',
          )}
        >
          <Plus size={18} />
        </button>
      )}
    >
      <div ref={plusViewRef} data-plus-view={plusView}>
        {plusView === 'main' && (
          <>
            {!isMedia && (
              <ToolItem
                icon={<Paperclip size={15} />}
                disabled={editPending}
                onClick={() => {
                  closePlus()
                  fileInput.current?.click()
                }}
              >
                {t('파일·사진 첨부')}
              </ToolItem>
            )}
            {showTemplates && (
              <ToolItem
                icon={<LayoutGrid size={15} />}
                label={t('템플릿 고르기')}
                hint={shownTemplate ? templateText(shownTemplate, currentLang() === 'en').name : undefined}
                onClick={() => {
                  closePlus()
                  setGalleryOpen(true)
                }}
              >
                {t('템플릿 고르기')}
              </ToolItem>
            )}
            {showSkills && (
              <ToolItem
                icon={<Sparkles size={15} />}
                label={t('스킬')}
                submenu
                hint={activeSkills.length ? String(activeSkills.length) : undefined}
                onClick={() => openPlusView('skills')}
              >
                {t('스킬')}
              </ToolItem>
            )}
            {canWebSearch && (
              <ToolItem
                icon={<Globe size={15} />}
                label={`${t('웹 검색')}: ${webSearchModeLabel}`}
                submenu
                hint={webSearchModeLabel}
                onClick={() => openPlusView('web')}
              >
                {t('웹 검색')}
              </ToolItem>
            )}
            {usableConnectors.length > 0 && (
              <ToolItem
                icon={<Plug size={15} />}
                label={
                  activeConnectors.length
                    ? t('커넥터 {n}개').replace('{n}', String(activeConnectors.length))
                    : t('커넥터')
                }
                submenu
                hint={activeConnectors.length ? String(activeConnectors.length) : undefined}
                onClick={() => openPlusView('connectors')}
              >
                {t('커넥터')}
              </ToolItem>
            )}
            {(menuAgents.length > 0 || !isMedia) && (
              <ToolItem
                icon={<AtSign size={15} />}
                label={t('에이전트')}
                submenu
                hint={sessionAgent?.name}
                onClick={() => openPlusView('agents')}
              >
                {t('에이전트')}
              </ToolItem>
            )}
            {micInMenu && (
              <ToolItem
                icon={<Mic size={15} />}
                label={t('말로 쓰기')}
                onClick={() => {
                  closePlus()
                  void startRecording()
                }}
              >
                {t('말로 쓰기')}
              </ToolItem>
            )}
          </>
        )}
        {plusView === 'skills' && (
          <>
            {back(t('이번 요청에 적용할 스킬 (최대 3개)'))}
            <div className="px-1.5 pb-1">
              <input
                value={skillQuery}
                onChange={(e) => setSkillQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') {
                    e.preventDefault()
                    plusViewRef.current?.querySelector<HTMLButtonElement>('[role="menuitemcheckbox"]:not(:disabled)')?.focus()
                  }
                }}
                placeholder={t('스킬 찾기')}
                aria-label={t('스킬 찾기')}
                className="h-8 w-full rounded-control border border-line bg-panel px-2.5 text-sm text-fg outline-none placeholder:text-faint focus:border-accent"
              />
            </div>
            {shownSkills.length === 0 && (
              <p className="px-2.5 py-1.5 text-sm text-faint">{t('맞는 스킬이 없습니다.')}</p>
            )}
            {shownSkills.map((s) => {
              const selected = activeSkills.some((skill) => skill.id === s.id)
              const unavailable = skillUnavailableReason(s)
              const limitReached = !selected && activeSkills.length >= 3
              return (
                <MenuItem
                  key={s.id}
                  checked={selected}
                  keepOpen
                  disabled={!!unavailable || limitReached}
                  title={limitReached ? t('최대 3개까지 선택할 수 있습니다.') : undefined}
                  hint={
                    unavailable
                      ? unavailable
                      : recommended.has(s.id)
                        ? t('이 프로젝트 스킬')
                        : t('약 {n} 토큰').replace('{n}', s.estimatedTokens.toLocaleString())
                  }
                  onClick={() => toggleSkill(s.id, selected)}
                >
                  {s.name}
                </MenuItem>
              )
            })}
            <MenuSeparator />
            <MenuItem icon={<Plus size={14} />} onClick={() => navigate('/customize?tab=skills')}>
              {t('스킬 관리')}
            </MenuItem>
          </>
        )}
        {plusView === 'web' && (
          <>
            {back(t('웹 검색'))}
            {searchBlocked && (
              <p className="px-2.5 pb-1.5 text-xs text-warn">
                {t('이 모델은 외부에 연결하지 않아 웹 검색을 쓸 수 없습니다')}
              </p>
            )}
            <div role="group" aria-label={t('웹 검색')}>
              {(['auto', 'on', 'off'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  role="menuitemradio"
                  aria-checked={webSearchMode === mode}
                  disabled={searchBlocked}
                  onClick={() => {
                    setWebSearchMode(mode)
                    closePlus()
                  }}
                  className="flex w-full items-start gap-2.5 rounded-control px-2.5 py-1.5 text-left transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-55"
                >
                  <span className="mt-0.5 grid size-4 shrink-0 place-items-center text-accent" aria-hidden>
                    {webSearchMode === mode && <Check size={14} />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-base text-fg">{webSearchModeLabels[mode]}</span>
                    <span className="block text-xs text-faint">{webSearchHelp[mode]}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {plusView === 'connectors' && (
          <>
            {back(t('커넥터'))}
            {/* Account-wide, unlike the per-turn controls beside it. */}
            <p className="px-2.5 pb-1.5 text-xs text-faint">
              {t('계정 전체 설정입니다. 여기서 끄면 모든 대화에서 꺼집니다.')}
            </p>
            {usableConnectors.map((c) => (
              <MenuItem
                key={c.id}
                keepOpen
                icon={<span className="text-base">{c.icon}</span>}
                hint={c.status === 'connected' ? (c.enabled ? t('켜짐') : t('꺼짐')) : t('인증 필요')}
                onClick={() => c.status === 'connected' && toggleConnector(c.id)}
              >
                {c.name}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem icon={<Plus size={14} />} onClick={() => navigate('/customize?tab=connectors')}>
              {t('커넥터 관리')}
            </MenuItem>
          </>
        )}
        {plusView === 'agents' && (
          <>
            {back(t('에이전트로 새 대화'))}
            {menuAgents.map((a) => {
              // An agent this screen does not offer opens on its own screen, which the row says.
              const startOn = usableAgents.includes(a) ? kind : agentStartKind(a, kind)
              const elsewhere = startOn !== kind
              return (
                <MenuItem
                  key={a.id}
                  // The name is the agent's own: the screen it opens on is a badge beside it, never
                  // read as part of the name (「개조식 보고서」 is not 「개조식 보고서 보고서」).
                  label={elsewhere ? t('{name} — {kind} 화면에서 열림').replace('{name}', a.name).replace('{kind}', t(kindMeta[startOn].label)) : a.name}
                  hint={
                    elsewhere ? (
                      <span
                        aria-hidden
                        data-testid="agent-surface-badge"
                        className="rounded-full border border-line px-1.5 py-px text-xs text-faint"
                      >
                        {t(kindMeta[startOn].label)}
                      </span>
                    ) : a.model ? (
                      a.model.split('/').pop()
                    ) : undefined
                  }
                  onClick={() => {
                    closePlus()
                    // An agent belongs to the session, so choosing one starts a conversation.
                    // What is typed or attached while it opens goes along with it.
                    const starting = newSession(startOn, { agentId: a.id, projectId })
                    agentStart.current = starting
                    void starting
                      .then((id) => {
                        carriedComposer = heldComposer(id)
                        navigate(`/s/${id}`)
                      })
                      .catch((err: unknown) => setNotice(startFailure(err, t)))
                      .finally(() => {
                        if (agentStart.current === starting) agentStart.current = null
                      })
                  }}
                >
                  {a.name}
                </MenuItem>
              )
            })}
            {menuAgents.length > 0 && <MenuSeparator />}
            <MenuItem
              icon={<Plus size={14} />}
              onClick={() => {
                closePlus()
                navigate('/customize/agents/new')
              }}
            >
              {t('에이전트 만들기')}
            </MenuItem>
          </>
        )}
      </div>
    </Dropdown>
  )
  const micButton = canRecord && (!phone || recording || transcribing) && (
    <button
      onClick={() => void (recording ? stopRecording() : startRecording())}
      disabled={transcribing}
      className={cn(
        'grid size-9 shrink-0 place-items-center rounded-control transition-colors hover:bg-elevated hover:text-fg',
        recording ? 'text-danger' : 'text-muted',
      )}
      aria-label={recording ? t('녹음 끝내기') : t('말로 쓰기')}
      aria-pressed={recording}
      title={
        recording
          ? t('누르면 녹음을 끝내고 받아씁니다')
          : `${t('마이크로 말하면 글로 받아 적습니다. 보내기 전에 고칠 수 있습니다')} (${isMac() ? '⌘' : 'Ctrl'}+Shift+M)`
      }
    >
      {transcribing ? (
        <Loader2 size={16} className="animate-spin" />
      ) : (
        <Mic size={16} className={recording ? 'animate-pulse' : undefined} />
      )}
    </button>
  )
  const modelPicker = (
    <fieldset disabled={!!editing} className="contents">
    <ModelPicker
      key={editing ? 'editing' : 'normal'}
      kind={kind}
      sessionId={sessionId ?? reusableSessionId}
      modality={kind === 'av' ? (avOptions.mode === 'video' ? 'video' : 'audio') : undefined}
      onEnableAuto={async (mode) => {
        setChatError(null)
        try {
          let id = sessionId ?? reusableSessionId
          if (!id) {
            id = await newSession(kind, { projectId, routingMode: mode })
            carriedComposer = heldComposer(id)
            setReusableSessionId(id)
            navigate(`/s/${id}`, { replace: true })
          } else {
            await setSessionRoutingMode(id, mode)
          }
        } catch (error) {
          setChatError(
            errorMessage(error, t('Auto를 켜지 못했습니다. 잠시 후 다시 시도하세요.')),
          )
        }
      }}
      onBusyChange={setModelSelectionPending}
    />
    </fieldset>
  )
  const sendButton = (
    <button
      onClick={streaming && sessionId ? () => stopStreaming(sessionId) : () => submit()}
      disabled={(!value.trim() && !streaming) || modelSelectionPending || editPending || startPending || queuedSend || !!pendingPrivacy}
      className={cn(
        'grid size-9 shrink-0 place-items-center rounded-full transition-colors',
        streaming
          ? 'bg-elevated text-fg'
          : 'bg-accent text-accent-fg hover:bg-accent-hover disabled:bg-elevated disabled:text-faint',
      )}
      aria-label={streaming ? t('중지') : editing ? t('수정 후 다시 보내기') : t('전송')}
      title={
        streaming
          ? t('생성을 멈춥니다')
          : startPending
            ? t('보내는 중…')
          : modelSelectionPending
            ? t('모델 설정 저장 중…')
          : !value.trim()
            ? t('보낼 내용을 먼저 입력하세요')
            : unsupportedVideo
              ? t('이 모델은 이 조합을 만들지 않습니다')
              : uploading
                ? t('첨부를 올리는 중입니다. 다 올리면 보낼 수 있습니다')
                : awaitingModels
                  ? t('모델을 불러오는 중입니다. 불러오면 보낼 수 있습니다')
                  : readingAttachment
                    ? t('첨부 글자를 읽는 중입니다({done}/{total}쪽). 지금 보내면 읽은 쪽까지만 씁니다')
                        .replace('{done}', String(readingAttachment.ocrDone ?? 0))
                        .replace('{total}', String(readingAttachment.ocrTotal ?? 0))
                    : t('Enter로도 보낼 수 있습니다')
      }
    >
      {streaming ? (
        <Square size={13} fill="currentColor" />
      ) : startPending || queuedSend ? (
        <Loader2 size={16} className="animate-spin" />
      ) : (
        <ArrowUp size={16} />
      )}
    </button>
  )

  return (
    <div className="relative mx-auto w-full max-w-3xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))]" {...dropHandlers}>
      {dragging && (
        <div className="pointer-events-none absolute inset-x-4 inset-y-0 z-10 grid place-items-center rounded-panel border-2 border-dashed border-accent bg-accent-soft/90 text-base font-medium text-accent">
          <span className="flex items-center gap-2">
            <Paperclip size={15} />
            {t('여기에 놓으면 첨부됩니다')}
          </span>
        </div>
      )}
      <div className="rounded-panel border border-line bg-panel shadow-raised transition-colors focus-within:border-line-strong">
        {editing && (
          <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-sm">
            <Pencil size={14} className="shrink-0 text-accent" />
            <span className="min-w-0 flex-1">{t('메시지 수정 · 새 대화')}</span>
            <Button size="icon" variant="ghost" title={t('수정 취소')} aria-label={t('수정 취소')}
              disabled={editPending || privacyRetrying} onClick={cancelEditing}>
              <X size={14} />
            </Button>
          </div>
        )}
        {(project ||
          attachments.length > 0 ||
          uploadFailed.length > 0 ||
          webSearchChip ||
          sessionAgent ||
          activeSkills.length > 0 ||
          autoBypassPreview ||
          // Same rule as the chip below: the session's own shape survives a reload.
          shownTemplate) && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
            {autoBypassPreview && (
              <Badge tone="warn" className="max-w-full whitespace-normal!">
                <Gauge size={11} />
                {session?.routingMode === 'auto_quality'
                  ? t('Auto · 품질 우선 · 이번 요청은 기능 사용으로 현재 모델 유지')
                  : t('Auto · 이번 요청은 기능 사용으로 품질 모델 유지')}
              </Badge>
            )}
            {webSearchChip &&
              (searchBlocked ? (
                <Badge tone="warn">
                  <Globe size={11} />
                  {t('웹 검색 안 함 · 이 모델은 외부에 연결하지 않습니다')}
                </Badge>
              ) : (
                <Badge tone={webSearchMode === 'on' ? 'accent' : 'neutral'}>
                  <Globe size={11} />
                  {`${t('웹 검색')}: ${webSearchModeLabel}`}
                  <button
                    type="button"
                    onClick={() => setWebSearchMode('auto')}
                    aria-label={t('웹 검색 자동으로 되돌리기')}
                    title={t('웹 검색 자동으로 되돌리기')}
                    className="ml-0.5 text-faint hover:text-fg"
                  >
                    <X size={10} />
                  </button>
                </Badge>
              ))}
            {sessionAgent && (
              <Badge tone="accent" title={t('이 대화의 에이전트')}>
                <AtSign size={11} />
                {sessionAgent.name}
              </Badge>
            )}
            {project && (
              <Badge tone="accent">
                <Boxes size={11} />
                {project.emoji} {project.name}
              </Badge>
            )}
            {shownTemplate && (
              <Badge tone="accent">
                <LayoutGrid size={11} />
                {templateText(shownTemplate, currentLang() === 'en').name}
                <button
                  type="button"
                  onClick={() => {
                    setPendingTemplate(null)
                    // Sticky server-side once used, so clear the session row too.
                    if (session?.renderTemplateId) {
                      void setSessionTemplate(session.id, null)
                    }
                  }}
                  disabled={!!editing}
                  aria-label={t('{name} 템플릿 해제').replace(
                    '{name}',
                    templateText(shownTemplate, currentLang() === 'en').name,
                  )}
                  className="ml-0.5 text-faint hover:text-fg"
                >
                  <X size={10} />
                </button>
              </Badge>
            )}
            {activeSkills.map((skill) => (
              <Badge key={skill.id} tone="accent">
                <Sparkles size={11} />
                {skill.name}
                <button
                  type="button"
                  onClick={() => {
                    activeRestoreToken.current = null
                    const next = activeSkills
                      .filter((candidate) => candidate.id !== skill.id)
                      .map((candidate) => candidate.id)
                    liveActivatedSkillIds.current = next
                    setActivatedSkillIds(next)
                  }}
                  aria-label={t('{name} 제거').replace('{name}', skill.name)}
                  className="ml-0.5 text-faint hover:text-fg"
                >
                  <X size={10} />
                </button>
              </Badge>
            ))}
            {attachments.map((f) => {
              const reading = ocrNote(f, t)
              return (
              <span
                key={f.id}
                data-ocr={f.ocrState ?? undefined}
                className={cn(
                  'flex items-center gap-1.5 rounded-control border px-1.5 py-0.5 text-xs',
                  f.error
                    ? 'border-warn/40 bg-warn/5 text-warn'
                    : 'border-line bg-elevated',
                )}
                title={
                  f.error ??
                  (reading
                    ? reading.text
                    : t('{n} 토큰').replace('{n}', f.tokens.toLocaleString()))
                }
              >
                {isReading(f) ? (
                  <Loader2 size={10} className="animate-spin text-faint" />
                ) : (
                  <Paperclip size={10} className={f.error ? '' : 'text-faint'} />
                )}
                {f.name}
                {reading && (
                  <span className={reading.tone === 'warn' ? 'text-warn' : 'text-faint'}>
                    {reading.text}
                  </span>
                )}
                {f.error && <TriangleAlert size={10} />}
                <button
                  onClick={() =>
                    setAttachments((current) => {
                      activeRestoreToken.current = null
                      const next = current.filter((item) => item.id !== f.id)
                      liveAttachments.current = next
                      return next
                    })
                  }
                  className="text-faint hover:text-fg"
                  aria-label={t('{name} 제거').replace('{name}', f.name)}
                >
                  <X size={10} />
                </button>
              </span>
              )
            })}
            {uploading && (
              <span className="flex items-center gap-1.5 rounded-control border border-line bg-elevated px-1.5 py-0.5 text-xs text-faint">
                <Loader2 size={10} className="animate-spin" />
                {t('업로드 중')}
              </span>
            )}
            {attachments.some((f) => f.error) && (
              <p role="status" className="w-full text-xs text-warn">
                {attachments
                  .filter((f) => f.error)
                  .map((f) => `${f.name} — ${f.error}`)
                  .join(' · ')}
              </p>
            )}
            {uploadFailed.length > 0 && (
              <p role="alert" className="w-full text-xs text-danger">
                {t('첨부하지 못했습니다: {names}').replace('{names}', uploadFailed.join(' · '))}
              </p>
            )}
          </div>
        )}

        {isMedia && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-2.5 py-2">
            {kind === 'image' ? <ImageOptions model={model} /> : <AvOptions />}
            <span
              className={cn(
                'ml-auto pr-1 text-xs',
                unsupportedVideo ? 'text-warn' : 'text-faint',
              )}
            >
              {unsupportedVideo
                ? t('이 모델은 이 조합을 만들지 않습니다')
                : kind === 'image' && FIGURE_STYLES[imageOptions.style]
                  // A figure is drawn by the text model, not priced as a picture.
                  ? t('도식은 글 모델이 씁니다 · 그림 요금이 아닙니다')
                  : t('예상 {n} 크레딧').replace('{n}', estimate.toLocaleString())}
            </span>
          </div>
        )}

        {/* A phone is one line — 「+」, the text, send — that grows with the text. */}
        <div className={cn(phone && 'flex items-end gap-1 px-1.5 py-1.5')}>
          {phone && plusMenu}
          <textarea
            ref={ref}
            autoFocus={autoFocus}
            rows={1}
            value={value}
            readOnly={editPending || startPending}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={() => { composing.current = false }}
            onChange={(e) => {
              activeRestoreToken.current = null
              // 「@」 typed into an empty box picks an agent, as the menu's 에이전트 does.
              if (e.target.value === '@' && !liveValue.current && usableAgents.length > 0 && !editing) {
                setPlusView('agents')
                setPlusOpen(true)
                return
              }
              liveValue.current = e.target.value
              // Written synchronously: a send navigates before an effect would run.
              if (!editing) draftStore.set(draftKey, e.target.value)
              setValue(e.target.value)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && editing && !e.nativeEvent.isComposing) {
                e.preventDefault()
                cancelEditing()
                return
              }
              if (holdToTalk(e)) return
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                submit()
              }
            }}
            onKeyUp={releaseToTalk}
            // File pastes only; text pastes are untouched.
            onPaste={onPasteFiles}
            placeholder={
              recording && pushToTalk.current
                ? t('듣고 있습니다. 스페이스를 떼면 받아 적습니다')
                : transcribing
                  ? t('받아쓰는 중…')
                  : pending
                    ? pending.stage === 'clarify'
                      ? t('답을 적거나, 위에서 고르세요')
                      : t('고칠 곳을 적어 주세요. 그대로 좋으면 위 버튼을 누르세요')
                    : t(placeholders[kind])
            }
            aria-label={t('프롬프트 입력')}
            data-composer=""
            className={cn(
              'block resize-none overflow-y-hidden bg-transparent text-md leading-relaxed text-fg placeholder:text-faint focus:outline-none phone:text-[1rem] phone:leading-[1.6]',
              phone ? 'min-w-0 flex-1 self-center px-1.5 py-1' : 'w-full px-4 pt-3.5 pb-1',
            )}
          />
          {phone && micButton}
          {phone && sendButton}
        </div>

        {!isMedia && (
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ATTACH_ACCEPT}
            className="hidden"
            aria-label={t('파일 선택')}
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? [])
              // Reset immediately so picking the same file twice still fires.
              e.target.value = ''
              void addFiles(picked)
            }}
          />
        )}

        {!phone && (
          <div className="flex items-center gap-1 px-2 pb-2">
            {plusMenu}
            {micButton}
            <div className="ml-auto flex min-w-0 items-center gap-1">
              {modelPicker}
              {sendButton}
            </div>
          </div>
        )}
      </div>
      {/* The phone's line has no room for the model; it sits just under the box. */}
      {phone && modelPicker && <div className="mt-1 flex justify-end">{modelPicker}</div>}
      {mediaError && (
        <p
          role="status"
          className="mt-2 flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger"
        >
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{mediaError}</span>
          <button onClick={clearMediaError} aria-label={t('닫기')} className="shrink-0">
            <X size={13} />
          </button>
        </p>
      )}
      {queuedSend && (
        <p role="status" className="mt-2 flex items-center gap-2 px-1 text-sm text-muted">
          <Loader2 size={13} className="shrink-0 animate-spin" />
          <span className="min-w-0 flex-1">
            {uploading
              ? t('첨부를 다 올리면 바로 보냅니다')
              : t('모델을 불러오면 바로 보냅니다')}
          </span>
          <button type="button" onClick={() => setQueuedSend(false)} className="shrink-0 underline">
            {t('취소')}
          </button>
        </p>
      )}
      {chatError && !pendingPrivacy && (
        <p
          role="alert"
          className="mt-2 flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger"
        >
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">{chatError}</span>
          <button onClick={() => setChatError(null)} aria-label={t('닫기')} className="shrink-0">
            <X size={13} />
          </button>
        </p>
      )}
      {/* Keyboard hints mean nothing on a phone. */}
      <p className="mt-2 text-center text-xs text-faint phone:hidden">
        {busy && isMedia
            ? t('생성 중입니다 — 완료되면 위 카드가 결과로 바뀝니다')
            : kind === 'image'
              ? t('Enter로 생성 · 만든 그림은 아티팩트에 쌓입니다')
              : kind === 'av' && avOptions.mode === 'audio'
                ? t('Enter로 생성 · 음성과 음악은 아티팩트에 쌓입니다')
                : kind === 'av'
                  ? t('Enter로 생성 · 영상은 몇 분 걸리고 진행은 카드에 표시됩니다')
                  : kind === 'slides'
                    ? t('Enter로 생성 · 구성을 잡은 뒤 한 장씩 채웁니다')
                    : `${t('Enter 전송, Shift+Enter 줄바꿈')}, ${isMac() ? 'Cmd' : 'Ctrl'}+/ ${t('단축키 보기')}`}
      </p>

      <Modal
        open={pendingPrivacy !== null}
        onClose={dismissPrivacyDecision}
        title={t('개인정보가 포함된 요청입니다')}
        description={t('외부 모델로 보내기 전에 처리 방법을 선택하세요. 탐지된 실제 값은 표시하거나 기록하지 않습니다.')}
        width="max-w-xl"
        footer={
          <Button
            disabled={privacyRetrying}
            onClick={dismissPrivacyDecision}
          >
            {t('편집으로 돌아가기')}
          </Button>
        }
      >
        {pendingPrivacy && (
          <>
            {chatError && (
              <p role="alert" className="rounded-control bg-danger/10 px-3 py-2 text-sm text-danger">
                {chatError}
              </p>
            )}
            <div className="flex flex-wrap gap-1.5" aria-label={t('탐지된 개인정보 범주')}>
              {pendingPrivacy.decision.findings.map((finding) => (
                <Badge key={`${finding.source}:${finding.category}`} tone="warn">
                  {t(SOURCE_LABEL[finding.source] ?? finding.source)} ·{' '}
                  {t(FINDING_LABEL[finding.category] ?? finding.category)} {finding.count}
                </Badge>
              ))}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {pendingPrivacy.decision.allowedActions.includes('route_strict_local') && (
                <Button
                  variant="primary"
                  disabled={privacyRetrying}
                  onClick={() => {
                    setPrivacyRetrying(true)
                    void deliverChat(
                      pendingPrivacy.sessionId,
                      pendingPrivacy.text,
                      pendingPrivacy.attachments,
                      false,
                      pendingPrivacy.activatedSkillIds,
                      'route_strict_local',
                      pendingPrivacy.decision.decisionToken,
                      pendingPrivacy.restoreToken,
                    )
                      .catch(() => undefined)
                      .finally(() => setPrivacyRetrying(false))
                  }}
                >
                  {t('안전한 로컬 모델로 전환')}
                </Button>
              )}
              <Button
                disabled={privacyRetrying}
                onClick={() => {
                  setPrivacyRetrying(true)
                  void deliverChat(
                    pendingPrivacy.sessionId,
                    pendingPrivacy.text,
                    pendingPrivacy.attachments,
                    pendingPrivacy.webSearch,
                    pendingPrivacy.activatedSkillIds,
                    'mask_external',
                    pendingPrivacy.decision.decisionToken,
                    pendingPrivacy.restoreToken,
                  )
                    .catch(() => undefined)
                    .finally(() => setPrivacyRetrying(false))
                }}
              >
                {t('가린 뒤 기존 모델 사용')}
              </Button>
              {pendingPrivacy.decision.allowedActions.includes('send_raw_external') && (
                <Button
                  variant="danger"
                  disabled={privacyRetrying}
                  onClick={() => {
                    setPrivacyRetrying(true)
                    void deliverChat(
                      pendingPrivacy.sessionId,
                      pendingPrivacy.text,
                      pendingPrivacy.attachments,
                      pendingPrivacy.webSearch,
                      pendingPrivacy.activatedSkillIds,
                      'send_raw_external',
                      pendingPrivacy.decision.decisionToken,
                      pendingPrivacy.restoreToken,
                    )
                      .catch(() => undefined)
                      .finally(() => setPrivacyRetrying(false))
                  }}
                >
                  {t('원문을 외부 모델로 전송')}
                </Button>
              )}
            </div>
            {pendingPrivacy.webSearch &&
              pendingPrivacy.decision.allowedActions.includes('route_strict_local') && (
                <p className="text-sm text-warn">
                  {t('안전한 로컬 모델은 외부에 연결하지 않습니다. 그 버튼을 고르면 이 요청은 웹 검색 없이 실행됩니다.')}
                </p>
              )}
            {pendingPrivacy.decision.safeModels.length === 0 && (
              <p className="text-sm text-warn">
                {t('관리자가 설정한 strict-local 모델이 없어 마스킹하거나 내용을 편집해야 합니다.')}
              </p>
            )}
          </>
        )}
      </Modal>

      <DesignGalleryModal
        kind={kind}
        sessionId={sessionId}
        open={galleryOpen}
        onClose={() => setGalleryOpen(false)}
      />
    </div>
  )
}
