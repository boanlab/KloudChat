import {
  AudioLines,
  BarChart3,
  Check,
  Copy,
  FileText,
  Image as ImageIcon,
  Paperclip,
  Presentation,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  Video,
} from 'lucide-react'
import { useState } from 'react'
import { Badge, Button } from '@/components/ui'
import { cn, formatTokens } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { ArtifactKind, CostRouting, Message, ModelInfo } from '@/types'
import { CompareView } from './CompareView'
import { Markdown } from './Markdown'
import { StepTimeline } from './StepTimeline'
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

export function MessageItem({
  message,
  sessionId,
  streaming,
}: {
  message: Message
  sessionId: string
  streaming?: boolean
}) {
  const t = useT()
  const { artifacts, openArtifact, rateMessage, models, user } = useStore()
  const [copied, setCopied] = useState(false)
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
    return (
      // A prompt is worth copying as often as an answer is — to run again with
      // one word changed, to paste into a colleague's chat, to keep. It was
      // reachable only by selecting the text by hand.
      <div className="group animate-fade-up flex items-start justify-end gap-1">
        <span className="mt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {copyButton('프롬프트 복사')}
        </span>
        <div className="max-w-[80%] space-y-2">
          {message.attachments?.map((a) => (
            <div
              key={a.name}
              className="ml-auto flex w-fit items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-1.5 text-base"
            >
              <Paperclip size={13} className="text-faint" />
              <span>{a.name}</span>
              <span className="text-faint">{a.size}</span>
            </div>
          ))}
          <div className="rounded-panel rounded-br-md bg-elevated px-4 py-2.5 text-md leading-[1.7] whitespace-pre-wrap">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  const costRoute = message.routing?.costRouting
  const costRouteDisplay = costRoute ? costRoutePresentation(costRoute, models, t) : null
  const linked = (message.artifactIds ?? [])
    .map((id) => artifacts.find((a) => a.id === id))
    .filter((a) => a !== undefined)

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
          <StepTimeline steps={message.steps} live={!!streaming} />
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
          // "thinking…" under it reads as still running.
          !message.steps?.length &&
          !message.error && (
            <p className="animate-blink text-md text-faint">{t('생각하는 중…')}</p>
          )
        )}
        {streaming && message.content && (
          <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-blink bg-accent" />
        )}

        {/* Below the answer, not instead of it. A turn that failed halfway has
            two things to say — what it managed to write, and that it stopped —
            and the reader needs both to decide whether to run it again. */}
        {message.error && (
          <div
            role="status"
            className="mt-3 flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-3 py-2.5 text-base text-danger"
          >
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            <span>{message.error}</span>
          </div>
        )}

        {linked.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {linked.map((a) => {
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
            <span className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            {copyButton(t('복사'))}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('좋아요')}
              className={cn(message.liked === 'up' && 'text-success')}
              onClick={() => rateMessage(sessionId, message.id, 'up')}
            >
              <ThumbsUp size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('싫어요')}
              className={cn(message.liked === 'down' && 'text-danger')}
              onClick={() => rateMessage(sessionId, message.id, 'down')}
            >
              <ThumbsDown size={14} />
            </Button>
            </span>
            {/* Which model answered and what it cost. Kept visible rather than
                behind a hover: on a shared proxy with a monthly allowance, "what
                did that just cost me" is not a detail you go looking for. */}
            {message.usage && user?.preferences.showUsage !== false && (
              <span className="ml-1 text-xs">
                {model?.label ?? message.model} · {formatTokens(message.usage.inputTokens)} in ·{' '}
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
