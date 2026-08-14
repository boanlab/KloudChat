import {
  ArrowUp,
  Boxes,
  Columns2,
  Globe,
  Paperclip,
  Plug,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Sparkles,
  Square,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FileRow } from '@/lib/api'
import { transcribe } from '@/lib/api'
import { useNavigate } from 'react-router-dom'
import { Badge, Dropdown, MenuItem, MenuLabel, MenuSeparator } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { ModelPicker } from './ModelPicker'
import { useT } from '@/lib/useT'

//: One verb ending (`~세요`) across all five surfaces. They sit next to each
//: other, so a mix of endings is visible.
const placeholders: Record<SessionKind, string> = {
  chat: '무엇이든 물어보세요',
  report: '보고서 주제와 넣고 싶은 절을 적으세요',
  slides: '발표 주제와 시간을 적으세요. 예: 자기지도 학습, 15분, 학부생 대상',
  image: '만들고 싶은 이미지를 설명하세요',
  av: '만들고 싶은 영상이나 오디오를 설명하세요',
}

const ASPECTS = ['1:1', '16:9', '9:16', '4:3']
const STYLES = ['미니멀', '사진', '일러스트', '3D 렌더', '수채화', '없음']
const VIDEO_DURATIONS = [4, 6, 8, 10]
const AUDIO_DURATIONS = [15, 30, 60, 120]
// Speech and music only: nothing serves sound effects, and an option that can
// only fail is worse than no option.
const AUDIO_KINDS = ['narration', 'music'] as const
const AUDIO_KIND_LABEL: Record<(typeof AUDIO_KINDS)[number], string> = {
  narration: '내레이션',
  music: '음악',
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
            'flex h-7 items-center gap-1.5 rounded-lg border border-line px-2 text-[12px] transition-colors',
            open ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
          )}
        >
          <span className="text-faint">{label}</span>
          <span className="font-medium">{format ? format(value) : value}</span>
        </button>
      )}
    >
      {options.map((o) => (
        <MenuItem key={String(o)} onClick={() => onChange(o)} hint={o === value ? '✓' : undefined}>
          {format ? format(o) : String(o)}
        </MenuItem>
      ))}
    </Dropdown>
  )
}

function ImageOptions() {
  const t = useT()
  const { imageOptions, setImageOptions } = useStore()
  return (
    <>
      <OptionGroup
        label={t('비율')}
        value={imageOptions.aspect}
        options={ASPECTS}
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
        label={t('장수')}
        value={imageOptions.count}
        options={[1, 2, 4]}
        onChange={(v) => setImageOptions({ count: v })}
        format={(v) => t('{n}장').replace('{n}', String(v))}
      />
    </>
  )
}

/**
 * One surface, two modalities. `mode` comes first because it decides which of
 * the remaining chips apply — aspect ratio means nothing to a narration track.
 */
function AvOptions() {
  const t = useT()
  const { avOptions, setAvOptions } = useStore()
  const audio = avOptions.mode === 'audio'
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
        <OptionGroup
          label={t('유형')}
          value={avOptions.audioKind}
          options={AUDIO_KINDS}
          onChange={(v) => setAvOptions({ audioKind: v })}
          format={(v) => t(AUDIO_KIND_LABEL[v])}
        />
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
    </>
  )
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
  const [value, setValue] = useState('')
  //: idle → 'recording' while the mic is open, 'working' while Whisper reads it.
  const [dictation, setDictation] = useState<'off' | 'recording' | 'working'>('off')
  const [dictationError, setDictationError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)

    /**
     * Records, then transcribes through this instance's own Whisper.
     *
     * Not `webkitSpeechRecognition`, which streams the microphone to a third
     * party. The transcript fills the composer rather than being sent —
     * dictation is a way of typing, not of submitting.
     */
  const toggleDictation = async () => {
    if (dictation === 'recording') {
      recorder.current?.stop()
      return
    }
    if (dictation === 'working') return
    setDictationError(null)
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setDictationError(t('마이크를 쓸 수 없습니다. 브라우저 권한을 확인해 주세요.'))
      return
    }
    const chunks: Blob[] = []
    const rec = new MediaRecorder(stream)
    recorder.current = rec
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    rec.onstop = async () => {
      // Released before the round trip, or the browser's recording indicator
      // stays lit.
      stream.getTracks().forEach((t) => t.stop())
      setDictation('working')
      try {
        const text = await transcribe(new Blob(chunks, { type: rec.mimeType }))
        setValue((v) => (v ? `${v.replace(/\s*$/, '')} ${text}` : text))
        ref.current?.focus()
      } catch (err) {
        setDictationError(err instanceof Error ? err.message : t('받아쓰지 못했습니다.'))
      } finally {
        setDictation('off')
      }
    }
    rec.start()
    setDictation('recording')
  }
  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  // A template is inserted, not sent: the cursor lands where the user takes
  // over.
  useEffect(() => {
    if (!draft) return
    setValue(draft)
    setDraft('')
    const el = ref.current
    if (el) {
      el.focus()
      requestAnimationFrame(() => el.setSelectionRange(el.value.length, el.value.length))
    }
  }, [draft, setDraft])
  /** Uploaded files, not names: the turn sends ids and the server reads the text. */
  const [attachments, setAttachments] = useState<FileRow[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const [webSearch, setWebSearch] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const navigate = useNavigate()
  const {
    send,
    stopStreaming,
    streaming,
    skills,
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
    jobs,
    uploadFile,
    newSession,
    toggleSkill,
    generateImages,
    generateAudio,
    generateVideo,
    avOptions,
    dictationEnabled,
  } = useStore()

  const project = projects.find((p) => p.id === projectId)
  const usableSkills = skills.filter((s) => s.kinds.length === 0 || s.kinds.includes(kind))
  const activeSkills = usableSkills.filter((s) => s.enabled)
  // Empty `kinds` means every surface, the same rule skills and tool
  // allowlists use.
  const usableAgents = agents.filter(
    (a) => a.enabled && (a.kinds.length === 0 || a.kinds.includes(kind)),
  )
  const usableConnectors = connectors.filter(
    (c) => c.installed && (c.kinds.length === 0 || c.kinds.includes(kind)),
  )
  const activeConnectors = usableConnectors.filter((c) => c.enabled && c.status === 'connected')
  const isMedia = kind === 'image' || kind === 'av'
  const model = models.find((m) => m.id === modelByKind[kind])
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
  const busy = isMedia ? jobRunning : streaming

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 280)}px`
  }, [value])

  const submit = () => {
    const text = value.trim()
    if (!text || busy || unsupportedVideo) return
    const attachmentIds = attachments.map((f) => f.id)
    const attachmentLabels = attachments.map((f) => f.name)
    // Clear the composer first: the session is created server-side, so awaiting
    // the round trip would leave the sent text sitting in the box.
    setValue('')
    setAttachments([])
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
      // Not a conversation turn: the result is an artifact, and `send` would
      // put an empty assistant bubble above the gallery.
      void generateImages(sessionId, text, {
        projectId,
        onSession: (id) => navigate(`/s/${id}`, { replace: true }),
      })
      return
    }
    void send(sessionId, kind, text, {
      projectId,
      webSearch,
      attachments: attachmentIds,
      attachmentNames: attachmentLabels,
      // Sending from /new/:kind creates a session; the URL has to follow it.
      onSession: (id) => navigate(`/s/${id}`, { replace: true }),
    })
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="rounded-2xl border border-line bg-panel shadow-sm transition-colors focus-within:border-line-strong">
        {(project || attachments.length > 0 || webSearch || (compareMode && kind === 'chat')) && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
            {compareMode && kind === 'chat' && (
              <Badge tone="accent">
                <Columns2 size={11} />
                {compareModels
                  .map((id) => models.find((m) => m.id === id)?.label ?? id)
                  .join(' vs ')}
              </Badge>
            )}
            {webSearch && (
              <Badge tone="accent">
                <Globe size={11} />
                {t('웹 검색')}
              </Badge>
            )}
            {project && (
              <Badge tone="accent">
                <Boxes size={11} />
                {project.emoji} {project.name}
              </Badge>
            )}
            {attachments.map((f) => (
              <span
                key={f.id}
                className={cn(
                  'flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px]',
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
                  onClick={() => setAttachments((a) => a.filter((x) => x.id !== f.id))}
                  className="text-faint hover:text-fg"
                  aria-label={t('{name} 제거').replace('{name}', f.name)}
                >
                  <X size={10} />
                </button>
              </span>
            ))}
            {uploading && (
              <span className="flex items-center gap-1.5 rounded-md border border-line bg-elevated px-1.5 py-0.5 text-[11px] text-faint">
                <Loader2 size={10} className="animate-spin" />
                {t('업로드 중')}
              </span>
            )}
          </div>
        )}

        {/* 생성 파라미터 바 — 이미지·오디오/동영상 전용 */}
        {isMedia && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-2.5 py-2">
            {kind === 'image' ? <ImageOptions /> : <AvOptions />}
            <span
              className={cn(
                'ml-auto pr-1 text-[11px]',
                unsupportedVideo ? 'text-warn' : 'text-faint',
              )}
            >
              {unsupportedVideo
                ? t('이 모델은 이 조합을 만들지 않습니다')
                : t('예상 {n} 크레딧').replace('{n}', estimate.toLocaleString())}
            </span>
          </div>
        )}

        <textarea
          ref={ref}
          autoFocus={autoFocus}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={t(placeholders[kind])}
          aria-label={t('프롬프트 입력')}
          className="w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] leading-relaxed text-fg placeholder:text-faint focus:outline-none"
        />

        <div className="flex items-center gap-1 px-2 pb-2">
          <input
            ref={fileInput}
            type="file"
            multiple
            className="hidden"
            aria-label={t('파일 선택')}
            onChange={async (e) => {
              const picked = Array.from(e.target.files ?? [])
              // Reset immediately so picking the same file twice still fires.
              e.target.value = ''
              if (!picked.length) return
              setUploading(true)
              try {
                for (const file of picked) {
                  const row = await uploadFile(file, {
                    projectId: projectId ?? undefined,
                    sessionId: sessionId ?? undefined,
                  }).catch(() => null)
                  if (row) setAttachments((a) => [...a, row])
                }
              } finally {
                setUploading(false)
              }
            }}
          />
          <button
            onClick={() => fileInput.current?.click()}
            className="grid size-8 place-items-center rounded-lg text-muted transition-colors hover:bg-elevated hover:text-fg"
            aria-label={t('첨부')}
          >
            <Paperclip size={16} />
          </button>

          {usableSkills.length > 0 && (
            <Dropdown
              className="min-w-64"
              trigger={() => (
                <button
                  // Icon-only, so it needs an accessible name.
                  aria-label={t('스킬')}
                  className={cn(
                    'flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] transition-colors hover:bg-elevated',
                    activeSkills.length ? 'text-accent' : 'text-muted hover:text-fg',
                  )}
                >
                  <Sparkles size={15} />
                  {activeSkills.length > 0 && <span>{activeSkills.length}</span>}
                </button>
              )}
            >
              <MenuLabel>{t('이 화면에서 쓸 수 있는 스킬')}</MenuLabel>
              {usableSkills.map((s) => (
                <MenuItem
                  key={s.id}
                  hint={s.enabled ? t('켜짐') : t('꺼짐')}
                  onClick={() => void toggleSkill(s.id)}
                >
                  {s.name}
                </MenuItem>
              ))}
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
                    'flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] transition-colors hover:bg-elevated',
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
                    hint={compareModels.includes(m.id) ? '✓' : `${m.creditCost}`}
                    onClick={() => toggleCompareModel(m.id)}
                  >
                    {m.label}
                  </MenuItem>
                ))}
            </Dropdown>
          )}

          {!isMedia && (
            <button
              onClick={() => setWebSearch((w) => !w)}
              aria-pressed={webSearch}
              className={cn(
                'flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] transition-colors hover:bg-elevated',
                webSearch ? 'text-accent' : 'text-muted hover:text-fg',
              )}
              aria-label={t('웹 검색')}
              title={t('웹에서 최신 자료를 찾아 근거로 씁니다')}
            >
              <Globe size={15} />
            </button>
          )}


          {dictationEnabled && (
            <button
              onClick={() => void toggleDictation()}
              aria-pressed={dictation === 'recording'}
              aria-label={t('음성 입력')}
              title={dictation === 'recording' ? t('멈추고 받아쓰기') : t('말한 내용을 받아 적습니다')}
              disabled={dictation === 'working'}
              className={cn(
                'flex h-8 items-center gap-1.5 rounded-lg px-2 transition-colors hover:bg-elevated',
                dictation === 'recording' ? 'text-danger' : 'text-muted hover:text-fg',
                dictation === 'working' && 'opacity-60',
              )}
            >
              {dictation === 'working' ? (
                <Loader2 size={15} className="animate-spin" />
              ) : dictation === 'recording' ? (
                <MicOff size={15} />
              ) : (
                <Mic size={15} />
              )}
            </button>
          )}

          {usableConnectors.length > 0 && (
            <Dropdown
              className="min-w-72"
              trigger={() => (
                <button
                  className={cn(
                    'flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] transition-colors hover:bg-elevated',
                    activeConnectors.length ? 'text-accent' : 'text-muted hover:text-fg',
                  )}
                  aria-label={t('커넥터')}
                >
                  <Plug size={15} />
                  {activeConnectors.length > 0 && <span>{activeConnectors.length}</span>}
                </button>
              )}
            >
              <MenuLabel>{t('커넥터')}</MenuLabel>
              {usableConnectors.map((c) => (
                <MenuItem
                  key={c.id}
                  icon={<span className="text-[13px]">{c.icon}</span>}
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
                <button className="flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] text-muted transition-colors hover:bg-elevated hover:text-fg">
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
                    void newSession(kind, { agentId: a.id, projectId }).then((id) =>
                      navigate(`/s/${id}`),
                    )
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
                sessionId={sessionId}
                modality={kind === 'av' ? (avOptions.mode === 'video' ? 'video' : 'audio') : undefined}
              />
            )}
            <button
              onClick={streaming ? stopStreaming : submit}
              disabled={!value.trim() && !streaming}
              className={cn(
                'grid size-8 place-items-center rounded-lg transition-colors',
                streaming
                  ? 'bg-elevated text-fg'
                  : 'bg-accent text-accent-fg hover:bg-accent-hover disabled:bg-elevated disabled:text-faint',
              )}
              aria-label={streaming ? t('중지') : t('전송')}
            >
              {streaming ? <Square size={13} fill="currentColor" /> : <ArrowUp size={16} />}
            </button>
          </div>
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-faint">
        {dictationError
          ? dictationError
          : dictation === 'recording'
            ? t('듣고 있습니다 — 다시 누르면 받아 적습니다')
            : dictation === 'working'
              ? t('받아 적는 중…')
              : busy && isMedia
            ? t('생성 중입니다 — 완료되면 위 카드가 결과로 바뀝니다')
            : kind === 'image'
              ? t('Enter 로 생성 · 만든 그림은 아티팩트에 쌓입니다')
              : kind === 'av' && avOptions.mode === 'audio'
                ? t('Enter 로 생성 · 음성과 음악은 아티팩트에 쌓입니다')
                : kind === 'av'
                  ? t('Enter 로 생성 · 영상은 몇 분 걸리고 진행은 카드에 표시됩니다')
                  : kind === 'slides'
                    ? t('Enter 로 생성 · 구성을 잡은 뒤 한 장씩 채웁니다')
                    : t('Enter 전송, Shift+Enter 줄바꿈')}
      </p>
    </div>
  )
}
