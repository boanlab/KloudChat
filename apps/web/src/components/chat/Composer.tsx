import {
  ArrowUp,
  Boxes,
  Columns2,
  Gauge,
  Globe,
  LayoutGrid,
  LayoutTemplate,
  Mic,
  Paperclip,
  Plug,
  Loader2,
  Plus,
  ShieldCheck,
  Sparkles,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { FileRow, PrivacyDecision } from '@/lib/api'
import { transcriptionsApi } from '@/lib/api'
import { startWavRecording, type WavRecorder } from '@/lib/wavRecorder'
import { DesignGalleryModal, offersTemplates } from '@/components/chat/DesignGallery'
import { errorCode, errorMessage, PrivacyDecisionError, templateText } from '@/lib/api'
import { refusalSentence, startFailure } from '@/lib/failures'
import { handoffSurface } from '@/lib/documentRequest'
import { DICTATION_EVENT, isMac } from '@/lib/shortcuts'
import { currentLang } from '@/lib/i18n'
import { FINDING_LABEL } from '@/lib/privacy'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, MenuSeparator, Modal } from '@/components/ui'
import { cn } from '@/lib/utils'
import { effectiveModelId, useStore } from '@/store/useStore'
import type { PrivacyAction, SessionKind, Skill, StartingPoint } from '@/types'
import { ModelPicker } from './ModelPicker'
import { useFileDrop, usePasteFiles } from '@/lib/useFileDrop'
import { useT } from '@/lib/useT'

//: One verb ending (`~세요`) across all five surfaces. They sit next to each
//: other, so a mix of endings is visible.
const placeholders: Record<SessionKind, string> = {
  chat: '무엇이든 물어보세요',
  report: '보고서 주제와 넣고 싶은 절을 적으세요',
  slides: '발표 주제와 시간을 적으세요',
  image: '만들고 싶은 이미지를 설명하세요',
  av: '만들고 싶은 영상이나 오디오를 설명하세요',
}


const ASPECTS = ['16:9', '9:16', '4:3', '1:1']
/* What kind of picture, not only what it looks like. 자동 lets the planner
   read it off the request; 없음 sends the sentence as typed. Kept in step with
   `imagegen.STYLE_CHOICES`. */
const STYLES = ['자동', '도식', '인포그래픽', '차트', '사진', '일러스트', '미니멀', '3D 렌더', '수채화', '없음']
/** Agents whose conversation is in English, so dictation is pinned to it. */
const ENGLISH_AGENTS = new Set(['english-tutor', 'toeic-master', 'opic-master'])
const LABELS: { id: 'auto' | 'ko' | 'en' | 'none'; label: string }[] = [
  { id: 'auto', label: '자동' },
  { id: 'ko', label: '한국어' },
  { id: 'en', label: '영어' },
  { id: 'none', label: '없음' },
]
const VIDEO_DURATIONS = [4, 6, 8, 10]
const AUDIO_DURATIONS = [15, 30, 60, 120]
// Speech and music only: nothing serves sound effects, and an option that can
// only fail is worse than no option.
const AUDIO_KINDS = ['narration', 'music'] as const
const AUDIO_KIND_LABEL: Record<(typeof AUDIO_KINDS)[number], string> = {
  narration: '내레이션',
  music: '음악',
}

/** The six the gateway accepts. Anything else comes back as `alloy`. */
const VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as const

type PendingPrivacy = {
  decision: PrivacyDecision
  sessionId: string | null
  text: string
  attachments: FileRow[]
  activatedSkillIds: string[]
  startingTemplate: StartingPoint | null
  webSearch: boolean
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

/**
 * Where the chips beside this came from, while they are still the 서식's.
 *
 * A media 서식 leaves no chip on the composer, only these values — and they
 * are one workspace-wide preference, so they persist. Naming the source is
 * the honest half of that; turning any chip by hand takes the name off.
 */
function TemplateOptionNote({ kinds }: { kinds: readonly string[] }) {
  const t = useT()
  const template = useStore((s) => s.optionTemplate)
  // The 서식 chip a row above already names an image template while its pick is
  // waiting for a turn. Saying it twice, two inches apart, is not twice as true.
  const chipped = useStore((s) => s.pendingTemplate)
  if (!template || !kinds.includes(template.kind) || chipped?.id === template.id) return null
  return (
    <span
      className="flex items-center gap-1 text-xs text-faint"
      title={t('값을 직접 바꾸면 이 표시는 사라집니다')}
    >
      <LayoutGrid size={11} />
      {t('{name} 서식이 정한 값').replace(
        '{name}',
        templateText(template, currentLang() === 'en').name,
      )}
    </span>
  )
}

function ImageOptions() {
  const t = useT()
  const { imageOptions, setImageOptions } = useStore()
  const imageModel = useStore((s) => s.modelByKind.image)
  // Only Gemini's image models take the ratio as a parameter; the others
  // return a square whatever is asked, and are now told to compose for one.
  const squareOnly =
    Boolean(imageModel) && !/gemini|^google\//i.test(imageModel) && imageOptions.aspect !== '1:1'
  return (
    <>
      <OptionGroup
        label={t('비율')}
        value={imageOptions.aspect}
        options={ASPECTS}
        onChange={(v) => setImageOptions({ aspect: v })}
      />
      {squareOnly && (
        <span className="text-xs text-warn" title={t('이 모델은 비율 지정을 받지 않아 정사각형으로 그립니다. 16:9 가 꼭 필요하면 Gemini 이미지 모델을 고르세요.')}>
          {t('이 모델은 정사각형만 그립니다')}
        </span>
      )}
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
      <TemplateOptionNote kinds={['image']} />
    </>
  )
}

/**
 * The nearest clip this model is priced for, or null when it prices none.
 * Sound is given up first: a silent-only or sound-only model has made that
 * choice already, while 1080p → 720p is a visible loss worth asking about.
 */
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

/**
 * One surface, two modalities. `mode` comes first because it decides which of
 * the remaining chips apply — aspect ratio means nothing to a narration track.
 */
function AvOptions() {
  const t = useT()
  const { avOptions, setAvOptions, models, modelByKind } = useStore()
  const audio = avOptions.mode === 'audio'
  //: Whatever this surface will run on, which in 영상 is not necessarily a
  //: model that makes clips — hence the modality check below.
  const avModel = models.find((m) => m.id === modelByKind.av)
  //: Whether there is anything to move onto — an instance can serve this
  //: surface with speech alone, and asking for a clip model that is not in the
  //: catalogue would cost a 서식 its name and change nothing else.
  const hasVideoModel = models.some((m) => m.kinds.includes('av') && m.modality === 'video')
  const shapedFor = useRef<string | null>(null)
    /**
     * Turning 종류 to 영상 also changes the model, and the chips are left
     * showing whatever the last clip used. A model that does not price that
     * combination makes the composer refuse the turn, so the chips follow the
     * model here rather than surfacing at submit. Only when the model changes
     * underneath — a chip turned afterwards is the person's answer.
     *
     * The mode goes to the store first: 영상 is the mode this surface opens in,
     * and the cheapest remembered `av` model is a speech model.
     */
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
          {/* Music has no reader. The chip appears only where it applies,
              the way the video chips do. */}
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
          {/* Both were sent hard-coded — 720p and silent — while the backend
              took them and priced each combination differently. Choosing them
              is the difference between 12,000 and 32,000 크레딧 for one clip. */}
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
      <TemplateOptionNote kinds={['video', 'audio']} />
    </>
  )
}

/**
 * What a composer was holding at the moment the session it belongs to came
 * into existence.
 *
 * Creating a session moves the person from the start screen to the
 * conversation, and those are two different screens — so this component is
 * unmounted and a new one is mounted in its place. A ref inside it does not
 * survive that, and neither does the staged-but-unsent work above all the
 * attachments, which would be dropped in the gap without a word. Module scope
 * is the one thing here that outlives the remount.
 *
 * Read once by the new composer and cleared, so it can never re-apply itself
 * to a later conversation.
 */
let carriedComposer: {
  sessionId: string
  value: string
  attachments: FileRow[]
  startingTemplate: StartingPoint | null
  activatedSkillIds: string[]
  webSearch: boolean
} | null = null

/**
 * What is typed and not yet sent, by the conversation it was typed in — or by
 * the surface, on the home screen, where there is no conversation yet.
 *
 * Two opposite losses came from the sentence living in component state alone.
 * The home screen remounts the composer on a tab change, so a draft written
 * under 보고서 was gone the moment 슬라이드 was clicked. The conversation
 * screen keeps one composer mounted across `/s/A` → `/s/B`, so a draft written
 * in A turned up in B's box. Module scope outlives the remount, and the key
 * keeps each sentence with the place it was typed. In memory only: a reload
 * starts clean, the same as before.
 */
const drafts = new Map<string, string>()
const draftKeyFor = (sessionId: string | null, kind: SessionKind) => sessionId ?? `new:${kind}`

/** Whether an existing session has something typed and not yet sent — the one
 *  thing that makes an otherwise empty conversation worth keeping. */
export function hasUnsentDraft(sessionId: string) {
  return !!drafts.get(sessionId)?.trim()
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
  //: The two surfaces that leave the chat pipeline at submit: a picture or a
  //: clip is made by its own endpoint, which takes a prompt and the option
  //: chips and has no room for anything else the composer could collect.
  const isMedia = kind === 'image' || kind === 'av'
  //: The surfaces that can be written against the web. Chat runs a tool loop;
  //: a report, a deck and a rendered page each run a research pass before they
  //: write, planning queries off the request and reading the pages they find.
  //:
  //: This used to be chat alone, and the reasoning was sound for what existed
  //: then: the document writers were handed no tools, so a lit globe promised
  //: a search that was never going to happen. Hiding the control was the wrong
  //: half of that to fix. A chat answer that is out of date gets argued with;
  //: a report gets exported and mailed. The two media surfaces still hide it —
  //: an image endpoint takes a prompt and chips and nothing else.
  //: A rendered page has no `SessionKind` of its own — it is a report or a
  //: slides session wearing a render template — so these three cover it too.
  const canWebSearch = kind === 'chat' || kind === 'report' || kind === 'slides'
  const draftKey = draftKeyFor(sessionId, kind)
  const [value, setValue] = useState(() => drafts.get(draftKey) ?? '')
  const liveValue = useRef(value)
  liveValue.current = value
  const draftKeyRef = useRef(draftKey)
  useEffect(() => {
    if (draftKeyRef.current !== draftKey) {
      // The conversation changed under a mounted composer. What was typed has
      // already been kept under the old key, keystroke by keystroke; this
      // one's own draft comes up in its place — usually nothing.
      draftKeyRef.current = draftKey
      const own = drafts.get(draftKey) ?? ''
      liveValue.current = own
      setValue(own)
      return
    }
    drafts.set(draftKey, value)
  }, [draftKey, value])
  const restoreSequence = useRef(0)
  const activeRestoreToken = useRef<number | null>(null)

  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  /**
   * A sentence a gallery hands over. Inserted, not sent — and added to what is
   * in the box, never written over it.
   *
   * Somebody three sentences into a prompt who opens the gallery to see what a
   * shape does was asking a question, not offering to give those sentences up.
   * On the picture and clip surfaces, which are the only ones that still fill
   * the box at all, those sentences *are* the prompt, so what a replacement
   * throws away is the whole of the work — silently, and with nothing to press
   * to get it back.
   *
   * Appending rather than asking, because the question would arrive before its
   * answer is knowable: a confirm names no sentence the person has read yet,
   * and it puts a modal in front of a click that was an exploration. And
   * rather than filling only an empty box, because a shape picked for a prompt
   * already half written is the ordinary case, and doing nothing there is a
   * gallery whose cards stop working the moment somebody starts typing.
   *
   * What is appended arrives selected, so the one keystroke that undoes an
   * unwanted pick takes out exactly what the gallery put in and nothing that
   * was written by hand.
   */
  //: A range to restore once React has actually written the text it belongs to.
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
    // Into an empty box the caret lands at the end, as it always has: there is
    // nothing to take back out there, and a media 서식's sentence handed over
    // selected is a sentence the next keystroke destroys — which is the very
    // loss this is about, only pointed the other way.
    //
    // Left for the layout effect below rather than set here: the textarea is
    // controlled, so React writes this value in a later commit and that write
    // collapses any selection made before it.
    pendingSelection.current = [kept ? next.length - draft.length : next.length, next.length]
  }, [draft, setDraft])
  useLayoutEffect(() => {
    const range = pendingSelection.current
    if (!range) return
    pendingSelection.current = null
    ref.current?.setSelectionRange(range[0], range[1])
  }, [value])
  /** Uploaded files, not names: the turn sends ids and the server reads the text. */
  const [attachments, setAttachments] = useState<FileRow[]>([])
  const liveAttachments = useRef(attachments)
  liveAttachments.current = attachments
  const [pendingPrivacy, setPendingPrivacy] = useState<PendingPrivacy | null>(null)
  const [reusableSessionId, setReusableSessionId] = useState<string | null>(null)
  const [privacyRetrying, setPrivacyRetrying] = useState(false)
  const [modelSelectionPending, setModelSelectionPending] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  // A form a picked template brought with it. Taken once and cleared, so it
  // attaches to the draft it arrived with and not to every turn after it.
  const pendingAttachment = useStore((s) => s.pendingAttachment)
  /**
   * The 시작점 this turn carries, held here rather than in the store for the
   * same reason the attachments and the one-turn skills are: a refused turn
   * has to be handed back whole, and the gallery is long gone by then.
   */
  const [startingTemplate, setStartingTemplate] = useState<StartingPoint | null>(null)
  const liveStartingTemplate = useRef(startingTemplate)
  liveStartingTemplate.current = startingTemplate
  const pendingStartingTemplate = useStore((s) => s.pendingStartingTemplate)
  const setPendingStartingTemplate = useStore((s) => s.setPendingStartingTemplate)
  const pendingTemplate = useStore((s) => s.pendingTemplate)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  //: Readable from a callback that outlived the render which sent the turn,
  //: for the same reason the draft and the attachments each keep one.
  const livePendingTemplate = useRef(pendingTemplate)
  livePendingTemplate.current = pendingTemplate
  const designTemplates = useStore((s) => s.designTemplates)
  const promptTemplates = useStore((s) => s.promptTemplates)
  const [galleryOpen, setGalleryOpen] = useState(false)
  const setSessionTemplate = useStore((s) => s.setSessionTemplate)
  const setPendingAttachment = useStore((s) => s.setPendingAttachment)
  const composerRestore = useStore((s) => s.composerRestore)
  const setComposerRestore = useStore((s) => s.setComposerRestore)
  useEffect(() => {
    if (!pendingAttachment) return
    // A picture or a clip is made from the prompt alone, so a form that
    // followed a template onto one of those surfaces is let go instead of
    // being shown as a chip nothing reads — and let go rather than left in
    // the store, where it would attach itself to the next chat turn.
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
  /**
   * A refused turn, put back into the composer that is on screen now.
   *
   * The guards are the same ones the sending composer used to apply to itself,
   * and they are applied here for the same reason: submit clears the box, so
   * anything in it now was typed after the refusal left and outranks it. What
   * changed is only where the question is asked — of the live composer, which
   * on this path is a different instance from the one that sent the turn.
   */
  useEffect(() => {
    if (!composerRestore || composerRestore.sessionId !== sessionId) return
    setComposerRestore(null)
    if (composerRestore.error) setChatError(composerRestore.error)
    if (
      liveValue.current ||
      liveAttachments.current.length > 0 ||
      liveActivatedSkillIds.current.length > 0 ||
      liveStartingTemplate.current
    ) {
      return
    }
    activeRestoreToken.current = null
    liveValue.current = composerRestore.value
    liveAttachments.current = composerRestore.attachments
    liveActivatedSkillIds.current = composerRestore.activatedSkillIds
    liveStartingTemplate.current = composerRestore.startingTemplate
    setValue(composerRestore.value)
    setAttachments(composerRestore.attachments)
    setActivatedSkillIds(composerRestore.activatedSkillIds)
    setStartingTemplate(composerRestore.startingTemplate)
    requestAnimationFrame(() => ref.current?.focus())
  }, [composerRestore, sessionId, setComposerRestore])

  /**
   * 시작점의 빈칸에 적은 값. Keyed by the blank's index.
   *
   * 대화상자에서 받지 않는다. Five fields on a card in a dialogue asked for
   * decisions before the person had started, and cards that tall overflowed
   * the grid onto the row below. The dialogue picks the job; the questions
   * are asked here, as the chat starts, where the answer is the request.
   */
  const [startingValues, setStartingValues] = useState<Record<number, string>>({})

  useEffect(() => {
    if (!pendingStartingTemplate) return
    activeRestoreToken.current = null
    liveStartingTemplate.current = pendingStartingTemplate
    setStartingTemplate(pendingStartingTemplate)
    setStartingValues({})
    setPendingStartingTemplate(null)
    if (pendingStartingTemplate.text) setValue(pendingStartingTemplate.text)
    // 시작점이 필요하다고 한 것은 시작점이 켠다.
    //
    // 문헌 동향 조사 with web search off is a survey of the model's memory,
    // and the card said nothing about it. Now the card says, and the
    // composer holds to it: search on, the job's skills switched on by
    // name. The person can still turn either off — it is a default, not a
    // lock — but it is no longer something they have to know to do.
    if (pendingStartingTemplate.needs?.includes('web')) setWebSearch(true)
    const wanted = pendingStartingTemplate.skills ?? []
    if (wanted.length) {
      const ids = usableSkills
        .filter((skill) => wanted.includes(skill.name))
        .map((skill) => skill.id)
        .slice(0, 3)
      if (ids.length) setActivatedSkillIds(ids)
    }
    requestAnimationFrame(() => ref.current?.focus())
    // usableSkills is derived from store state that does not change between a
    // pick and this effect; listing it would re-run the effect on every store
    // tick and re-apply the pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingStartingTemplate, setPendingStartingTemplate])
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  /**
   * 말로 쓰기. The microphone records until it is pressed again, the clip
   * goes to the deployment's own Whisper (never to the browser vendor —
   * `webkitSpeechRecognition` streams audio to a third party), and the words
   * land in the box where the caret was. Nothing is sent until the person
   * reads them and presses send; the recording itself is not kept.
   */
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
      // 16 kHz 16-bit mono: 32,000 bytes a second. Under a third of a second
      // is a tap on the key, not a sentence — nothing to send to Whisper.
      if (blob.size <= 44 + 32_000 * 0.3) return ''
      // Heard in context. The last thing said back is the vocabulary the
      // next sentence most likely uses — 「some tennis」 in a chat about
      // tennis — and a chat with an English agent is English: without the
      // pin, one mumbled sentence came back as 《Fold and Vacant》 and the
      // tutor followed it out of the conversation.
      const lastAnswer = [...(session?.messages ?? [])]
        .reverse()
        .find((m) => m.role === 'assistant' && m.content?.trim())
      const english =
        sessionAgent?.catalogKey != null && ENGLISH_AGENTS.has(sessionAgent.catalogKey)
      const { text } = await transcriptionsApi.transcribe(blob, 'speech.wav', {
        language: english ? 'en' : undefined,
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
  /**
   * Push to talk, the way a muted Zoom call works: hold the space bar, speak,
   * let go — the words are written and sent, as if Enter had been pressed.
   *
   * Only from an empty box: a space typed into a sentence is a space. A
   * quick tap is dropped by the length check in `stopRecording`, so the key
   * never sends by accident. `pushToTalk` remembers that *this* recording is
   * a held key, so releasing it sends, while the mic button's own recording
   * still waits for the person to read and press send.
   */
  const pushToTalk = useRef(false)
  const holdToTalk = (e: { key: string; repeat: boolean; preventDefault: () => void }) => {
    if (e.key !== ' ' || !canRecord || busy || transcribing) return false
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
    void stopRecording().then((text) => {
      if (text.trim()) submit(text)
    })
    return true
  }
  // ⌘⇧M from anywhere: start the microphone, or stop it and take the words
  // into the box — the mic button, on a key. Not push-to-talk: this one waits
  // for the person to read and press send.
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
  useEffect(() => {
    if (!canRecord) return
    // Outside any field too — after clicking on the transcript, say — but
    // never on a control, where the space bar is a click.
    const idle = (target: EventTarget | null) => {
      const el = target as HTMLElement | null
      if (!el || el === document.body) return true
      if (el.isContentEditable) return false
      return !['INPUT', 'TEXTAREA', 'BUTTON', 'SELECT', 'A', 'SUMMARY'].includes(el.tagName)
    }
    const down = (e: KeyboardEvent) => {
      if (idle(e.target)) holdToTalk(e)
    }
    const up = (e: KeyboardEvent) => {
      if (idle(e.target)) releaseToTalk(e)
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  })
  /**
   * On by default, and off is the deliberate choice.
   *
   * It was the other way round, and what that produced was answers written
   * from a model's training data and presented with no sign of it — a GPU spec
   * two generations stale, a list of "current" open-source models that had
   * been superseded twice. The person only found out because they happened to
   * know better and typed 인터넷 검색해봐, which works exactly once per fact
   * they already doubted.
   *
   * `searchBlocked` still overrides this per turn, so a strict-local model
   * never inherits a lit globe from the default.
   */
  const [webSearch, setWebSearch] = useState(true)
  const [activatedSkillIds, setActivatedSkillIds] = useState<string[]>([])
  const liveActivatedSkillIds = useRef(activatedSkillIds)
  liveActivatedSkillIds.current = activatedSkillIds
  //: Read from callbacks that outlive the render which armed them, for the
  //: same reason the attachments and the draft each keep one.
  const liveWebSearch = useRef(webSearch)
  liveWebSearch.current = webSearch
  /**
   * Everything staged for a turn that has not been sent, addressed to the
   * session that has just been created for it. Built from the live refs rather
   * than the render's values: the callers are `onSession` callbacks, which fire
   * after the render that armed them has gone.
   */
  const heldComposer = (id: string) => ({
    sessionId: id,
    value: liveValue.current,
    attachments: liveAttachments.current,
    startingTemplate: liveStartingTemplate.current,
    activatedSkillIds: liveActivatedSkillIds.current,
    webSearch: liveWebSearch.current,
  })
  // Switching surfaces keeps this composer mounted, so a choice made for the
  // last one would follow the person to the next — and an upload that walked
  // onto the picture or clip surface would be dropped at submit, after the
  // wait and the credits. The typed sentence stays; it is theirs to reuse
  // anywhere.
  useEffect(() => {
    if (sessionId && carriedComposer?.sessionId === sessionId) {
      // Put back rather than merely left alone, because on a remount there is
      // nothing to leave alone — but only where it carries something. This is
      // captured when the session comes into existence, which on a send is
      // *after* the composer was cleared, so half of it is empty by then; a
      // refusal arriving in the same commit has already put the real work back
      // through the store, and assigning these over it would clear the very
      // thing both paths exist to keep.
      const held = carriedComposer
      carriedComposer = null
      // Empty on a send, which clears the box before the session exists; full
      // on the paths that create one without sending — turning Auto on is the
      // whole of somebody's unsent sentence surviving a change of screen.
      if (held.value) {
        liveValue.current = held.value
        setValue(held.value)
      }
      if (held.attachments.length) {
        liveAttachments.current = held.attachments
        setAttachments(held.attachments)
      }
      if (held.startingTemplate) {
        liveStartingTemplate.current = held.startingTemplate
        setStartingTemplate(held.startingTemplate)
      }
      if (held.activatedSkillIds.length) {
        liveActivatedSkillIds.current = held.activatedSkillIds
        setActivatedSkillIds(held.activatedSkillIds)
      }
      if (held.webSearch) setWebSearch(true)
      return
    }
    liveActivatedSkillIds.current = []
    setActivatedSkillIds([])
    // A 시작점 belongs to the surface it was picked on, and to one turn.
    liveStartingTemplate.current = null
    setStartingTemplate(null)
    liveAttachments.current = []
    setAttachments([])
    // The same reasoning as the skills beside it, and it was the one switch
    // left out: 웹 검색 is a decision about this conversation, and following
    // the person into the next one spends their credits on a search nobody
    // asked for there.
    setWebSearch(false)
  }, [sessionId, kind])
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
    compareMode,
    compareModels,
    toggleCompareMode,
    toggleCompareModel,
    models,
    modelByKind,
    imageOptions,
    setImageOptions,
    jobs,
    uploadFile,
    newSession,
    setSessionRoutingMode,
    generateImages,
    generateAudio,
    generateVideo,
    avOptions,
    mediaError,
    clearMediaError,
  } = useStore()

  const project = projects.find((p) => p.id === projectId)
  const effectiveSessionId = sessionId ?? reusableSessionId
  const session = sessions.find((candidate) => candidate.id === effectiveSessionId)
  //: A generation waiting to be answered or approved. Only the document
  //: surfaces ever have one; everywhere else this is null and nothing changes.
  const pending = session?.pending ?? null
  const sessionAgent = agents.find((agent) => agent.id === session?.agentId)
  /**
   * The rendering template this turn will use: the one just picked, or the one
   * the session is already wearing. Derived rather than mirrored into state —
   * the server is what makes the choice sticky, and a copy of it here would be
   * one more thing that can disagree with the document being produced.
   */
  const shownTemplate =
    (pendingTemplate?.surface === kind ? pendingTemplate : null) ??
    designTemplates.find((row) => row.id === session?.renderTemplateId) ??
    null
  // Sentences count too — 챗 has no 서식 at all, so asking only about shapes
  // hid the picker on the one surface where a saved starting point is the
  // whole of what it offers.
  // `offersTemplates` is the same gate the gallery button uses. Without it the
  // composer kept its own door open onto a picker 챗 no longer offers.
  const hasTemplates =
    offersTemplates(kind) &&
    (designTemplates.some((row) => row.surface === kind) ||
      promptTemplates.some((row) => row.kind === kind))
  //: Whether the empty screen — and its own copy of this button — is gone.
  const started = (session?.messages.length ?? 0) > 0
  const model = models.find(
    (candidate) => candidate.id === effectiveModelId(session, kind, agents, modelByKind),
  )
  /**
   * When the search toggle cannot reach the web whatever it says. A
   * strict-local model is handed no network tool at all — that route exists so
   * the text does not leave — and a comparison sends neither column the flag.
   * An answer written from memory under a lit globe is the worst outcome here,
   * so the control follows the turn rather than the stored preference.
   */
  const searchBlocked = (compareMode && kind === 'chat') || Boolean(model?.strictLocal)
  const effectiveWebSearch = webSearch && !searchBlocked
  const agentSkillAllowlist = sessionAgent?.skillIds
  const agentToolAllowlist = sessionAgent?.tools
  const recommended = new Set(project?.skillIds ?? [])
  const usableSkills = skills
    .filter(
      (skill) =>
        skill.enabled &&
        (skill.kinds.length === 0 || skill.kinds.includes(kind)) &&
        (agentSkillAllowlist === undefined ||
          agentSkillAllowlist === null ||
          agentSkillAllowlist.includes(skill.id)),
    )
    .sort((left, right) => Number(recommended.has(right.id)) - Number(recommended.has(left.id)))

  const skillUnavailableReason = (skill: Skill): string | null => {
    if (skill.requiredTools.length === 0) return null
    if (kind !== 'chat' || compareMode) {
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
  const autoBypassPreview =
    session?.routingMode === 'auto' &&
    Boolean(
      project ||
        sessionAgent ||
        attachments.length > 0 ||
        effectiveWebSearch ||
        activeSkills.length > 0,
    )
  const autoPausedForCompare =
    session?.routingMode === 'auto' && compareMode && kind === 'chat'
  // Empty `kinds` means every surface, the same rule skills and tool
  // allowlists use.
  const usableAgents = agents.filter(
    (a) => a.enabled && (a.kinds.length === 0 || a.kinds.includes(kind)),
  )
  const usableConnectors = connectors.filter(
    (c) => c.installed && (c.kinds.length === 0 || c.kinds.includes(kind)),
  )
  const activeConnectors = usableConnectors.filter((c) => c.enabled && c.status === 'connected')
  // Per picture, per second by (resolution, sound), or per call — never
  // `creditCost`, which is per 1k output tokens and reads as a fraction of the
  // real price on these surfaces.
  const videoRate =
    model?.creditPerSecond?.[`${avOptions.resolution}:${avOptions.withAudio ? 'sound' : 'silent'}`]
  // Not every model does every shape — Sora always carries sound, so it has no
  // silent price. The submit endpoint refuses those, so the control is disabled
  // rather than 422-ing after Enter.
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
  // This conversation's own turn. Another session generating is its business.
  const streaming = !!sessionId && !!running[sessionId]
  const busy = isMedia ? jobRunning : streaming

  const deliverChat = async (
    targetSessionId: string | null,
    text: string,
    files: FileRow[],
    search: boolean,
    skillIds: string[],
    startedFrom: StartingPoint | null,
    action?: PrivacyAction,
    decisionToken?: string,
    restoreToken?: number,
  ) => {
    setChatError(null)
    const resolvedSessionId = targetSessionId ?? reusableSessionId
    let attemptedSessionId = resolvedSessionId
    try {
      const acceptedSessionId = await send(resolvedSessionId, 'chat', text, {
        projectId,
        webSearch: search,
        attachments: files.map((file) => file.id),
        attachmentNames: files.map((file) => file.name),
        activatedSkillIds: skillIds,
        startingTemplate: startedFrom ?? undefined,
        privacyAction: action,
        privacyDecisionToken: decisionToken,
        onSession: (id) => {
          attemptedSessionId = id
          carriedComposer = heldComposer(id)
          navigate(`/s/${id}`, { replace: true })
        },
      })
      if (!sessionId) navigate(`/s/${acceptedSessionId}`, { replace: true })
      activeRestoreToken.current = null
      setReusableSessionId(null)
      setPendingPrivacy(null)
    } catch (error) {
      if (error instanceof PrivacyDecisionError) {
        const decisionSessionId = error.sessionId ?? resolvedSessionId
        setReusableSessionId(decisionSessionId)
        setPendingPrivacy({
          decision: error.decision,
          sessionId: decisionSessionId,
          text,
          attachments: files,
          activatedSkillIds: skillIds,
          startingTemplate: startedFrom,
          webSearch: search,
          restoreToken: restoreToken ?? ++restoreSequence.current,
        })
        return
      }
      setReusableSessionId((current) => current ?? attemptedSessionId)
      // The code, not the sentence: `errorMessage` is what goes on screen and
      // deliberately swallows machine strings, so branching on its output
      // depended on one leaking through.
      const notice =
        errorCode(error) === 'auto_quality_model_required'
          ? t('Auto에 사용할 품질 모델을 다시 선택하세요. 초안과 첨부 파일은 그대로 보관했습니다.')
          : (refusalSentence(errorCode(error), t) ??
            errorMessage(error, t('요청을 전송하지 못했습니다. 잠시 후 다시 시도하세요.')))
      // Handed back through the store rather than through this component's own
      // setters. A turn that created a session has already moved the person to
      // the conversation, and the composer that sent it is unmounted by the
      // time the refusal lands — the sentence, the uploads and the reason all
      // went to a screen nobody was looking at. Whichever composer is on the
      // session now is the one that has to receive them.
      setComposerRestore({
        sessionId: attemptedSessionId,
        value: text,
        attachments: files,
        activatedSkillIds: skillIds,
        startingTemplate: startedFrom,
        error: notice,
      })
      throw error
    }
  }

  const dismissPrivacyDecision = () => {
    if (!pendingPrivacy || privacyRetrying) return
    // The response can arrive after the user has already started another
    // draft. Only the submission that still owns the cleared composer may put
    // its text and files back; a newer edit/upload deliberately revokes that
    // ownership in the handlers above.
    if (activeRestoreToken.current === pendingPrivacy.restoreToken) {
      activeRestoreToken.current = null
      liveValue.current = pendingPrivacy.text
      liveAttachments.current = pendingPrivacy.attachments
      liveActivatedSkillIds.current = pendingPrivacy.activatedSkillIds
      liveStartingTemplate.current = pendingPrivacy.startingTemplate
      setValue(pendingPrivacy.text)
      setAttachments(pendingPrivacy.attachments)
      setActivatedSkillIds(pendingPrivacy.activatedSkillIds)
      setStartingTemplate(pendingPrivacy.startingTemplate)
    }
    setReusableSessionId(pendingPrivacy.sessionId)
    setPendingPrivacy(null)
  }

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 280)}px`
  }, [value])

  /**
   * Takes files from wherever they came from — the picker, a drop, a paste.
   *
   * One path for all three. It used to live inline in the file input's own
   * `onChange`, which is why dropping and pasting could not reuse it and, in
   * practice, why neither existed.
   */
  const addFiles = async (picked: File[]) => {
    if (!picked.length || isMedia) return
    setUploading(true)
    try {
      for (const file of picked) {
        const row = await uploadFile(file, {
          projectId: projectId ?? undefined,
          sessionId: sessionId ?? undefined,
        }).catch(() => null)
        if (row) {
          setAttachments((current) => {
            activeRestoreToken.current = null
            const next = [...current, row]
            liveAttachments.current = next
            return next
          })
        }
      }
    } finally {
      setUploading(false)
    }
  }

  // Attachments are a chat/report/deck idea; the two media surfaces send a
  // prompt and option chips and have nowhere to put a file, so they neither
  // light up nor swallow the browser's default.
  const { over: dragging, handlers: dropHandlers } = useFileDrop(
    (files) => void addFiles(files),
    !isMedia,
  )
  const onPasteFiles = usePasteFiles((files) => void addFiles(files))

  /**
   * 채운 빈칸을 요청 앞에 줄로 붙인다. 채운 것만 — 「기간·언어: 」 한 줄은
   * 모델에게 아무것도 말하지 않고 읽는 사람에게는 빠뜨렸다고 말한다.
   */
  const withStartingValues = (typed: string) => {
    if (!startingTemplate) return typed
    // 매체 서식은 문장 하나를 채운다. Its `{name}` blanks take what was
    // filled in, and a blank left empty keeps its example — so the sentence
    // the picture model reads is always whole. The person's own words go
    // after it.
    if (startingTemplate.examplePrompt && startingTemplate.blanks) {
      const values = Object.fromEntries(
        startingTemplate.blanks.map((blank, index) => [
          blank.name,
          (startingValues[index] ?? '').trim() || startingTemplate.examples?.[index] || '',
        ]),
      )
      const sentence = startingTemplate.examplePrompt.replace(/\{(\w+)\}/g, (whole, name: string) =>
        name in values ? values[name] : whole,
      )
      return [sentence, typed].filter(Boolean).join('\n')
    }
    const lines = startingTemplate.fills
      .map((fill, index) => [fill, (startingValues[index] ?? '').trim()] as const)
      .filter(([, filled]) => filled)
      .map(([fill, filled]) => `${fill}: ${filled}`)
    if (!lines.length) return typed
    return [startingTemplate.title, ...lines, typed].filter(Boolean).join('\n')
  }
  // 매체 서식은 예시만으로도 온전한 문장이 되므로 빈칸이 비어도 보낼 수 있다.
  const startingFilled =
    Object.values(startingValues).some((one) => one.trim()) ||
    Boolean(startingTemplate?.examplePrompt)

  const submit = (spoken?: string) => {
    const text = withStartingValues((spoken ?? value).trim())
    if (!text || busy || modelSelectionPending || unsupportedVideo) return
    clearMediaError()
    const attachmentIds = attachments.map((f) => f.id)
    const attachmentLabels = attachments.map((f) => f.name)
    const sentAttachments = attachments
    const sentSkillIds = activeSkills.map((skill) => skill.id)
    const sentStartingTemplate = startingTemplate
    setChatError(null)
    const restoreToken = ++restoreSequence.current
    activeRestoreToken.current = kind === 'chat' ? restoreToken : null
    // Clear the composer first: the session is created server-side, so awaiting
    // the round trip would leave the sent text sitting in the box.
    liveValue.current = ''
    liveAttachments.current = []
    liveActivatedSkillIds.current = []
    // Not sticky, unlike the 서식 chip beside it: a 시작점 starts one turn and
    // then the conversation is the person's own.
    liveStartingTemplate.current = null
    setValue('')
    setAttachments([])
    setActivatedSkillIds([])
    setStartingTemplate(null)
    setStartingValues({})
    if (kind === 'av' && avOptions.mode === 'video') {
      // A ticket, not an answer: the clip takes minutes and the job row
      // outlives this request, so the card carries it.
      void generateVideo(sessionId, text, {
        projectId,
        onSession: (id) => navigate(`/s/${id}`, { replace: true }),
      })
      return
    }
    if (kind === 'av' && avOptions.mode === 'audio') {
      // Audio returns inside its call; only video becomes a job.
      void generateAudio(sessionId, text, {
        projectId,
        onSession: (id) => navigate(`/s/${id}`, { replace: true }),
      })
      return
    }
    if (kind === 'image') {
      // A turn like any other, but not a streamed one: the pictures come back
      // from one call, so `generateImages` writes both halves itself.
      void generateImages(sessionId, text, {
        projectId,
        onSession: (id) => navigate(`/s/${id}`, { replace: true }),
      })
      return
    }
    // "협조 공문 작성해줘" typed into a chat is an order for a document, and a
    // chat bubble is the wrong shape for one — no sections, no 서식, no file.
    // The sentence and its attachments go to the report surface instead, as
    // a new conversation there — and "발표 자료 만들어줘" to the slides. An
    // agent chat or a chat 시작점 is a deliberate choice of surface and keeps
    // the sentence.
    const handoff = kind === 'chat' && !sessionAgent && !sentStartingTemplate ? handoffSurface(text) : null
    if (handoff) {
      void send(null, handoff, text, {
        projectId,
        webSearch: effectiveWebSearch,
        attachments: attachmentIds,
        attachmentNames: attachmentLabels,
        onSession: (id) => {
          carriedComposer = heldComposer(id)
          navigate(`/s/${id}`, { replace: !sessionId })
        },
      }).catch(() => {
        setComposerRestore({
          sessionId,
          value: text,
          attachments: sentAttachments,
          activatedSkillIds: sentSkillIds,
          startingTemplate: sentStartingTemplate,
          error: '',
        })
      })
      return
    }
    if (kind === 'chat') {
      void deliverChat(
        sessionId ?? reusableSessionId,
        text,
        sentAttachments,
        effectiveWebSearch,
        sentSkillIds,
        sentStartingTemplate,
        undefined,
        undefined,
        restoreToken,
      ).catch(() => undefined)
      return
    }
    // The pick is spent here, on the turn now leaving. From this point the
    // session row the server writes is the record of the shape — the chip
    // already prefers it — and a pick left standing would follow the person
    // into the next conversation and outrank the shape that one was wearing.
    const sentTemplate = pendingTemplate?.surface === kind ? pendingTemplate : null
    setPendingTemplate(null)
    //: Which session the refusal below has to be addressed to. Reassigned by
    //: `onSession` when the turn is the one that brings the session into being.
    let landedSessionId = sessionId
    void send(sessionId, kind, text, {
      projectId,
      webSearch: effectiveWebSearch,
      attachments: attachmentIds,
      attachmentNames: attachmentLabels,
      activatedSkillIds: sentSkillIds,
      // Only the writing surfaces take one; an image template is applied by
      // `generateImages` on its own path above.
      renderTemplateId:
        shownTemplate && shownTemplate.kind !== 'image' ? shownTemplate.id : undefined,
      startingTemplate: sentStartingTemplate ?? undefined,
      // Sending from /new/:kind creates a session; the URL has to follow it.
      onSession: (id) => {
        landedSessionId = id
        carriedComposer = heldComposer(id)
        navigate(`/s/${id}`, { replace: true })
      },
    })
      .catch(() => {
        // A policy/permission refusal happens before the server stores the
        // turn. Restore the exact draft instead of making the user reconstruct
        // the sentence, uploads, and one-turn skill choice.
        //
        // Through the store, for the same reason the chat path does it: a turn
        // that created a session has already moved the person to it, and the
        // composer that sent this one is gone by the time the refusal lands.
        // The guard about a newer draft is applied where it can be answered —
        // on whichever composer is actually on screen.
        setComposerRestore({
          sessionId: landedSessionId,
          value: text,
          attachments: sentAttachments,
          activatedSkillIds: sentSkillIds,
          startingTemplate: sentStartingTemplate,
          error: '',
        })
        // A refused turn never reached the server, so the session row was
        // rolled back with it and the shape is nobody's record now. Hand the
        // pick back with the rest of the draft, unless a newer one has been
        // chosen while this request was in flight.
        if (sentTemplate && !livePendingTemplate.current) setPendingTemplate(sentTemplate)
      })
  }

  return (
    <div className="relative mx-auto w-full max-w-3xl px-4 pb-4" {...dropHandlers}>
      {/* Shown only while something is actually over it. A permanent dashed
          rectangle would be a second input competing with the real one. */}
      {dragging && (
        <div className="pointer-events-none absolute inset-x-4 inset-y-0 z-10 grid place-items-center rounded-panel border-2 border-dashed border-accent bg-accent-soft/90 text-base font-medium text-accent">
          <span className="flex items-center gap-2">
            <Paperclip size={15} />
            {t('여기에 놓으면 첨부됩니다')}
          </span>
        </div>
      )}
      {/* 한 덩어리로 읽히는 입력 상자. 첨부·옵션·모델·전송이 모두 이 테두리
          안에 있고, 바깥에는 아무 버튼도 두지 않는다 — 프롬프트를 쓰는 동안
          눈이 갈 곳은 여기 하나면 된다. */}
      <div className="rounded-panel border border-line bg-panel shadow-raised transition-colors focus-within:border-line-strong">
        {(project ||
          attachments.length > 0 ||
          webSearch ||
          activeSkills.length > 0 ||
          autoBypassPreview ||
          autoPausedForCompare ||
          startingTemplate ||
          // The chip below reads the session's own shape as well as the pick
          // waiting for a turn, and this row has to open on the same rule.
          // Asking only about the pick hid the whole row after a reload —
          // client state is gone by then, while the shape the session is
          // wearing survives and keeps coming out in every answer.
          shownTemplate ||
          (compareMode && kind === 'chat')) && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
            {autoBypassPreview && (
              <Badge tone="warn">
                <Gauge size={11} />
                {t('Auto · 이번 요청은 기능 사용으로 품질 모델 유지')}
              </Badge>
            )}
            {autoPausedForCompare && (
              <Badge tone="warn">
                <Gauge size={11} />
                {t('Auto 일시 중지 · 비교할 모델을 직접 실행')}
              </Badge>
            )}
            {compareMode && kind === 'chat' && (
              <Badge tone="accent">
                <Columns2 size={11} />
                {compareModels
                  .map((id) => models.find((m) => m.id === id)?.label ?? id)
                  .join(' vs ')}
                {/* The way out, on the thing that shows the state. There was a
                    toggle for this, buried in the ⧉ menu, and every other chip
                    in this row carries its own × — so the one mode you could
                    not obviously leave was the one that doubles the cost of
                    every request until you do. */}
                <button
                  type="button"
                  onClick={toggleCompareMode}
                  aria-label={t('모델 비교 끄기')}
                  title={t('모델 비교 끄기')}
                  className="ml-0.5 text-faint hover:text-fg"
                >
                  <X size={10} />
                </button>
              </Badge>
            )}
            {webSearch &&
              (searchBlocked ? (
                // The toggle is the person's standing wish; this row is what
                // the turn will actually do. They disagree here, and saying so
                // is the whole point of the chip.
                <Badge tone="warn">
                  <Globe size={11} />
                  {compareMode && kind === 'chat'
                    ? t('웹 검색 안 함 · 모델 비교는 검색 없이 실행합니다')
                    : t('웹 검색 안 함 · 이 모델은 외부에 연결하지 않습니다')}
                </Badge>
              ) : (
                <Badge tone="accent">
                  <Globe size={11} />
                  {t('웹 검색')}
                </Badge>
              ))}
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
                    // 서식이 가면 그 서식의 질문도 간다.
                    if (startingTemplate?.examplePrompt) {
                      liveStartingTemplate.current = null
                      setStartingTemplate(null)
                      setStartingValues({})
                    }
                    // Sticky server-side once a turn has used it, so clearing
                    // the chip has to clear the row too.
                    if (session?.renderTemplateId) {
                      void setSessionTemplate(session.id, null)
                    }
                  }}
                  aria-label={t('{name} 서식 해제').replace(
                    '{name}',
                    templateText(shownTemplate, currentLang() === 'en').name,
                  )}
                  className="ml-0.5 text-faint hover:text-fg"
                >
                  <X size={10} />
                </button>
              </Badge>
            )}
            {/* Beside the 서식 chip and read the same way: one names the
                shape the answer comes out in, this one names where the asking
                started. Neither is in the box, so neither ends up in the
                transcript as something the person wrote. */}
            {/* 매체 서식은 바로 위의 서식 칩이 이미 이름을 말한다 — 같은 이름의
                칩 둘은 하나로 읽히지 않는다. */}
            {startingTemplate && (!startingTemplate.examplePrompt || !pendingTemplate) && (
              <Badge tone="accent">
                <LayoutTemplate size={11} />
                {startingTemplate.title}
                <button
                  type="button"
                  onClick={() => {
                    activeRestoreToken.current = null
                    liveStartingTemplate.current = null
                    setStartingTemplate(null)
                  }}
                  aria-label={t('{name} 시작점 해제').replace('{name}', startingTemplate.title)}
                  className="ml-0.5 text-faint hover:text-fg"
                >
                  <X size={10} />
                </button>
              </Badge>
            )}
            {/* 파일이 있어야 하는 일인데 아직 없다. Said beside the chip
                rather than discovered after sending: 전공 원문 읽기 without
                the 원문 answers about nothing. */}
            {startingTemplate?.needs?.includes('file') && attachments.length === 0 && (
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                className="inline-flex h-6 items-center gap-1 rounded-full border border-warn/40 bg-warn/10 px-2 text-xs text-warn hover:bg-warn/20"
              >
                <Paperclip size={11} />
                {t('이 일에는 파일이 필요합니다 — 첨부하기')}
              </button>
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
            {attachments.map((f) => (
              <span
                key={f.id}
                className={cn(
                  'flex items-center gap-1.5 rounded-control border px-1.5 py-0.5 text-xs',
                  // Uploaded but unreadable: said here, so nobody asks about
                  // contents that never existed.
                  f.error
                    ? 'border-warn/40 bg-warn/5 text-warn'
                    : 'border-line bg-elevated',
                )}
                title={f.error ?? t('{n} 토큰').replace('{n}', f.tokens.toLocaleString())}
              >
                <Paperclip size={10} className={f.error ? '' : 'text-faint'} />
                {f.name}
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
            ))}
            {uploading && (
              <span className="flex items-center gap-1.5 rounded-control border border-line bg-elevated px-1.5 py-0.5 text-xs text-faint">
                <Loader2 size={10} className="animate-spin" />
                {t('업로드 중')}
              </span>
            )}
            {/*
              The reason, in words, on the screen.
              
              A failed upload wore a ⚠ and kept why it failed in a `title` —
              which is a hover, and a hover is nothing on a phone and nearly
              nothing to somebody who has not been told there is something to
              hover over. So a scan nobody can read and a file that is not what
              its name says both arrived as a chip with a small orange triangle,
              and the next thing that happened was a question about contents
              that were never there.
            */}
            {attachments.some((f) => f.error) && (
              <p role="status" className="w-full text-xs text-warn">
                {attachments
                  .filter((f) => f.error)
                  .map((f) => `${f.name} — ${f.error}`)
                  .join(' · ')}
              </p>
            )}
          </div>
        )}

        {/* 생성 파라미터 바 — 이미지·오디오/동영상 전용 */}
        {isMedia && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-2.5 py-2">
            {kind === 'image' ? <ImageOptions /> : <AvOptions />}
            <span
              className={cn(
                'ml-auto pr-1 text-xs',
                unsupportedVideo ? 'text-warn' : 'text-faint',
              )}
            >
              {unsupportedVideo
                ? t('이 모델은 이 조합을 만들지 않습니다')
                : pendingTemplate?.kind === 'image' && pendingTemplate.figure
                  // 도식은 글 모델이 쓴다. The image price on the bar would
                  // be the price of a picture nobody is about to make.
                  ? t('도식은 글 모델이 씁니다 · 그림 요금이 아닙니다')
                  : t('예상 {n} 크레딧').replace('{n}', estimate.toLocaleString())}
            </span>
          </div>
        )}

        {/* 시작점의 질문. 대화상자가 아니라 여기서 받는다 — 일을 고른 뒤,
            챗을 시작하면서. 빈칸마다 예시가 흐리게 들어 있고, 채운 만큼이
            요청 앞에 줄로 붙는다. 아래 상자는 덧붙일 말을 위한 자리다. */}
        {!pending && startingTemplate && startingTemplate.fills.length > 0 && (
          <div
            role="group"
            aria-label={t('{name} 시작점 질문').replace('{name}', startingTemplate.title)}
            className="grid gap-x-3 gap-y-2 border-b border-line px-3 py-2.5 sm:grid-cols-2"
          >
            {startingTemplate.fills.map((fill, index) => {
              const blank = startingTemplate.blanks?.[index]
              const set = (next: string) => {
                setStartingValues((all) => ({ ...all, [index]: next }))
                // 비율 빈칸은 비율 칩이다. A media 서식 recommends a shape per
                // job — 16:9 for a talk cover, 9:16 for a printed poster — and
                // the chip beside the send button is what the picture request
                // actually reads, so the pick lands there too.
                if (blank?.name === 'aspect') {
                  const ratio = /^\s*(\d+:\d+)/.exec(next)?.[1]
                  if (ratio && ASPECTS.includes(ratio)) setImageOptions({ aspect: ratio })
                }
              }
              return (
                <label key={`${fill}-${index}`} className={cn('block min-w-0', blank?.long && 'sm:col-span-2')}>
                  <span className="mb-0.5 block text-xs font-medium text-muted">{fill}</span>
                  {blank?.options?.length ? (
                    <select
                      // 고르지 않았으면 서식의 기본값이 보이고, 그대로 나간다.
                      value={startingValues[index] ?? startingTemplate.examples?.[index] ?? ''}
                      onChange={(e) => set(e.target.value)}
                      aria-label={`${startingTemplate.title} · ${fill}`}
                      className="h-8 w-full rounded-control border border-line bg-panel px-2 text-sm focus:border-accent focus:outline-none"
                    >
                      {blank.options.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  ) : blank?.long ? (
                    <textarea
                      value={startingValues[index] ?? ''}
                      onChange={(e) => set(e.target.value)}
                      rows={4}
                      placeholder={startingTemplate.examples?.[index] || ''}
                      aria-label={`${startingTemplate.title} · ${fill}`}
                      className="w-full resize-y rounded-control border border-line bg-panel px-2 py-1.5 text-sm leading-relaxed placeholder:text-faint focus:border-accent focus:outline-none"
                    />
                  ) : (
                    <input
                      value={startingValues[index] ?? ''}
                      onChange={(e) => set(e.target.value)}
                      onKeyDown={(e) => {
                        // Enter 는 여기서도 보낸다 — 마지막 칸을 채우고 상자로
                        // 옮겨 갈 이유가 없다.
                        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                          e.preventDefault()
                          submit()
                        }
                      }}
                      placeholder={startingTemplate.examples?.[index] || ''}
                      aria-label={`${startingTemplate.title} · ${fill}`}
                      className="h-8 w-full rounded-control border border-line bg-panel px-2 text-sm placeholder:text-faint focus:border-accent focus:outline-none"
                    />
                  )}
                </label>
              )
            })}
          </div>
        )}
        <textarea
          ref={ref}
          autoFocus={autoFocus}
          rows={1}
          value={value}
          onChange={(e) => {
            activeRestoreToken.current = null
            liveValue.current = e.target.value
            // Keep it before React gets a chance to unmount this composer.
            // Picking a starting point navigates immediately; an effect that
            // runs after paint can otherwise lose the final keystroke — or the
            // whole draft when typing and clicking happen in one frame.
            drafts.set(draftKey, e.target.value)
            setValue(e.target.value)
          }}
          onKeyDown={(e) => {
            if (holdToTalk(e)) return
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
          onKeyUp={releaseToTalk}
          // A screenshot on the clipboard had nowhere to go. Text pastes are
          // untouched — the handler returns unless the clipboard holds files.
          onPaste={onPasteFiles}
          placeholder={
            recording && pushToTalk.current
              ? t('듣고 있습니다. 스페이스를 떼면 보냅니다')
              : transcribing
                ? t('받아쓰는 중…')
                : // A proposal is waiting, so this box is not where a new document
                  // starts — it is where this one gets adjusted. Saying so is what
                  // stops somebody typing a question and watching a deck appear.
            pending
              ? pending.stage === 'clarify'
                ? t('답을 적거나, 위에서 고르세요')
                : t('고칠 곳을 적어 주세요. 그대로 좋으면 위 버튼을 누르세요')
              : // What this 시작점 needs, rather than what the surface generally
                // does — the half of the template the person still has to supply.
                startingTemplate && startingTemplate.fills.length > 0
                ? t('덧붙일 말이 있으면 여기에 적으세요')
                : t(placeholders[kind])
          }
          aria-label={t('프롬프트 입력')}
          data-composer=""
          className="w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-md leading-relaxed text-fg placeholder:text-faint focus:outline-none"
        />

        {/* Wraps rather than squeezing. On a phone this row is wider than the
            screen, and flex answered that by shrinking the buttons — the
            attachment control ended up 16px across, narrower than the icon
            inside it. */}
        <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
          {/* Not on the picture and clip surfaces. `generateImages`,
              `generateAudio` and `generateVideo` send a prompt and the option
              chips; there is no field on those endpoints an upload could ride
              in, and a paperclip that takes a file only to drop it at submit
              costs the person a wait and the clip's credits before they find
              out. */}
          {!isMedia && (
            <>
              <input
                ref={fileInput}
                type="file"
                multiple
                className="hidden"
                aria-label={t('파일 선택')}
                onChange={(e) => {
                  const picked = Array.from(e.target.files ?? [])
                  // Reset immediately so picking the same file twice still fires.
                  e.target.value = ''
                  void addFiles(picked)
                }}
              />
              <button
                onClick={() => fileInput.current?.click()}
                className="grid size-9 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg"
                aria-label={t('첨부')}
                title={t('파일을 올려 답변의 근거로 씁니다')}
              >
                <Paperclip size={16} />
              </button>
            </>
          )}
          {canRecord && (
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
                  : `${t('마이크로 말하면 글로 받아 적습니다. 보내기 전에 고칠 수 있습니다. 빈 입력창에서 스페이스를 누른 채 말하면 떼는 순간 보냅니다')} (${isMac() ? '⌘' : 'Ctrl'}+Shift+M)`
              }
            >
              {transcribing ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Mic size={16} className={recording ? 'animate-pulse' : undefined} />
              )}
            </button>
          )}

          {/* Only once the conversation has started. A shape you can only
              pick before the first turn is one you cannot change your mind
              about — and until that turn the empty screen is offering the same
              thing two inches above, which is clutter, not reassurance. */}
          {hasTemplates && started && (
            <button
              onClick={() => setGalleryOpen(true)}
              className={cn(
                'grid size-9 shrink-0 place-items-center rounded-control transition-colors hover:bg-elevated hover:text-fg',
                shownTemplate ? 'text-accent' : 'text-muted',
              )}
              aria-label={t('작업 시작하기')}
              title={t('업무 시작점이나 결과 서식을 고릅니다')}
            >
              <LayoutGrid size={16} />
            </button>
          )}

          {/* Same rule as the attachment: a skill is a block of context the
              chat pipeline assembles, and a picture model is never handed one.
              Offering the list here would let somebody spend the choice on a
              turn that cannot use it. */}
          {!isMedia && usableSkills.length > 0 && (
            <Dropdown
              className="min-w-64"
              trigger={() => (
                <button
                  // Icon-only, so it needs an accessible name — and a title,
                  // because the name alone says what the button is and not
                  // what pressing it does. Every other control on this bar
                  // carries both.
                  aria-label={t('스킬')}
                  title={t('이번 요청에만 적용할 스킬을 고릅니다')}
                  className={cn(
                    'flex h-9 shrink-0 items-center gap-1.5 rounded-control px-2.5 text-base transition-colors hover:bg-elevated',
                    activeSkills.length ? 'text-accent' : 'text-muted hover:text-fg',
                  )}
                >
                  <Sparkles size={15} />
                  {activeSkills.length > 0 && <span>{activeSkills.length}</span>}
                </button>
              )}
            >
              <MenuLabel>{t('이번 요청에 적용할 스킬 (최대 3개)')}</MenuLabel>
              {usableSkills.map((s) => {
                const selected = activeSkills.some((skill) => skill.id === s.id)
                const unavailable = skillUnavailableReason(s)
                const limitReached = !selected && activeSkills.length >= 3
                return (
                  <MenuItem
                    key={s.id}
                    checked={selected}
                    keepOpen
                    disabled={!!unavailable || limitReached}
                    // The limit is one sentence for the whole menu (the label
                    // says 최대 3개) and a tooltip on the rows it disables —
                    // not a hint that took the row's width and cut the name
                    // down to 수치에 ….
                    title={limitReached ? t('최대 3개까지 선택할 수 있습니다.') : undefined}
                    // The figure stays once the row is chosen — that is when it
                    // is being spent. The mark is drawn by the row itself.
                    hint={
                      unavailable
                        ? unavailable
                        : recommended.has(s.id)
                          ? t('프로젝트 추천')
                          : t('약 {n} 토큰').replace('{n}', s.estimatedTokens.toLocaleString())
                    }
                    onClick={() => {
                      activeRestoreToken.current = null
                      const next = selected
                        ? activeSkills
                            .filter((skill) => skill.id !== s.id)
                            .map((skill) => skill.id)
                        : [...activeSkills.map((skill) => skill.id), s.id]
                      liveActivatedSkillIds.current = next
                      setActivatedSkillIds(next)
                    }}
                  >
                    {s.name}
                  </MenuItem>
                )
              })}
              <MenuSeparator />
              <MenuItem icon={<Plus size={14} />} onClick={() => navigate('/skills')}>
                {t('스킬 관리')}
              </MenuItem>
            </Dropdown>
          )}

          {kind === 'chat' && (
            <Dropdown
              className="min-w-72"
              trigger={() => (
                <button
                  className={cn(
                    'flex h-9 shrink-0 items-center gap-1.5 rounded-control px-2.5 text-base transition-colors hover:bg-elevated',
                    compareMode ? 'text-accent' : 'text-muted hover:text-fg',
                  )}
                  aria-label={t('모델 비교')}
                  title={t('같은 질문을 여러 모델에 동시에 보내고 답변을 나란히 비교합니다')}
                >
                  <Columns2 size={15} />
                  {compareMode && <span>{compareModels.length}</span>}
                </button>
              )}
            >
              <MenuLabel>{t('모델 비교')}</MenuLabel>
              <MenuItem
                icon={<Columns2 size={14} />}
                hint={compareMode ? t('켜짐') : t('꺼짐')}
                onClick={toggleCompareMode}
              >
                {t('비교 모드')}
              </MenuItem>
              <MenuSeparator />
              <MenuLabel>{t('비교할 모델 (2~3개)')}</MenuLabel>
              {models
                .filter((m) => m.kinds.includes('chat'))
                .map((m) => (
                  <MenuItem
                    key={m.id}
                    checked={compareModels.includes(m.id)}
                    hint={t('출력 1k당 {n} 크레딧').replace('{n}', m.creditCost.toLocaleString())}
                    onClick={() => toggleCompareModel(m.id)}
                    keepOpen
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="truncate">{m.label}</span>
                      {m.strictLocal ? (
                        <Badge tone="success">
                          <ShieldCheck size={10} />
                          strict-local
                        </Badge>
                      ) : (
                        <Badge tone="warn">
                          {t(
                            m.dataBoundary === 'hybrid'
                              ? '외부 전환 가능'
                              : m.dataBoundary === 'external'
                                ? '외부 제공'
                                : m.dataBoundary === 'self_hosted'
                                  ? 'self-hosted · strict 미확인'
                                  : '경계 미확인',
                          )}
                        </Badge>
                      )}
                    </span>
                  </MenuItem>
                ))}
            </Dropdown>
          )}

          {canWebSearch && (
            <button
              onClick={() => setWebSearch((w) => !w)}
              aria-pressed={effectiveWebSearch}
              disabled={searchBlocked}
              className={cn(
                'flex h-9 shrink-0 items-center gap-1.5 rounded-control px-2.5 text-base transition-colors hover:bg-elevated',
                effectiveWebSearch ? 'text-accent' : 'text-muted hover:text-fg',
                searchBlocked && 'opacity-55 hover:bg-transparent hover:text-muted',
              )}
              aria-label={t('웹 검색')}
              title={
                searchBlocked
                  ? compareMode && kind === 'chat'
                    ? t('모델 비교는 웹 검색 없이 실행합니다')
                    : t('이 모델은 외부에 연결하지 않아 웹 검색을 쓸 수 없습니다')
                  : t('웹에서 최신 자료를 찾아 근거로 씁니다')
              }
            >
              <Globe size={15} />
            </button>
          )}


          {usableConnectors.length > 0 && (
            <Dropdown
              className="min-w-72"
              trigger={() => (
                <button
                  className={cn(
                    'flex h-9 shrink-0 items-center gap-1.5 rounded-control px-2.5 text-base transition-colors hover:bg-elevated',
                    activeConnectors.length ? 'text-accent' : 'text-muted hover:text-fg',
                  )}
                  /* With the count on the face of it and 커넥터 in the label,
                     the accessible name has to hold both — otherwise the
                     button says 25 and answers to something else. */
                  aria-label={
                    activeConnectors.length
                      ? t('커넥터 {n}개').replace('{n}', String(activeConnectors.length))
                      : t('커넥터')
                  }
                >
                  <Plug size={15} />
                  {activeConnectors.length > 0 && <span>{activeConnectors.length}</span>}
                </button>
              )}
            >
              <MenuLabel>{t('커넥터')}</MenuLabel>
              {/* The one control in this toolbar that is not about the turn
                  being written. Its neighbours all reset at the next message;
                  this one writes the account, so it has to say so before it is
                  clicked rather than after a connector goes missing elsewhere. */}
              <p className="px-2.5 pb-1.5 text-xs text-faint">
                {t('계정 전체 설정입니다. 여기서 끄면 모든 대화에서 꺼집니다.')}
              </p>
              {usableConnectors.map((c) => (
                <MenuItem
                  key={c.id}
                  icon={<span className="text-base">{c.icon}</span>}
                  hint={c.status === 'connected' ? (c.enabled ? t('켜짐') : t('꺼짐')) : t('인증 필요')}
                  onClick={() => c.status === 'connected' && toggleConnector(c.id)}
                >
                  {c.name}
                </MenuItem>
              ))}
              <MenuSeparator />
              <MenuItem icon={<Plus size={14} />} onClick={() => navigate('/connectors')}>
                {t('커넥터 관리')}
              </MenuItem>
            </Dropdown>
          )}

          {usableAgents.length > 0 && (
            <Dropdown
              className="min-w-64"
              trigger={() => (
                <button
                  className="flex h-9 min-w-9 shrink-0 items-center justify-center gap-1.5 rounded-control px-2.5 text-base text-muted transition-colors hover:bg-elevated hover:text-fg"
                  title={t('에이전트를 골라 새 대화를 시작합니다')}
                >
                  @
                </button>
              )}
            >
              <MenuLabel>{t('에이전트로 새 대화')}</MenuLabel>
              {usableAgents.map((a) => (
                <MenuItem
                  key={a.id}
                  hint={a.model ? a.model.split('/').pop() : undefined}
                  onClick={() =>
                    // An agent belongs to the session, not one message, so
                    // choosing one starts a conversation.
                    void newSession(kind, { agentId: a.id, projectId })
                      .then((id) => navigate(`/s/${id}`))
                      .catch((err: unknown) => setNotice(startFailure(err, t)))
                  }
                >
                  {a.name}
                </MenuItem>
              ))}
            </Dropdown>
          )}

          <div className="ml-auto flex items-center gap-1">
            {!(compareMode && kind === 'chat') && (
              <ModelPicker
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
            )}
            <button
              onClick={streaming && sessionId ? () => stopStreaming(sessionId) : () => submit()}
              disabled={(!value.trim() && !startingFilled && !streaming) || modelSelectionPending}
              className={cn(
                'grid size-9 place-items-center rounded-full transition-colors',
                streaming
                  ? 'bg-elevated text-fg'
                  : 'bg-accent text-accent-fg hover:bg-accent-hover disabled:bg-elevated disabled:text-faint',
              )}
              aria-label={streaming ? t('중지') : t('전송')}
              title={
                streaming
                  ? t('생성을 멈춥니다')
                  : modelSelectionPending
                    ? t('모델 설정 저장 중…')
                  : !value.trim() && !startingFilled
                    ? t('보낼 내용을 먼저 입력하세요')
                    : unsupportedVideo
                      ? t('이 모델은 이 조합을 만들지 않습니다')
                      : t('Enter 로도 보낼 수 있습니다')
              }
            >
              {streaming ? <Square size={13} fill="currentColor" /> : <ArrowUp size={16} />}
            </button>
          </div>
        </div>
      </div>
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
      <p className="mt-2 text-center text-xs text-faint">
        {busy && isMedia
            ? t('생성 중입니다 — 완료되면 위 카드가 결과로 바뀝니다')
            : kind === 'image'
              ? t('Enter 로 생성 · 만든 그림은 아티팩트에 쌓입니다')
              : kind === 'av' && avOptions.mode === 'audio'
                ? t('Enter 로 생성 · 음성과 음악은 아티팩트에 쌓입니다')
                : kind === 'av'
                  ? t('Enter 로 생성 · 영상은 몇 분 걸리고 진행은 카드에 표시됩니다')
                  : kind === 'slides'
                    ? t('Enter 로 생성 · 구성을 잡은 뒤 한 장씩 채웁니다')
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
                      pendingPrivacy.startingTemplate,
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
                    pendingPrivacy.startingTemplate,
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
                      pendingPrivacy.startingTemplate,
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
            {pendingPrivacy.decision.requestedModels.length > 1 &&
              pendingPrivacy.decision.allowedActions.includes('route_strict_local') && (
                <p className="text-sm text-muted">
                  {t('모델 비교는 아직 시작되지 않았습니다. 안전 모델을 선택하면 비교 대신 strict-local 모델 1개로 실행합니다.')}
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
