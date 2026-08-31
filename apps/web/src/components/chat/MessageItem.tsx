import {
  AudioLines,
  BarChart3,
  Check,
  CircleStop,
  Copy,
  Download,
  FileText,
  Image as ImageIcon,
  Paperclip,
  Presentation,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  Video,
} from 'lucide-react'
import { memo, useState } from 'react'
import { Badge, Button } from '@/components/ui'
import { MediaResult } from '@/components/media/MediaResult'
import { downloadFile, errorMessage, templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { FINDING_LABEL } from '@/lib/privacy'
import { cn, fileSize, formatTokens, isMedia } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { ArtifactKind, CostRouting, Message, ModelInfo } from '@/types'
import { CompareView } from './CompareView'
import { Markdown } from './Markdown'
import { RetryActions } from './RetryActions'
import { StepTimeline } from './StepTimeline'
import { TurnProgress } from './TurnProgress'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

const artifactIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: FileText,
  html: FileText,
}

const artifactLabel: Record<ArtifactKind, string> = {
  report: '보고서',
  deck: '슬라이드',
  chart: '차트',
  image: '이미지',
  audio: '오디오',
  video: '동영상',
  code: '코드',
  html: 'HTML',
}

function costRouteDecisionLabel(
  route: CostRouting,
  t: (text: string) => string,
): string {
  if (route.decision === 'routed') {
    return t('Auto 절약')
  }
  if (route.decision === 'classifier_unavailable') {
    return t('Auto · 분류기를 사용할 수 없어 품질 모델 유지')
  }
  if (route.decision === 'bypassed') {
    if (route.reasonCode === 'privacy_detected') {
      return t('Auto · 개인정보 감지로 난이도 판정 생략')
    }
    if (route.reasonCode === 'unsupported_turn') {
      return t('Auto · 기능 사용으로 품질 모델 유지')
    }
    if (route.reasonCode === 'disabled') {
      return t('Auto · 관리 정책이 꺼져 품질 모델 유지')
    }
    if (route.reasonCode === 'no_economy_model' || route.reasonCode === 'no_economy_models') {
      return t('Auto · 사용할 절약 모델이 없어 품질 모델 유지')
    }
    return t('Auto · 난이도 판정을 생략하고 품질 모델 유지')
  }
  if (route.reasonCode === 'high_complexity') {
    return t('Auto · 복잡한 요청으로 품질 모델 유지')
  }
  if (route.reasonCode === 'input_too_long') {
    return t('Auto · 긴 대화이므로 품질 모델 유지')
  }
  if (route.reasonCode === 'no_economy_model' || route.reasonCode === 'no_economy_models') {
    return t('Auto · 사용할 절약 모델이 없어 품질 모델 유지')
  }
  return t('Auto · 확실하지 않아 품질 모델 유지')
}

function modelPresentation(
  id: string | undefined,
  models: ModelInfo[],
  t: (text: string) => string,
): { label: string; detail: string } {
  if (!id) {
    const pending = t('확인 중…')
    return { label: pending, detail: pending }
  }
  const label = models.find((model) => model.id === id)?.label ?? id
  return {
    label,
    detail: label === id ? id : `${label} (${id})`,
  }
}

function costRoutePresentation(
  route: CostRouting,
  models: ModelInfo[],
  t: (text: string) => string,
): { label: string; title: string } {
  const requested = modelPresentation(route.requestedModel, models, t)
  const selected = modelPresentation(route.selectedModel, models, t)
  const executed = modelPresentation(route.executedModel, models, t)
  const decision = costRouteDecisionLabel(route, t)
  const saved = route.estimatedCreditsSaved
    ? ` · ${t('예상 {n} 크레딧 절약').replace('{n}', route.estimatedCreditsSaved.toLocaleString())}`
    : ''
  const visibleModels = [
    `${t('요청 모델')}: ${requested.label}`,
    `${t('선택 모델')}: ${selected.label}`,
    `${t('실행 모델')}: ${executed.label}`,
  ].join(' · ')
  const detailedModels = [
    `${t('요청 모델')}: ${requested.detail}`,
    `${t('선택 모델')}: ${selected.detail}`,
    `${t('실행 모델')}: ${executed.detail}`,
  ].join(' · ')
  return {
    label: `${decision} · ${visibleModels}${saved}`,
    title: detailedModels,
  }
}

/**
 * How a turn ended, when it did not end in an answer.
 *
 * `error` is this tab's live account and wins while it is there; `failure` is
 * what the server recorded, and the only thing left after a reload. The words
 * follow the surface, because "답변을 받지 못했습니다" is not what happened when
 * a picture was asked for.
 */
function turnFailureNotice(
  message: Message,
  media: boolean,
  t: (text: string) => string,
): string | undefined {
  if (message.error) return message.error
  if (message.failure === 'stopped') {
    return media ? t('요청한 만큼 만들어지지 않았습니다.') : t('여기서 멈췄습니다.')
  }
  if (message.failure === 'interrupted') {
    return media
      ? t('요청한 만큼 만들어지지 않았습니다.')
      : t('답변이 중간에 끊겨 여기까지만 남았습니다.')
  }
  if (message.failure === 'no_answer') {
    return media ? t('만들지 못했습니다.') : t('답변을 받지 못했습니다.')
  }
  return undefined
}

/**
 * One turn of the transcript.
 *
 * Memoised, and reading the store through selectors, for the same reason: a
 * streamed delta patches one message, and everything else in the conversation
 * must not re-render — and re-parse its markdown — for it. Subscribing to the
 * whole store (`useStore()`) re-rendered every row on every chunk, and the
 * `session` row it read changed identity on every chunk too, so a memo alone
 * would not have held. The reads below are primitives or rows that only
 * change when they change.
 */
function MessageItemInner({
  message,
  sessionId,
  streaming,
}: {
  message: Message
  sessionId: string
  streaming?: boolean
}) {
  const t = useT()
  const artifacts = useStore((s) => s.artifacts)
  const openArtifact = useStore((s) => s.openArtifact)
  const rateMessage = useStore((s) => s.rateMessage)
  const retryMediaTurn = useStore((s) => s.retryMediaTurn)
  const models = useStore((s) => s.models)
  const user = useStore((s) => s.user)
  const designTemplates = useStore((s) => s.designTemplates)
  const sessionKind = useStore((s) => s.sessions.find((c) => c.id === sessionId)?.kind)
  const renderTemplateId = useStore(
    (s) => s.sessions.find((c) => c.id === sessionId)?.renderTemplateId,
  )
  /**
   * The question this answer was for.
   *
   * Read back off the transcript rather than carried on the message: an
   * assistant row does not know its prompt, and a retry that had to guess it
   * would ask the wrong thing. Selected as the row itself — the retry needs
   * its id — which keeps its identity until it changes.
   */
  const askedAbove = useStore((s) => {
    const list = s.sessions.find((c) => c.id === sessionId)?.messages ?? []
    const at = list.findIndex((m) => m.id === message.id)
    if (at < 0) return undefined
    for (let i = at - 1; i >= 0; i--) if (list[i].role === 'user') return list[i]
    return undefined
  })
  // The two surfaces whose answer is a thing rather than a sentence. They are
  // read differently at both ends of the turn: what a failure says, and what
  // stands where an answer would be.
  const madeHere = sessionKind === 'image' || sessionKind === 'av'
  const [copied, setCopied] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)

  /** Hands an attachment back to the person who attached it. */
  const take = async (id: string, name: string) => {
    setFileError(null)
    try {
      await downloadFile(id, name)
    } catch (err) {
      setFileError(errorMessage(err, t('파일을 내려받지 못했습니다.')))
    }
  }
  const model = models.find((m) => m.id === message.model)
  const actualModelChanged = Boolean(
    message.routing?.actualModel &&
      message.routing.actualModel !== message.routing.requestedModels[0],
  )
  const showRouting = Boolean(
    message.routing &&
      (message.routing.action !== 'none' || actualModelChanged || message.routing.costRouting),
  )
  const messageBoundary =
    model?.dataBoundary ??
    (message.routing?.dataBoundary !== 'mixed' ? message.routing?.dataBoundary : undefined)

  const copyButton = (label: string) => (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      onClick={async () => {
        if (!(await copyText(message.content))) return
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </Button>
  )

  if (message.role === 'user') {
    /**
     * What the turn was begun from, named rather than quoted.
     *
     * This is the line somebody reads a year later to remember what they did,
     * and it is the whole reason the framing stopped being typed into the box:
     * the prompt above it is theirs, and the machinery is here, separately, by
     * name. The 서식 comes off the session because it is sticky there; the
     * 시작점 is stored on the turn, because it was only ever about this one.
     */
    const shape = designTemplates.find((row) => row.id === renderTemplateId)
    const failed = turnFailureNotice(message, madeHere, t)
    const startedFrom = message.startedFrom
    // What the detector found in this sentence, which is also what the stored
    // copy no longer contains: routers/sessions.py writes the user's Message
    // masked whenever there is a finding, whichever action was accepted. The
    // bubble still holds the typed original until the session is reopened, so
    // the difference is said here rather than discovered a week later by
    // whoever presses 프롬프트 복사.
    const redacted = (message.routing?.findingCounts ?? []).filter(
      (finding) => finding.source === 'current_input',
    )
    return (
      // A prompt is worth copying as often as an answer is — to run again with
      // one word changed, to paste into a colleague's chat, to keep. It was
      // reachable only by selecting the text by hand.
      <div className="group animate-fade-up flex items-start justify-end gap-1">
        <span className="mt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {copyButton('프롬프트 복사')}
        </span>
        <div className="max-w-[80%] space-y-2">
          {startedFrom && (
            <p className="text-right text-xs text-faint">
              {shape
                ? t('시작점 {name} · 서식 {title}')
                    .replace('{name}', startedFrom.title)
                    .replace('{title}', templateText(shape, currentLang() === 'en').name)
                : t('시작점 {name}').replace('{name}', startedFrom.title)}
            </p>
          )}
          {message.attachments?.map((a) => {
            const bytes = typeof a.size === 'number' ? fileSize(a.size) : a.size
            const chip = (
              <>
                <Paperclip size={13} className="text-faint" />
                <span className="truncate">{a.name}</span>
                {bytes && <span className="shrink-0 text-faint">{bytes}</span>}
              </>
            )
            const shell =
              'ml-auto flex w-fit max-w-full items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-1.5 text-base'
            // The file is still on the server, under this id. Without a way
            // back to it the chip was a receipt for something the reader could
            // no longer have — it named a document and then kept it.
            return a.id ? (
              <button
                key={a.id}
                type="button"
                title={t('원본 파일을 내려받습니다')}
                className={`${shell} transition-colors hover:border-strong hover:bg-elevated`}
                onClick={() => void take(a.id!, a.name)}
              >
                {chip}
                <Download size={13} className="shrink-0 text-faint" />
              </button>
            ) : (
              <div key={a.name} className={shell}>
                {chip}
              </div>
            )
          })}
          {fileError && <p className="text-right text-sm text-danger">{fileError}</p>}
          <div className="rounded-panel rounded-br-md bg-elevated px-4 py-2.5 text-md leading-[1.7] whitespace-pre-wrap">
            {message.content}
          </div>
          {/* A request that came back with nothing says so here, under the
              sentence that asked. Nothing spoke, so there is no reply to put
              it in — and a conversation that ends on a prompt with silence
              beneath it is the state this whole surface was in. */}
          {failed && (
            <div
              role="status"
              className={cn(
                'flex items-center justify-end gap-2 text-base',
                // Stopped before the first token is still the reader's own
                // doing, and reads in the page's colours, not the error's.
                message.failure === 'stopped' && !message.error ? 'text-muted' : 'text-danger',
              )}
            >
              {message.failure === 'stopped' && !message.error ? (
                <CircleStop size={14} className="shrink-0" />
              ) : (
                <TriangleAlert size={14} className="shrink-0" />
              )}
              <span>{failed}</span>
              {/* A clip's job card has always had a retry; a conversation turn
                  had none, so the only way back from a question nobody
                  answered was to type it again. Asked again rather than
                  repaired: the turn that went unanswered stays in the record
                  beside the one that did not. */}
              {madeHere ? (
                <Button size="sm" onClick={() => void retryMediaTurn(sessionId, message.content)}>
                  <RotateCcw size={13} />
                  {t('다시 시도')}
                </Button>
              ) : (
                <RetryActions
                  sessionId={sessionId}
                  messageId={message.id}
                  prompt={message.content}
                  kind={sessionKind ?? 'chat'}
                />
              )}
            </div>
          )}
          {redacted.length > 0 && (
            <div className="space-y-1 text-right">
              <div className="flex flex-wrap justify-end gap-1.5">
                {redacted.map((finding) => (
                  <Badge key={finding.category} tone="warn">
                    <ShieldAlert size={10} />
                    {t(FINDING_LABEL[finding.category] ?? finding.category)} {finding.count}
                  </Badge>
                ))}
              </div>
              <p className="text-sm text-warn">
                {t('기록에는 가려진 채 저장됩니다. 이 대화를 다시 열면 여기에도 자리표시자만 남습니다.')}
              </p>
            </div>
          )}
        </div>
      </div>
    )
  }

  const costRoute = message.routing?.costRouting
  const costRouteDisplay = costRoute ? costRoutePresentation(costRoute, models, t) : null
  const linked = (message.artifactIds ?? [])
    .map((id) => artifacts.find((a) => a.id === id))
    .filter((a) => a !== undefined)
  // A picture, a clip or a player is shown; a document is named. The
  // difference is whether the artifact can be read where it stands: a report
  // cannot, and a chip that opens it is the honest offer.
  const shown = linked.filter(isMedia)
  const named = linked.filter((a) => !isMedia(a))
  const failed = turnFailureNotice(message, madeHere, t)
  const stopped = !message.error && message.failure === 'stopped'

  return (
    <div className="animate-fade-up group flex gap-3">
      <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-control bg-accent text-accent-fg">
        <Sparkles size={14} />
      </div>
      <div className="min-w-0 flex-1">
        {message.routing && showRouting && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {costRoute && costRouteDisplay && (
              <Badge
                tone={costRoute.decision === 'routed' ? 'success' : 'warn'}
                title={costRouteDisplay.title}
              >
                {costRouteDisplay.label}
              </Badge>
            )}
            {message.routing.action !== 'none' &&
              message.routing.initialAction === 'send_raw_external' &&
              message.routing.action !== 'send_raw_external' && (
                <Badge tone="warn">{t('확인 후 요청 원문은 외부 전송')}</Badge>
              )}
            {message.routing.action !== 'none' && (
              <Badge
                tone={message.routing.action === 'send_raw_external' ? 'warn' : 'success'}
              >
                {message.routing.action === 'route_strict_local' ||
                message.routing.action === 'strict_local'
                  ? t('strict-local로 보호됨')
                  : message.routing.action === 'mask_external'
                    ? t('개인정보를 가려 전송함')
                    : t('확인 후 외부 원문 전송')}
              </Badge>
            )}
            {messageBoundary && (
              <Badge tone={messageBoundary === 'self_hosted' ? 'success' : 'warn'}>
                {messageBoundary === 'self_hosted'
                  ? 'self-hosted'
                  : messageBoundary === 'hybrid'
                    ? t('외부 전환 가능')
                    : messageBoundary === 'external'
                      ? t('외부 제공')
                      : t('경계 미확인')}
              </Badge>
            )}
            {message.routing.toolOutputMasked ? (
              <Badge tone="warn">
                {t('도구 결과 {n}건 추가 마스킹').replace(
                  '{n}',
                  message.routing.toolOutputMasked.toLocaleString(),
                )}
              </Badge>
            ) : null}
            {actualModelChanged &&
              !message.routing.costRouting &&
              message.routing.actualModel && (
              <Badge title={message.routing.actualModel}>
                {t('실제 실행 모델')}: {message.routing.actualModel}
              </Badge>
              )}
          </div>
        )}
        {message.steps && message.steps.length > 0 && (
          <StepTimeline
            steps={message.steps}
            live={!!streaming}
            startedAt={new Date(message.createdAt).getTime()}
          />
        )}

        {message.variants ? (
          <CompareView
            variants={message.variants}
            sessionId={sessionId}
            messageId={message.id}
          />
        ) : message.content ? (
          <Markdown>{message.content}</Markdown>
        ) : (
          // Not while an error is showing: a failed turn with a blinking
          // "thinking…" under it reads as still running. Nor once the turn has
          // its answer in hand — on these surfaces the answer is the picture
          // below, and no sentence ever arrives to replace this line.
          !message.steps?.length &&
          !failed &&
          shown.length === 0 &&
          named.length === 0 && (
            <TurnProgress
              sessionId={sessionId}
              startedAt={new Date(message.createdAt).getTime()}
              /* 그림과 클립은 생각하는 게 아니라 만들어진다. */
              label={madeHere ? t('만드는 중…') : t('생각하는 중…')}
              model={message.model}
            />
          )
        )}
        {streaming && message.content && (
          <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-blink bg-accent" />
        )}

        {/* Where the answer goes, because here it is the answer. */}
        {shown.length > 0 && (
          <div className="mt-1">
            <MediaResult artifacts={shown} credits={message.usage?.credits ?? 0} />
          </div>
        )}

        {/* Below the answer, not instead of it. A turn that failed halfway has
            two things to say — what it managed to write or make, and that it
            stopped — and the reader needs both to decide whether to run it
            again. */}
        {failed && (
          <div
            role="status"
            className={cn(
              'mt-3 flex items-start gap-2 rounded-card border px-3 py-2.5 text-base',
              // A stop the reader chose is not an error, and must not look like
              // one: the same box, in the page's own colours.
              stopped
                ? 'border-line bg-elevated text-muted'
                : 'border-danger/30 bg-danger/5 text-danger',
            )}
          >
            {stopped ? (
              <CircleStop size={14} className="mt-0.5 shrink-0" />
            ) : (
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            )}
            <span className="min-w-0 flex-1">{failed}</span>
            {/* The retry lives here too, not only under the question. This is
                where the reader's eye already is when the turn fails, and the
                sentence that asked can be scrolled off the top of a long
                answer that broke halfway. */}
            {!madeHere && askedAbove && (
              <RetryActions
                sessionId={sessionId}
                messageId={askedAbove.id}
                prompt={askedAbove.content}
                kind={sessionKind ?? 'chat'}
              />
            )}
          </div>
        )}

        {named.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {named.map((a) => {
              const Icon = artifactIcon[a.kind]
              return (
                <button
                  key={a.id}
                  onClick={() => openArtifact(a.id)}
                  className="flex items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-left transition-colors hover:border-accent hover:bg-elevated"
                >
                  <span className="grid size-7 place-items-center rounded-control bg-accent-soft text-accent">
                    <Icon size={14} />
                  </span>
                  <span>
                    <span className="block text-base font-medium">{a.title}</span>
                    <span className="block text-xs text-faint">
                      {artifactLabel[a.kind]} · v{a.version}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {!streaming && message.content && !message.variants && (
          <div className="mt-2 flex items-center gap-1 text-faint">
            {/* Visible at rest.
                It used to appear only on hover, and the row holds 복사 — the
                thing people reach for most on an answer and the one they cannot
                reach for if finding it means sweeping a mouse across the text.
                The report panel settled this for its own edit button and wrote
                down why: a control that only hover reveals leaves "is this even
                possible" answerable only by accident.
                Muted rather than loud, so a page of turns does not become a
                page of buttons; a rating that has been given keeps its colour
                and reads back as the verdict it is. */}
            <span className="flex items-center gap-1">
            {copyButton(t('복사'))}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('좋아요')}
              aria-pressed={message.liked === 'up'}
              title={t('이 답변이 도움이 되었습니다')}
              className={cn(message.liked === 'up' && 'text-success')}
              onClick={() => void rateMessage(sessionId, message.id, 'up')}
            >
              <ThumbsUp size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('싫어요')}
              aria-pressed={message.liked === 'down'}
              title={t('이 답변이 잘못되었습니다')}
              className={cn(message.liked === 'down' && 'text-danger')}
              onClick={() => void rateMessage(sessionId, message.id, 'down')}
            >
              <ThumbsDown size={14} />
            </Button>
            </span>
            {/* Which model answered and what it cost. Kept visible rather than
                behind a hover: on a shared proxy with a monthly allowance, "what
                did that just cost me" is not a detail you go looking for. */}
            {message.usage && user?.preferences.showUsage !== false && (
              <span className="ml-1 text-xs">
                {model?.label ?? message.model} ·{' '}
                {/* An estimate is said to be one. */}
                {message.usage.estimated ? '≈ ' : ''}
                {formatTokens(message.usage.inputTokens)} in ·{' '}
                {formatTokens(message.usage.outputTokens)} out ·{' '}
                {message.usage.credits > 0 ? (
                  t('{n} 크레딧').replace('{n}', message.usage.credits.toLocaleString())
                ) : (
                  <span
                    title={
                      messageBoundary === 'self_hosted'
                        ? t('직접 운영하는 모델이라 크레딧이 차감되지 않습니다')
                        : messageBoundary === 'external'
                          ? t('외부 제공자가 무료로 제공하는 모델입니다')
                          : messageBoundary === 'hybrid'
                            ? t('자체 운영 경로지만 외부 모델로 전환될 수 있습니다')
                            : t('모델의 데이터 경계 또는 가격 정보를 확인할 수 없습니다')
                    }
                  >
                    {t('무료')}
                  </span>
                )}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export const MessageItem = memo(MessageItemInner)
